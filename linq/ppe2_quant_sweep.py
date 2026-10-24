"""Перебор настроек квантования модели касок (ppe2): после квантования Миши голова без каски почти исчезает.

Для каждого варианта — compile_tpu.py в свою папку, затем эмулятор SDK (тот же целочисленный граф) против FP32
на кадрах камер с людьми: доля найденных объектов по классам. Запуск в окружении SDK (WSL):
    python linq/ppe2_quant_sweep.py --onnx .../ppe2.onnx --videos .../videos --out .../sweep
"""
import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.geometry import iou  # noqa: E402
from engine.postprocess import decode  # noqa: E402
from linq.compile_tpu import calibration_frames  # noqa: E402

CLASS_MAP = {0: "helmet", 1: "head", 2: "vest"}
VARIANTS = {
    "p9999": ["--smart", "--percentile", "99.99"],
    "p100": ["--smart", "--percentile", "100"],
    "clscpu": ["--smart", "--cls-on-cpu", "--percentile", "99.99"],
    "plain": ["--percentile", "99.99", "--calib", "200"],
}


def emulate(onnx_path: Path, tpu: Path, frames, conf=0.45):
    import torch
    from dnn_quant.api import TpuDtype
    from tpu_framework import QuantizedModel

    full = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    tail = ort.InferenceSession(str(tpu / "ppe2_tail.onnx"), providers=["CPUExecutionProvider"])
    tail_inputs = [(i.name, i.shape) for i in tail.get_inputs()]
    qm = QuantizedModel.load(str(tpu / "ppe2.qm.onnx"))
    tg = qm.as_torch_graph(batch_size=1, cache_word_length=128)
    gm = getattr(tg, "gm", None) or tg._gm
    gm.eval()
    import builtins
    builtins.np = np          # сгенерированный SDK граф ссылается на np без импорта
    in_anchor, out_anchor = tg.get_inputs_anchors(), tg.get_outputs_anchors()
    layer_to_orig = {v: k for k, v in out_anchor.items()}
    name = full.get_inputs()[0].name
    shape = (int(frames.shape[2]), int(frames.shape[3]))
    ref_n, hit = Counter(), Counter()
    for x in frames:
        x = x[None]
        ref = decode(full.run(None, {name: x})[0], "detect", CLASS_MAP, 1.0, (0, 0), shape, conf=conf)
        feed = qm.rescale({in_anchor[name]: x}, TpuDtype.Input)
        with torch.no_grad():
            outs = gm(*[torch.as_tensor(feed[n]) for n in tg._input_layer_names])
        outs = outs if isinstance(outs, (tuple, list)) else (outs,)
        named = qm.rescale({layer_to_orig[n]: o.detach().cpu().numpy() for n, o in zip(tg._output_layer_names, outs)},
                           TpuDtype.Output)
        feed2 = {}
        for n, want in tail_inputs:
            t = np.asarray(named[n], np.float32)
            if t.ndim == 4 and want[1] != t.shape[1] and want[1] == t.shape[3]:
                t = t.transpose(0, 3, 1, 2)
            feed2[n] = np.ascontiguousarray(t)
        got = decode(tail.run(None, feed2)[0], "detect", CLASS_MAP, 1.0, (0, 0), shape, conf=conf)
        used = set()
        for r in ref:
            ref_n[r.cls] += 1
            cands = [(iou(r.box, g.box), j) for j, g in enumerate(got) if j not in used and g.cls == r.cls]
            if cands and max(cands)[0] >= 0.5:
                used.add(max(cands)[1])
                hit[r.cls] += 1
    return {c: f"{hit[c]}/{ref_n[c]} = {hit[c] / max(ref_n[c], 1):.0%}" for c in ("head", "helmet", "vest")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--videos", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    frames = calibration_frames(Path(args.videos), 80, 640, offset=0.85)     # не те кадры, что в калибровке
    for tag, extra in VARIANTS.items():
        if args.only and tag not in args.only:
            continue
        out = Path(args.out) / tag
        if not (out / "ppe2.qm.onnx").exists():
            cmd = [sys.executable, str(ROOT / "linq/compile_tpu.py"), "--onnx", args.onnx, "--name", "ppe2",
                   "--videos", args.videos, "--out", str(out)] + (extra if "--calib" in extra else extra + ["--calib", "120"])
            subprocess.run(cmd, check=False, stdout=open(Path(args.out) / f"{tag}.log", "w"), stderr=subprocess.STDOUT)
        res = emulate(Path(args.onnx), out, frames)
        print(tag, json.dumps(res, ensure_ascii=False), "tpu:", [p.name for p in out.glob("*.tpu")], flush=True)


if __name__ == "__main__":
    main()
