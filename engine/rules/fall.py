"""Падение и неподвижное лежание по скелету YOLOv8-pose."""
import math

import numpy as np

from ..types import L_HIP, L_SHOULDER, R_HIP, R_SHOULDER, Detection, Track
from .base import RuleContext, Signal, register


def torso_angle(det: Detection) -> float | None:
    """Угол корпуса (середина плеч → середина бёдер) от вертикали, градусы."""
    pts = [det.keypoint(i) for i in (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP)]
    if any(p is None for p in pts):
        return None
    sh = (pts[0] + pts[1]) / 2
    hip = (pts[2] + pts[3]) / 2
    dx, dy = hip - sh
    return math.degrees(math.atan2(abs(dx), abs(dy) + 1e-6))


def is_horizontal(det: Detection, min_angle: float = 60.0) -> bool:
    angle = torso_angle(det)
    if angle is not None:
        return angle >= min_angle
    return det.width > det.height * 1.2


def standing_height(track: Track) -> float:
    """Типичный рост человека в треке: верхний квартиль высот рамки."""
    return float(np.percentile([d.height for _, d in track.history], 75))


@register("fall", "Падение человека")
class Fall:
    def __init__(self, drop_ratio: float = 0.35, drop_window_s: float = 1.5, memory_s: float = 6.0):
        self.drop_ratio = drop_ratio
        self.drop_window_s = drop_window_s
        self.memory_s = memory_s

    def _dropped_recently(self, track: Track, now: float) -> bool:
        hist = track.window(now, self.memory_s)
        if len(hist) < 3:
            return False
        ref = standing_height(track)
        # ищем резкое опускание центра рамки за короткое окно
        for i, (t0, d0) in enumerate(hist):
            for t1, d1 in hist[i + 1:]:
                if t1 - t0 > self.drop_window_s:
                    break
                if d1.center[1] - d0.center[1] > self.drop_ratio * ref and not is_horizontal(d0):
                    return True
        return False

    def evaluate(self, track: Track, ctx: RuleContext) -> Signal | None:
        det = track.det
        if is_horizontal(det) and self._dropped_recently(track, ctx.now):
            return Signal(0.9, {"torso_angle": round(torso_angle(det) or 0.0)})
        return None


@register("immobile", "Неподвижное лежание")
class Immobile:
    def __init__(self, still_s: float = 10.0, max_shift: float = 0.06):
        self.still_s = still_s
        self.max_shift = max_shift

    def evaluate(self, track: Track, ctx: RuleContext) -> Signal | None:
        det = track.det
        if not is_horizontal(det):
            return None
        hist = track.window(ctx.now, self.still_s)
        if not hist or ctx.now - hist[0][0] < self.still_s * 0.9:
            return None
        if not all(is_horizontal(d) for _, d in hist[-5:]):
            return None
        centers = np.array([d.center for _, d in hist])
        size = max(det.width, det.height)
        shift = float(np.max(np.linalg.norm(centers - centers.mean(0), axis=1))) / size
        if shift <= self.max_shift:
            return Signal(1.0, {"still_s": round(ctx.now - hist[0][0])})
        return None
