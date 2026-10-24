"""Прогон демо-роликов через TPU LinQ заранее: каждый кадр анализа (analysis_fps) всеми моделями камеры,
с порогами registry.json — без спешки и потерь кадров. Результат — data/dets/<камера>.jsonl;
сервис с LINQ_REPLAY=1 проигрывает ролик с этими детекциями в реальном темпе (трекер и правила — вживую).

    LINQ_BACKEND=tpu python tools/precompute_tpu.py [cam-01-site ...]
"""
import json
import os
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api.workers import load_detectors  # noqa: E402
from engine.config import load_cameras  # noqa: E402
from engine.dets_io import replay_path, to_json  # noqa: E402
from engine.pipeline import CameraPipeline  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("LINQ_DATA", "/workspace/linq/data"))
MODELS = Path(os.environ.get("LINQ_MODELS", "/workspace/linq/models"))
INFLIGHT = int(os.environ.get("PRECOMPUTE_INFLIGHT", "12"))


def main() -> None:
    detectors = load_detectors(MODELS / "registry.json", os.environ.get("LINQ_BACKEND", "tpu"))
    cams = load_cameras(os.environ.get("LINQ_CONFIGS", ROOT / "configs" / "cameras"))
    only = set(sys.argv[1:])
    pool = ThreadPoolExecutor(INFLIGHT)
    for cfg in cams:
        if only and cfg.id not in only:
            continue
        names = [n for n in (cfg.raw.get("models") or list(detectors)) if n in detectors]
        pipe = CameraPipeline(cfg, [detectors[n] for n in names], events=None)
        src = cfg.source if cfg.source.startswith(("/", "rtsp")) else str(DATA / "videos" / cfg.source)
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            print(cfg.id, "нет ролика", src, flush=True)
            continue
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        step = max(1, round(src_fps / cfg.analysis_fps))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) // step
        out = replay_path(DATA, cfg.id)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part")
        t0, n, q, idx = time.time(), 0, deque(), 0
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps({"camera": cfg.id, "source": cfg.source, "models": names, "step": step,
                                "backend": next(iter(detectors.values())).backend.name}, ensure_ascii=False) + "\n")

            def flush(item):
                nonlocal n
                i, fut = item
                f.write(json.dumps({"i": i, "d": [to_json(d) for d in fut.result()]}) + "\n")
                n += 1
                if n % 50 == 0:
                    print(f"{cfg.id}: {n}/{total} кадров, {n / (time.time() - t0):.1f} к/с", flush=True)

            while True:
                if not cap.grab():
                    break
                idx += 1
                if idx % step:
                    continue
                ok, frame = cap.retrieve()
                if not ok:
                    break
                if frame.shape[1] > 1280:                       # как в VideoWorker
                    s = 1280 / frame.shape[1]
                    frame = cv2.resize(frame, (1280, round(frame.shape[0] * s)))
                q.append((idx, pool.submit(pipe.detect, frame)))
                while len(q) >= INFLIGHT:
                    flush(q.popleft())
            while q:
                flush(q.popleft())
        cap.release()
        tmp.replace(out)
        print(f"{cfg.id}: готово {n} кадров за {time.time() - t0:.0f} с → {out}", flush=True)


if __name__ == "__main__":
    main()
