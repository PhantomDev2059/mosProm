"""Признаки самого «падающего» момента каждой записи UR Fall — для подбора порогов правила падения.

Для каждой пары кадров (вертикальная поза в t0, любой кадр t1 не позже чем через window с) считаем, насколько
опустились голова и таз (в долях роста в t0), во сколько раз сжалась высота рамки и наклон корпуса в t1.
Берём пару с наибольшим опусканием таза. Печатаем таблицу «падения против обычной активности».
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.rules.fall import is_upright, torso_angle  # noqa: E402
from engine.tracking import IouTracker  # noqa: E402
from engine.types import L_HIP, R_HIP  # noqa: E402


def hip_y(d):
    a, b = d.keypoint(L_HIP), d.keypoint(R_HIP)
    if a is None or b is None:
        return None
    return float((a[1] + b[1]) / 2)


def features(path: Path, pose, window: float = 1.0, fps: float = 10.0) -> dict:
    cap = cv2.VideoCapture(str(path))
    src = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(src / fps))
    tracker, idx = IouTracker(), 0
    while cap.grab():
        idx += 1
        if idx % step:
            continue
        _, frame = cap.retrieve()
        tracker.update(pose(frame[:, frame.shape[1] // 2:]), idx / src)
    cap.release()
    best = {"hip_drop": 0.0}
    for tr in list(tracker.tracks.values()):
        hist = list(tr.history)
        for i, (t0, d0) in enumerate(hist):
            h0y = hip_y(d0)
            if not is_upright(d0) or h0y is None:
                continue
            for t1, d1 in hist[i + 1:]:
                if t1 - t0 > window:
                    break
                h1y = hip_y(d1)
                if h1y is None:
                    continue
                f = {"hip_drop": (h1y - h0y) / d0.height, "head_drop": float(d1.box[1] - d0.box[1]) / d0.height,
                     "height_ratio": d1.height / d0.height, "angle": torso_angle(d1) or 0.0, "dt": t1 - t0}
                if f["hip_drop"] > best["hip_drop"]:
                    best = f
    return {k: round(v, 3) for k, v in best.items()}


def main() -> None:
    from api.workers import load_detectors
    pose = load_detectors(Path("/workspace/linq/models/registry.json"))["pose"]
    data = Path("/workspace/linq/data/eval/urfall")
    out = {}
    for v in sorted(data.glob("*-cam0.mp4")):
        out[v.stem.replace("-cam0", "")] = features(v, pose)
        print(v.stem, out[v.stem.replace("-cam0", "")], flush=True)
    (data.parent / "fall_features.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
