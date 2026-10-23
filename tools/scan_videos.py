"""Прогон всех моделей по роликам: что и сколько находится в каждом видео. Помогает выбрать демо-камеры."""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.backends import OnnxBackend  # noqa: E402
from engine.detector import Detector, ModelSpec  # noqa: E402


def load_detectors(registry_path: Path) -> list[Detector]:
    reg = json.loads(registry_path.read_text())
    dets = []
    for name, m in reg.items():
        spec = ModelSpec(name=name, kind=m["kind"], path=m["path"],
                         class_map={int(k): v for k, v in m["class_map"].items()}, conf=0.4)
        dets.append(Detector(spec, OnnxBackend(spec.path)))
    return dets


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", default="/workspace/linq/data/videos/raw")
    ap.add_argument("--registry", default="/workspace/linq/models/registry.json")
    ap.add_argument("--samples", type=int, default=24)
    args = ap.parse_args()
    detectors = load_detectors(Path(args.registry))
    print("backends:", {d.spec.name: d.backend.name for d in detectors}, flush=True)
    report = {}
    for path in sorted(Path(args.videos).glob("*.mp4")):
        cap = cv2.VideoCapture(str(path))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        counts, maxes = Counter(), Counter()
        for i in range(args.samples):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(i * n / args.samples))
            ok, frame = cap.read()
            if not ok:
                continue
            frame_counts = Counter()
            for d in detectors:
                for det in d(frame):
                    frame_counts[det.cls] += 1
            counts.update(frame_counts)
            for k, v in frame_counts.items():
                maxes[k] = max(maxes[k], v)
        cap.release()
        report[path.name] = {"duration_s": round(n / fps, 1), "size": f"{w}x{h}", "fps": round(fps, 1),
                             "frames_with": dict(counts), "max_per_frame": dict(maxes)}
        print(path.name, report[path.name]["duration_s"], report[path.name]["size"], dict(counts), flush=True)
    stats = {d.spec.name: d.stats() for d in detectors}
    out = Path(args.videos).parent / "scan.json"
    out.write_text(json.dumps({"videos": report, "timings": stats}, ensure_ascii=False, indent=1))
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    t = time.time()
    main()
    print("done in", round(time.time() - t), "s")
