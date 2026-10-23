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


def is_upright(det: Detection) -> bool:
    angle = torso_angle(det)
    if angle is not None:
        return angle < 35 and det.height > det.width * 1.1
    return det.height > det.width * 1.3


def lying_like(det: Detection) -> bool:
    """Лежит или почти лежит. Если корпус виден — только по его наклону: сидящий человек даёт широкую рамку,
    но вертикальный корпус. Без точек скелета — по форме рамки."""
    angle = torso_angle(det)
    if angle is not None:
        return angle >= 50
    return det.width > det.height * 1.05


@register("fall", "Падение человека")
class Fall:
    """Падение — резкое опускание, после которого человек остаётся внизу.

    1. Резкость: из вертикальной позы за drop_window_s голова (верх рамки) опускается хотя бы на head_drop
       роста, а высота рамки сжимается до shrink от исходной. Медленно сесть или лечь — не падение.
    2. Остался внизу: hold_s после этого момента ни одного кадра с вертикальной позой. Наклонился за предметом
       или присел — через секунду снова стоит, тревоги нет. Упал — лежит, тревога через ~2 с после падения.
    Пороги подобраны на нечётных записях UR Fall, качество проверяется на чётных (eval/urfall.py --split).
    """

    def __init__(self, drop_window_s: float = 1.0, head_drop: float = 0.35, shrink: float = 0.75,
                 hold_s: float = 0.6, memory_s: float = 10.0):
        self.drop_window_s = drop_window_s
        self.head_drop = head_drop
        self.shrink = shrink
        self.hold_s = hold_s
        self.memory_s = memory_s

    def fall_moment(self, hist: list) -> float | None:
        moment = None
        for i, (t0, d0) in enumerate(hist):
            if not is_upright(d0):
                continue
            for t1, d1 in hist[i + 1:]:
                if t1 - t0 > self.drop_window_s:
                    break
                if (d1.box[1] - d0.box[1]) >= self.head_drop * d0.height and d1.height <= self.shrink * d0.height:
                    moment = t1                      # последний подходящий момент: после него не вставал
                    break
        return moment

    def evaluate(self, track: Track, ctx: RuleContext) -> Signal | None:
        hist = track.window(ctx.now, self.memory_s)
        if len(hist) < 3:
            return None
        t_fall = self.fall_moment(hist)
        if t_fall is None or ctx.now - t_fall < self.hold_s:
            return None
        if any(is_upright(d) for t, d in hist if t > t_fall + 0.3):
            return None                              # поднялся — не падение
        return Signal(0.9, {"torso_angle": round(torso_angle(track.det) or 0.0)})


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
