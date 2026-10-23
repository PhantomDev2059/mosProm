"""Покадровая отладка правила падения: что видит поза до, во время и после падения по разметке UR Fall."""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.rules.fall import is_horizontal, torso_angle  # noqa: E402
from eval.urfall import FPS, ground_truth  # noqa: E402


def main(names: list[str]) -> None:
    from api.workers import load_detectors
    pose = load_detectors(Path("/workspace/linq/models/registry.json"))["pose"]
    data = Path("/workspace/linq/data/eval/urfall")
    gt = {g.video: g for g in ground_truth(data / "urfall-cam0-falls.csv")}
    for name in names:
        g = gt[name]
        cap = cv2.VideoCapture(str(data / f"{name}-cam0.mp4"))
        idx = 0
        print(f"== {name}: падение {g.start:.1f}–{g.end:.1f} с")
        while cap.grab():
            idx += 1
            if idx % 3:
                continue
            _, frame = cap.retrieve()
            frame = frame[:, frame.shape[1] // 2:]
            t = idx / FPS
            dets = pose(frame)
            if not dets:
                print(f"  {t:5.1f}  нет человека")
                continue
            d = max(dets, key=lambda x: x.score)
            ang = torso_angle(d)
            mark = "*" if g.start <= t <= g.end else " "
            print(f" {mark}{t:5.1f}  conf={d.score:.2f} h={d.height:5.0f} w={d.width:5.0f} cy={d.center[1]:5.0f} "
                  f"angle={'—' if ang is None else round(ang)} horiz={is_horizontal(d)}")
        cap.release()


if __name__ == "__main__":
    main(sys.argv[1:] or ["fall-01", "fall-03", "fall-05"])
