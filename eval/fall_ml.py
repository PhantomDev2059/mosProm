"""Обучение классификатора падения на позе (UR Fall) и честная проверка на отложенной половине.

1. Кэш позы из eval/sweep_fall.py → трекинг → признаки engine/rules/fall_ml.features на каждом кадре каждого трека.
2. Метки: кадры от начала падения до +2,5 с — «падение»; записи обычной активности и кадры до падения — «нет».
3. Обучение на нечётных записях, порог вероятности подбирается по F1 событий на них же (через тот же конвейер).
4. Итоговые цифры — на чётных записях, которых модель не видела.

    python eval/fall_ml.py
"""
import json
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.rules.fall_ml import FEATURES, features  # noqa: E402
from engine.tracking import IouTracker  # noqa: E402
from eval.sweep_fall import DATA, build_cache, score  # noqa: E402
from eval.urfall import ground_truth  # noqa: E402

MODEL = Path("/workspace/linq/models/fall_clf.pkl")


def samples(cache: dict, names: list[str], gt: dict) -> tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    for name in names:
        g = gt.get(name)
        tracker = IouTracker()
        for t, dets in cache[name]["frames"]:
            for tr in tracker.update(dets, t):
                if tr.cls != "person":
                    continue
                f = features(list(tr.history), t)
                if f is None:
                    continue
                if g is not None and g.start <= t <= g.start + 2.5:
                    label = 1
                elif g is None or t < g.start - 0.5:
                    label = 0
                else:
                    continue                                   # лежит долго после падения — не учим ни туда, ни сюда
                X.append(f)
                y.append(label)
    return np.asarray(X, np.float32), np.asarray(y)


def main() -> None:
    from sklearn.ensemble import HistGradientBoostingClassifier

    cache = build_cache()
    gt_list = ground_truth(DATA / "urfall-cam0-falls.csv")
    gt = {g.video: g for g in gt_list}
    names = sorted(cache)
    tune = [n for n in names if int(n.split("-")[1]) % 2 == 1]
    test = [n for n in names if int(n.split("-")[1]) % 2 == 0]
    X, y = samples(cache, tune, gt)
    print(f"обучение: {len(y)} кадров, из них падение {int(y.sum())}", flush=True)
    clf = HistGradientBoostingClassifier(max_depth=3, max_iter=200, learning_rate=0.05, l2_regularization=1.0,
                                         class_weight="balanced", random_state=0)
    clf.fit(X, y)

    best = None
    for thr in (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95):
        MODEL.write_bytes(pickle.dumps({"model": clf, "threshold": thr, "features": FEATURES}))
        from engine.rules.fall_ml import _load
        _load.cache_clear()
        for confirm in (0.0, 0.3):
            r = score_ml(cache, tune, gt_list, thr, confirm)
            print("thr", thr, "confirm", confirm, "tune f1", r["f1"], "rec", r["recall"], "prec", r["precision"], "fp", r["fp"], flush=True)
            if best is None or (r["f1"], -r["fp"]) > (best[2]["f1"], -best[2]["fp"]):
                best = (thr, confirm, r)
    thr, confirm, tune_r = best
    MODEL.write_bytes(pickle.dumps({"model": clf, "threshold": thr, "features": FEATURES, "confirm_s": confirm}))
    from engine.rules.fall_ml import _load
    _load.cache_clear()
    test_r = score_ml(cache, test, gt_list, thr, confirm)
    all_r = score_ml(cache, names, gt_list, thr, confirm)
    out = {"method": "ml", "threshold": thr, "confirm_s": confirm,
           "tune": {k: tune_r[k] for k in ("f1", "recall", "precision", "fp")},
           "test": {k: test_r[k] for k in ("f1", "recall", "precision", "fp")},
           "test_report": test_r["report"], "all_report": all_r["report"], "features": FEATURES,
           "train_frames": int(len(y)), "train_pos": int(y.sum())}
    (DATA.parent / "fall_ml.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps({k: out[k] for k in ("threshold", "confirm_s", "tune", "test")}, ensure_ascii=False))


def score_ml(cache, names, gt_list, thr, confirm):
    import eval.sweep_fall as sw
    orig = sw.run

    def run_ml(cache, names, params, confirm_s):
        from engine.config import parse_camera
        from engine.events import EventManager
        from engine.pipeline import CameraPipeline
        from eval.metrics import Interval
        pred = []
        for name in names:
            cfg = parse_camera({"camera": name, "source": "-", "analysis_fps": sw.ANALYSIS_FPS,
                                "rules": [{"type": "fall_ml", "confirm_s": confirm_s, "severity": "critical",
                                           "model": str(MODEL), "threshold": thr}]})
            opened = []
            mgr = EventManager(name, on_open=lambda e, d: opened.append(e))
            pipe = CameraPipeline(cfg, [], mgr)
            for t, dets in cache[name]["frames"]:
                pipe.step(dets, t)
            mgr.tick(1e9, lambda tid: 1e9)
            pred += [Interval(name, "fall", e.confirmed_at, e.closed_at or e.last_signal) for e in opened]
        return pred

    sw.run = run_ml
    try:
        return score(cache, names, gt_list, {}, confirm)
    finally:
        sw.run = orig


if __name__ == "__main__":
    main()
