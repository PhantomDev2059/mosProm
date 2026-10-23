"""Подбор параметров правила падения на половине UR Fall и честная проверка на другой половине.

1. Один раз прогоняем модель позы по всем записям и кэшируем детекции (pose_cache.pkl).
2. Перебираем сетку параметров правила на нечётных записях (tune), выбираем лучший по F1 при условии
   «не больше одной ложной тревоги на 10 записей обычной активности».
3. Лучшие параметры проверяем на чётных записях (test) — это число и показываем.

    python eval/sweep_fall.py
"""
import itertools
import json
import pickle
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.config import parse_camera  # noqa: E402
from engine.events import EventManager  # noqa: E402
from engine.pipeline import CameraPipeline  # noqa: E402
from eval.metrics import Interval, evaluate  # noqa: E402
from eval.urfall import FPS, ground_truth  # noqa: E402

DATA = Path("/workspace/linq/data/eval/urfall")
CACHE = DATA.parent / "pose_cache.pkl"
ANALYSIS_FPS = 10.0


def build_cache() -> dict:
    if CACHE.exists():
        return pickle.loads(CACHE.read_bytes())
    from api.workers import load_detectors
    pose = load_detectors(Path("/workspace/linq/models/registry.json"))["pose"]
    cache = {}
    for v in sorted(DATA.glob("*-cam0.mp4")):
        cap = cv2.VideoCapture(str(v))
        src = cap.get(cv2.CAP_PROP_FPS) or FPS
        step, idx, frames = max(1, round(src / ANALYSIS_FPS)), 0, []
        while cap.grab():
            idx += 1
            if idx % step:
                continue
            _, frame = cap.retrieve()
            frames.append((idx / src, pose(frame[:, frame.shape[1] // 2:])))
        cap.release()
        cache[v.stem.replace("-cam0", "")] = {"frames": frames, "seconds": idx / src}
    CACHE.write_bytes(pickle.dumps(cache))
    return cache


def run(cache: dict, names: list[str], params: dict, confirm_s: float) -> list[Interval]:
    pred = []
    for name in names:
        cfg = parse_camera({"camera": name, "source": "-", "analysis_fps": ANALYSIS_FPS,
                            "rules": [{"type": "fall", "confirm_s": confirm_s, **params}]})
        opened = []
        mgr = EventManager(name, on_open=lambda e, d: opened.append(e))
        pipe = CameraPipeline(cfg, [], mgr)
        for t, dets in cache[name]["frames"]:
            pipe.step(dets, t)
        mgr.tick(1e9, lambda tid: 1e9)
        pred += [Interval(name, e.type, e.confirmed_at, e.closed_at or e.last_signal) for e in opened]
    return pred


def score(cache, names, gt_all, params, confirm_s) -> dict:
    gt = [g for g in gt_all if g.video in names]
    pred = run(cache, names, params, confirm_s)
    hours = sum(cache[n]["seconds"] for n in names) / 3600
    r = evaluate(gt, pred, hours)
    fp = r["per_type"].get("fall", {}).get("fp", 0)
    return {"params": params, "confirm_s": confirm_s, "f1": r["micro"]["f1"] or 0.0, "recall": r["micro"]["recall"],
            "precision": r["micro"]["precision"], "fp": fp, "report": r}


def main() -> None:
    cache = build_cache()
    gt_all = ground_truth(DATA / "urfall-cam0-falls.csv")
    names = sorted(cache)
    tune = [n for n in names if int(n.split("-")[1]) % 2 == 1]
    test = [n for n in names if int(n.split("-")[1]) % 2 == 0]
    n_adl_tune = sum(n.startswith("adl") for n in tune)
    grid = {
        "drop_window_s": [0.8, 1.0, 1.3],
        "head_drop": [0.25, 0.3, 0.35, 0.45],
        "shrink": [0.65, 0.75, 0.9, 1.5],
        "hold_s": [0.0, 0.3, 0.6],
    }
    results = []
    for values in itertools.product(*grid.values()):
        params = dict(zip(grid.keys(), values))
        for confirm in (0.0, 0.3):
            results.append(score(cache, tune, gt_all, params, confirm))
    ok = [r for r in results if r["fp"] <= max(1, n_adl_tune // 10)]
    best = max(ok or results, key=lambda r: (r["f1"], r["recall"]))
    test_r = score(cache, test, gt_all, best["params"], best["confirm_s"])
    all_r = score(cache, names, gt_all, best["params"], best["confirm_s"])
    summary = {"best_params": best["params"], "confirm_s": best["confirm_s"],
               "tune": {k: best[k] for k in ("f1", "recall", "precision", "fp")},
               "test": {k: test_r[k] for k in ("f1", "recall", "precision", "fp")},
               "test_report": test_r["report"], "all_report": all_r["report"], "grid_size": len(results)}
    (DATA.parent / "fall_sweep.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps({k: summary[k] for k in ("best_params", "confirm_s", "tune", "test", "grid_size")}, ensure_ascii=False))
    top = sorted(results, key=lambda r: -r["f1"])[:8]
    for r in top:
        print(round(r["f1"], 3), r["recall"], r["precision"], r["fp"], r["params"], r["confirm_s"])


if __name__ == "__main__":
    main()
