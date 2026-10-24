"""Правила по объектам, не привязанным к человеку."""
import cv2
import numpy as np

from ..geometry import ioa
from ..types import PERSON, Detection, Track
from .base import RuleContext, Signal, register
from .person import head_box, torso_box

# Детектор огня с Hugging Face путает пламя с лампами, бликом сварки, жёлтым жилетом и оранжевой каской.
# Проверка по пикселям рамки (подобрано на срабатываниях в демо-смене):
FLAME_MIN = 0.2      # доля «огненных» пикселей: красный—жёлтый, насыщенный, яркий; у пламени 0,27–0,63, у ламп ~0
WHITE_MAX = 0.4      # лампа и блик сварки — почти белое пятно (0,7 и больше), у пламени до 0,2


def flame_colors(frame: np.ndarray, box: np.ndarray) -> dict:
    x1, y1, x2, y2 = (int(v) for v in box)
    crop = frame[max(y1, 0):max(y2, y1 + 1), max(x1, 0):max(x2, x1 + 1)]
    if crop.size == 0:
        return {}
    h, s, v = cv2.split(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV))
    return {"flame": float((((h <= 35) | (h >= 170)) & (s >= 90) & (v >= 140)).mean()),
            "white": float(((s < 50) & (v >= 220)).mean())}


def on_person(det: Detection, ctx: RuleContext) -> bool:
    """Рамка «огня» лежит на голове или корпусе человека — это каска, жилет или дым сигареты у лица."""
    return any(t.cls == PERSON and t.last_seen == ctx.now
               and (ioa(det.box, head_box(t.det)) >= 0.6 or ioa(det.box, torso_box(t.det)) >= 0.6)
               for t in ctx.tracks)


@register("open_fire", "Открытый огонь", target="fire")
class OpenFire:
    def evaluate(self, track: Track, ctx: RuleContext) -> Signal | None:
        det = track.det
        if not ctx.zone.contains(det.center):
            return None
        colors = det.attrs
        if colors and (colors.get("flame", 1.0) < FLAME_MIN or colors.get("white", 0.0) > WHITE_MAX):
            return None
        if on_person(det, ctx):
            return None
        return Signal(det.score, {"flame": round(colors["flame"], 2)} if "flame" in colors else {})


@register("smoke", "Задымление", target="smoke")
class Smoke:
    def evaluate(self, track: Track, ctx: RuleContext) -> Signal | None:
        if ctx.zone.contains(track.det.center):
            return Signal(track.det.score)
        return None
