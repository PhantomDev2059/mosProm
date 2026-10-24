"""Детекции прогона на TPU стенда против эталона FP32 (ONNX Миши, его decode) на тех же кадрах роликов.

    python eval/compare_tpu_fp32.py --dets D:/dev/mosprom/release/dets --models D:/dev/mosprom/release/x/models \
        --videos D:/dev/mosprom/release/x/videos --every 3
Совпадение — тот же класс и IoU >= 0.5. «Нашёл» = доля эталонных объектов, найденных на TPU; «лишние» = доля
детекций TPU без пары в эталоне.
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.backends import OnnxBackend  # noqa: E402
from engine.config import load_cameras  # noqa: E402
from engine.detector import Detector, ModelSpec  # noqa: E402
from engine.dets_io import from_json  # noqa: E402
from engine.geometry import iou  # noqa: E402
from engine.pipeline import CameraPipeline  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dets", required=True)
    ap.add_argument("--models", required=True)
    ap.add_argument("--videos", required=True)
    ap.add_argument("--every", type=int, default=3, help="каждый N-й кадр прогона")
    args = ap.parse_args()
    reg = json.loads((Path(args.models) / "registry.json").read_text(encoding="utf-8"))
    dets = {}
    for name, m in reg.items():
        spec = ModelSpec(name=name, kind=m["kind"], path=str(Path(args.models) / "onnx" / f"{name}.onnx"),
                         conf=m.get("conf", 0.4), class_map={int(k): v for k, v in m["class_map"].items()})
        dets[name] = Detector(spec, OnnxBackend(spec.path, prefer_gpu=False))
    stats = defaultdict(lambda: [0, 0, 0, 0, []])      # класс → [эталон, найдено, tpu всего, tpu лишних, Δscore]
    root = Path(__file__).resolve().parents[1]
    for cfg in load_cameras(root / "configs" / "cameras"):
        f = Path(args.dets) / f"{cfg.id}.jsonl"
        if not f.exists():
            continue
        lines = f.read_text(encoding="utf-8").splitlines()
        head, rows = json.loads(lines[0]), [json.loads(x) for x in lines[1:]][::args.every]
        pipe = CameraPipeline(cfg, [dets[n] for n in head["models"]], events=None)
        cap = cv2.VideoCapture(str(Path(args.videos) / cfg.source))
        want = {r["i"]: r["d"] for r in rows}
        idx = 0
        while want:
            if not cap.grab():
                break
            idx += 1
            if idx not in want:
                continue
            _, frame = cap.retrieve()
            if frame.shape[1] > 1280:
                s = 1280 / frame.shape[1]
                frame = cv2.resize(frame, (1280, round(frame.shape[0] * s)))
            ref = pipe.detect(frame)
            tpu = [from_json(o) for o in want.pop(idx)]
            used = set()
            for r in ref:
                st = stats[r.cls]
                st[0] += 1
                best, bj = 0.0, None
                for j, t in enumerate(tpu):
                    if j not in used and t.cls == r.cls:
                        v = iou(r.box, t.box)
                        if v > best:
                            best, bj = v, j
                if best >= 0.5:
                    used.add(bj)
                    st[1] += 1
                    st[4].append(tpu[bj].score - r.score)
            for j, t in enumerate(tpu):
                stats[t.cls][2] += 1
                if j not in used:
                    stats[t.cls][3] += 1
        print(f"{cfg.id}: сравнено кадров {len(rows) - len(want)}", flush=True)
    print(f"\n{'класс':12s} {'эталон FP32':>11s} {'нашёл TPU':>10s} {'TPU всего':>10s} {'лишние':>7s} {'Δуверенн.':>10s}")
    for c, (n_ref, hit, n_tpu, extra, ds) in sorted(stats.items(), key=lambda x: -x[1][0]):
        print(f"{c:12s} {n_ref:11d} {hit / max(n_ref, 1):10.0%} {n_tpu:10d} {extra / max(n_tpu, 1):7.0%} "
              f"{(np.mean(ds) if ds else 0):+10.3f}")


if __name__ == "__main__":
    main()
