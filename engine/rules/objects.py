"""Правила по объектам, не привязанным к человеку."""
from ..types import Track
from .base import RuleContext, Signal, register


@register("open_fire", "Открытый огонь", target="fire")
class OpenFire:
    def evaluate(self, track: Track, ctx: RuleContext) -> Signal | None:
        if ctx.zone.contains(track.det.center):
            return Signal(track.det.score)
        return None


@register("smoke", "Задымление", target="smoke")
class Smoke:
    def evaluate(self, track: Track, ctx: RuleContext) -> Signal | None:
        if ctx.zone.contains(track.det.center):
            return Signal(track.det.score)
        return None
