"""ONNX (Ultralytics YOLO) → программа .tpu для LinQ H1M + хвост ONNX для CPU.

Срез: на TPU идёт вся свёрточная часть до последних свёрток головы (cv2/cv3/cv4 каждого масштаба),
всё после — reshape, DFL, sigmoid, декодирование точек — в хвост, который выполняет CPU тем же
постпроцессингом, что и на GPU. Калибровка квантования — на кадрах наших камер.

Запуск в окружении SDK (Linux x86-64, Python 3.12.11):
    python linq/compile_tpu.py --onnx /workspace/linq/models/onnx/pose.onnx --name pose \
        --videos /workspace/linq/data/videos --out /workspace/linq/models/tpu
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import onnx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.postprocess import letterbox  # noqa: E402

HEAD_CONV = re.compile(r"^/model\.\d+/cv[234]\.\d+/cv[234]\.\d+\.2/Conv$")


def head_outputs(model: onnx.ModelProto, cls_on_cpu: bool = False) -> list[str]:
    """Выходы последних свёрток головы. cls_on_cpu — у ветки классов (cv3) срез на входе последней свёртки 1×1:
    логиты классов сильно смещены в минус, в int8 у порога уверенности теряется точность, а свёртка 1×1
    на CPU почти ничего не стоит."""
    outs = [n.input[0] if cls_on_cpu and "/cv3." in n.name else n.output[0]
            for n in model.graph.node if n.op_type == "Conv" and HEAD_CONV.match(n.name)]
    if not outs:
        raise SystemExit("не нашёл последние свёртки головы YOLO; укажите --cut вручную (имена из Netron)")
    return outs


def calibration_frames(videos: Path, count: int, size: int, offset: float = 0.5) -> np.ndarray:
    """Равномерная выборка кадров из роликов камер. offset сдвигает сетку: у калибровки и проверки кадры разные."""
    files = sorted(videos.glob("cam_*.mp4")) or sorted(videos.glob("*.mp4"))
    per_file = max(1, -(-count // len(files)))
    tensors = []
    for f in files:
        cap = cv2.VideoCapture(str(f))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        for i in range(per_file):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int((i + offset) * n / per_file))
            ok, frame = cap.read()
            if ok:
                tensors.append(letterbox(frame, size)[0])
        cap.release()
    return np.concatenate(tensors[:count], 0)


def smart_frames(onnx_path: str, videos: Path, count: int, size: int, conf: float = 0.25) -> np.ndarray:
    """Половина кадров — где исходная модель уверенно видит свои объекты, половина — обычные.

    Узкие детекторы (сигареты, огонь) иначе калибруются почти на пустых кадрах и после квантования теряют
    именно те активации, ради которых существуют.
    """
    import onnxruntime as ort

    pool = calibration_frames(videos, count * 4, size, offset=0.3)
    sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    peak = []
    for x in pool:
        out = sess.run(None, {name: x[None]})[0][0]          # (C, N)
        peak.append(float(out[4:].max()))                     # лучший балл класса (для pose — уверенность человека)
    order = np.argsort(peak)[::-1]
    rich = [i for i in order if peak[i] >= conf][: count // 2]
    rest = [i for i in range(len(pool)) if i not in set(rich)]
    rng = np.random.default_rng(0)
    plain = list(rng.choice(rest, size=min(count - len(rich), len(rest)), replace=False))
    return pool[sorted(rich + plain)]


def image_frames(folder: Path, count: int, size: int) -> np.ndarray:
    """Случайные изображения обучающего набора — та область данных, на которой модель училась."""
    files = sorted(folder.glob("*.jpg")) + sorted(folder.glob("*.png"))
    pick = np.random.default_rng(1).choice(len(files), size=min(count, len(files)), replace=False)
    return np.concatenate([letterbox(cv2.imread(str(files[i])), size)[0] for i in sorted(pick)], 0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--videos", default="/workspace/linq/data/videos")
    ap.add_argument("--out", default="/workspace/linq/models/tpu")
    ap.add_argument("--calib", type=int, default=64)
    ap.add_argument("--preset", choices=["o5", "o1"], default="o5")
    ap.add_argument("--cut", nargs="*", help="имена тензоров среза вместо автоматического поиска")
    ap.add_argument("--smart", action="store_true", help="калибровка с упором на кадры, где модель видит свои объекты")
    ap.add_argument("--finetune", type=int, default=0, help="эпох fine_tune_torch для порогов квантования")
    ap.add_argument("--images", help="папка обучающих изображений: половина калибровки берётся из неё")
    ap.add_argument("--percentile", type=float, default=99.9, help="перцентиль активаций для порогов")
    ap.add_argument("--cls-on-cpu", action="store_true", help="последняя свёртка ветки классов — в хвосте на CPU")
    args = ap.parse_args()

    from tpu_framework import O1, O5, TPU_H1M, Network, QuantizedModel, RegularModel, TpuProgram, compiler
    from tpu_framework import get_sub_model, preliminary_model_conditioning

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {"name": args.name, "source": args.onnx}
    t0 = time.time()

    model = onnx.load(args.onnx)
    input_name = model.graph.input[0].name
    output_name = model.graph.output[0].name
    cut = args.cut or head_outputs(model, args.cls_on_cpu)
    report["cls_on_cpu"] = args.cls_on_cpu
    head = get_sub_model(model=model, ordered_input_names=[input_name], ordered_output_names=cut)
    tail = get_sub_model(model=model, ordered_input_names=cut, ordered_output_names=[output_name])
    onnx.save(tail, out / f"{args.name}_tail.onnx")
    report.update(cut=cut, head_nodes=len(head.graph.node), tail_nodes=len(tail.graph.node))
    print(f"[1/4] срез: {len(head.graph.node)} узлов на TPU, {len(tail.graph.node)} в хвосте CPU", flush=True)

    head = preliminary_model_conditioning(head)
    size = head.graph.input[0].type.tensor_type.shape.dim[2].dim_value
    n_cam = args.calib // 2 if args.images else args.calib
    calib = (smart_frames(args.onnx, Path(args.videos), n_cam, size) if args.smart
             else calibration_frames(Path(args.videos), n_cam, size))
    if args.images:
        calib = np.concatenate([calib, image_frames(Path(args.images), args.calib - n_cam, size)], 0)
        report["calib_images"] = args.calib - n_cam
    print(f"[2/4] калибровка на {len(calib)} кадрах{' (smart)' if args.smart else ''}"
          f"{f', из них {args.calib - n_cam} из обучающего набора' if args.images else ''}", flush=True)
    regular = RegularModel(model=head)
    thresholds = regular.calibrate_torch(calibration_data={input_name: calib}, samplewise_percentile=args.percentile)
    report["percentile"] = args.percentile
    if args.finetune:
        t_ft = time.time()
        thresholds = regular.fine_tune_torch(thresholds=thresholds, fine_tuning_data={input_name: calib},
                                             fine_tuning_epochs=args.finetune)
        report["finetune_epochs"] = args.finetune
        report["finetune_s"] = round(time.time() - t_ft, 1)
        print(f"   fine_tune_torch: {args.finetune} эпох за {report['finetune_s']} с", flush=True)
    report["smart_calib"] = bool(args.smart)
    RegularModel.dump_thresholds(str(out / f"{args.name}_thresholds.json"), thresholds)
    qm_name = f"{args.name}.qm.onnx"
    regular.quantize(thresholds).save(file_dir=str(out), file_name=qm_name, io_params_name=f"{args.name}_scales.json")
    report["calib_frames"] = len(calib)
    print(f"[3/4] квантование готово за {time.time() - t0:.0f} с", flush=True)

    network, _ = Network.from_quantized_model(QuantizedModel.load(str(out / qm_name)))
    presets = [("o5", O5), ("o1", O1)] if args.preset == "o5" else [("o1", O1)]
    for tag, preset in presets:
        t1 = time.time()
        try:
            executable, descriptions = compiler.compile_(hardware_parameters=TPU_H1M, network=network, parameters=preset)
        except Exception as e:                       # O5 иногда не собирается — откат на O1, как советует SDK
            print(f"   пресет {tag} не собрался: {e}", flush=True)
            report[f"error_{tag}"] = str(e)[:300]
            continue
        path = out / f"{args.name}_b1_{tag}_h1m.tpu"
        TpuProgram.from_executable(executable, descriptions).to_file(str(path))
        report.update(program=str(path), preset=tag, compile_s=round(time.time() - t1, 1),
                      program_mb=round(path.stat().st_size / 1e6, 2))
        print(f"[4/4] {path.name}: {report['program_mb']} МБ, компиляция {report['compile_s']} с", flush=True)
        break
    report["total_s"] = round(time.time() - t0, 1)
    (out / f"{args.name}_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
