"""Оценка правила «падение» на открытом наборе UR Fall Detection (30 падений, 40 записей обычной активности).

Разметка падения — из покадровых меток авторов набора (urfall-cam0-falls.csv): 0 — человек падает, 1 — лежит.
Интервал нарушения — от первого кадра падения до последнего кадра лежания. В записях обычной активности
падений нет: любое событие «падение» там ложное.

    python eval/urfall.py --data /workspace/linq/data/eval/urfall --out /workspace/linq/data/eval/urfall_report.json
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.config import parse_camera  # noqa: E402
from engine.events import EventManager  # noqa: E402
from engine.pipeline import CameraPipeline  # noqa: E402
from eval.metrics import Interval, evaluate  # noqa: E402

FPS = 30.0


def ground_truth(labels_csv: Path) -> list[Interval]:
    frames = defaultdict(list)
    with labels_csv.open() as f:
        for row in csv.reader(f):
            frames[row[0]].append((int(row[1]), int(row[2])))
    gt = []
    for seq, items in frames.items():
        fall = [fr for fr, lab in items if lab in (0, 1)]
        if fall:
            gt.append(Interval(seq, "fall", (min(fall) - 1) / FPS, max(fall) / FPS, "critical"))
    return gt


def run_video(path: Path, detectors, analysis_fps: float, still_s: float) -> list[Interval]:
    cfg = parse_camera({"camera": path.stem, "source": str(path), "analysis_fps": analysis_fps,
                        "rules": [{"type": "fall"}, {"type": "immobile", "still_s": still_s}]})
    pred = []
    mgr = EventManager(cfg.id, on_open=lambda e, d: pred.append(e))
    pipe = CameraPipeline(cfg, detectors, mgr)
    cap = cv2.VideoCapture(str(path))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or FPS
    step = max(1, round(src_fps / analysis_fps))
    idx = 0
    while cap.grab():
        idx += 1
        if idx % step:
            continue
        ok, frame = cap.retrieve()
        if not ok:
            break
        frame = frame[:, frame.shape[1] // 2:]                      # правая половина — RGB, левая — карта глубины
        pipe.process(frame, idx / src_fps)
    cap.release()
    mgr.tick(1e9, lambda tid: 1e9)
    return [Interval(path.stem.replace("-cam0", ""), e.type, e.confirmed_at, e.closed_at or e.last_signal, e.severity,
                     e.score) for e in pred]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/workspace/linq/data/eval/urfall")
    ap.add_argument("--registry", default="/workspace/linq/models/registry.json")
    ap.add_argument("--out", default="/workspace/linq/data/eval/urfall_report.json")
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--still", type=float, default=5.0)
    args = ap.parse_args()

    from api.workers import load_detectors
    detectors = [load_detectors(Path(args.registry))["pose"]]
    data = Path(args.data)
    gt = ground_truth(data / "urfall-cam0-falls.csv")
    videos = sorted(data.glob("*-cam0.mp4"))
    pred, seconds = [], 0.0
    for v in videos:
        cap = cv2.VideoCapture(str(v))
        seconds += (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / (cap.get(cv2.CAP_PROP_FPS) or FPS)
        cap.release()
        got = run_video(v, detectors, args.fps, args.still)
        pred += got
        print(v.name, [f"{p.type}@{p.start:.1f}" for p in got], flush=True)
    report = evaluate(gt, pred, seconds / 3600, same_incident={"immobile": "fall"})
    report.update(videos=len(videos), falls=len(gt), seconds=round(seconds, 1), analysis_fps=args.fps)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
