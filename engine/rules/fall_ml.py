"""Падение по обученному классификатору поверх позы.

Признаки считаются по истории трека за последние 2 секунды — одинаково при обучении (eval/fall_ml.py) и в работе.
Модель — градиентный бустинг sklearn, обученный на нечётных записях UR Fall; путь к модели — параметр правила.
"""
import math
import pickle
from functools import lru_cache

import numpy as np

from ..types import Track
from .base import RuleContext, Signal, register
from .fall import is_upright, lying_like, torso_angle

FEATURES = ["head_drop", "shrink", "angle_now", "angle_max", "aspect_now", "cy_drop", "lying_share", "conf",
            "upright_recent", "head_speed"]


def features(hist: list, now: float) -> list[float] | None:
    win = [(t, d) for t, d in hist if now - t <= 2.0]
    if len(win) < 3:
        return None
    last = [(t, d) for t, d in win if now - t <= 0.5]
    det = win[-1][1]
    ups = [d.height for t, d in win if is_upright(d)]
    h_ref = max(ups) if ups else max(d.height for _, d in win)
    head_drop, head_speed = 0.0, 0.0
    for i, (t0, d0) in enumerate(win):
        for t1, d1 in win[i + 1:]:
            if t1 - t0 > 1.0:
                break
            drop = float(d1.box[1] - d0.box[1]) / max(d0.height, 1.0)
            if drop > head_drop:
                head_drop, head_speed = drop, drop / max(t1 - t0, 0.05)
    angles = [torso_angle(d) for _, d in last]
    angles = [a for a in angles if a is not None]
    a_now = torso_angle(det)
    return [
        head_drop,
        det.height / max(h_ref, 1.0),
        -1.0 if a_now is None else a_now,
        max(angles) if angles else -1.0,
        det.width / max(det.height, 1.0),
        (det.center[1] - min(d.center[1] for _, d in win)) / max(h_ref, 1.0),
        sum(lying_like(d) for _, d in last) / len(last),
        det.score,
        float(any(is_upright(d) for _, d in last)),
        head_speed,
    ]


@lru_cache(maxsize=4)
def _load(path: str):
    with open(path, "rb") as f:
        return pickle.load(f)


@register("fall_ml", "Падение человека")
class FallML:
    def __init__(self, model: str = "/workspace/linq/models/fall_clf.pkl", threshold: float | None = None):
        bundle = _load(model)
        self.clf = bundle["model"]
        self.threshold = threshold if threshold is not None else bundle["threshold"]

    def evaluate(self, track: Track, ctx: RuleContext) -> Signal | None:
        f = features(list(track.history), ctx.now)
        if f is None or any(math.isnan(v) for v in f):
            return None
        p = float(self.clf.predict_proba(np.asarray([f]))[0, 1])
        if p >= self.threshold:
            return Signal(p, {"probability": round(p, 2)})
        return None
