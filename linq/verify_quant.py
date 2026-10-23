"""Насколько квантованная под LinQ модель расходится с исходной FP32 — без доступа к железу.

Квантованная часть считается эмулятором SDK (QuantizedModel.as_torch_graph — тот же целочисленный граф,
что уходит в компилятор), хвост — ONNX на CPU, постпроцессинг — наш общий. Сравниваем детекции
с исходной моделью на кадрах наших камер: доля найденных тех же объектов, лишние, IoU, сдвиг точек скелета.

    python linq/verify_quant.py --name pose --kind pose
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.geometry import iou  # noqa: E402
from engine.postprocess import decode  # noqa: E402
from linq.compile_tpu import calibration_frames  # noqa: E402


def match(ref, got, thr=0.5):
    used, pairs = set(), []
    for r in ref:
        best, best_v = None, thr
        for j, g in enumerate(got):
            if j in used or g.cls != r.cls:
                continue
            v = iou(r.box, g.box)
            if v >= best_v:
                best, best_v = j, v
        if best is not None:
            used.add(best)
            pairs.append((r, got[best], best_v))
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--kind", default="detect")
    ap.add_argument("--models", default="/workspace/linq/models")
    ap.add_argument("--videos", default="/workspace/linq/data/videos")
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--tpu-dir", default=None, help="папка с результатами compile_tpu (по умолчанию models/tpu)")
    args = ap.parse_args()

    import torch
    from dnn_quant.api import TpuDtype
    from tpu_framework import QuantizedModel

    models = Path(args.models)
    reg = json.loads((models / "registry.json").read_text())[args.name]
    class_map = {int(k): v for k, v in reg["class_map"].items()}
    conf = reg.get("conf", 0.4)
    tpu = Path(args.tpu_dir) if args.tpu_dir else models / "tpu"

    full = ort.InferenceSession(reg["path"], providers=["CPUExecutionProvider"])
    tail = ort.InferenceSession(str(tpu / f"{args.name}_tail.onnx"), providers=["CPUExecutionProvider"])
    tail_inputs = [(i.name, i.shape) for i in tail.get_inputs()]
    qm = QuantizedModel.load(str(tpu / f"{args.name}.qm.onnx"))
    tg = qm.as_torch_graph(batch_size=1, cache_word_length=128)
    gm = getattr(tg, "gm", None) or tg._gm
    gm.eval()
    in_anchor = tg.get_inputs_anchors()
    out_anchor = tg.get_outputs_anchors()
    layer_to_orig = {v: k for k, v in out_anchor.items()}
    input_name = full.get_inputs()[0].name

    frames = calibration_frames(Path(args.videos), args.frames, 640, offset=0.85)   # не те кадры, что в калибровке
    shape = (int(frames.shape[2]), int(frames.shape[3]))
    tp = fp = fn = 0
    ious, kpt_err = [], []
    for x in frames:
        x = x[None]
        ref = decode(full.run(None, {input_name: x})[0], args.kind, class_map, 1.0, (0, 0), shape, conf=conf)

        feed = qm.rescale({in_anchor[input_name]: x}, TpuDtype.Input)
        with torch.no_grad():
            outs = gm(*[torch.as_tensor(feed[n]) for n in tg._input_layer_names])
        if not isinstance(outs, (tuple, list)):
            outs = (outs,)
        named = {layer_to_orig[n]: o.detach().cpu().numpy() for n, o in zip(tg._output_layer_names, outs)}
        named = qm.rescale(named, TpuDtype.Output)
        tail_feed = {}
        for name, want in tail_inputs:
            t = np.asarray(named[name], np.float32)
            if t.ndim == 4 and want[1] != t.shape[1] and want[1] == t.shape[3]:   # NHWC → NCHW
                t = t.transpose(0, 3, 1, 2)
            tail_feed[name] = np.ascontiguousarray(t)
        got = decode(tail.run(None, tail_feed)[0], args.kind, class_map, 1.0, (0, 0), shape, conf=conf)

        pairs = match(ref, got)
        tp += len(pairs)
        fn += len(ref) - len(pairs)
        fp += len(got) - len(pairs)
        for r, g, v in pairs:
            ious.append(v)
            if r.keypoints is not None and g.keypoints is not None:
                vis = (r.keypoints[:, 2] > 0.5) & (g.keypoints[:, 2] > 0.5)
                if vis.any():
                    d = np.linalg.norm(r.keypoints[vis, :2] - g.keypoints[vis, :2], axis=1) / max(r.height, 1)
                    kpt_err.append(float(d.mean()))

    report = {
        "name": args.name, "frames": len(frames), "fp32_objects": tp + fn,
        "recall_vs_fp32": round(tp / (tp + fn), 3) if tp + fn else None,
        "precision_vs_fp32": round(tp / (tp + fp), 3) if tp + fp else None,
        "mean_iou": round(float(np.mean(ious)), 3) if ious else None,
        "keypoint_err_heights": round(float(np.mean(kpt_err)), 4) if kpt_err else None,
    }
    print(json.dumps(report, ensure_ascii=False))
    rep_path = tpu / f"{args.name}_report.json"
    if rep_path.exists():
        data = json.loads(rep_path.read_text())
        data["quant_check"] = report
        rep_path.write_text(json.dumps(data, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
