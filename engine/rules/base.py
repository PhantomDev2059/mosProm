from dataclasses import dataclass, field
from typing import Protocol

from ..scene import Scene, Zone
from ..types import PERSON, Track


@dataclass
class Signal:
    score: float                                     # 0..1: уверенность, что нарушение есть в этом кадре
    details: dict = field(default_factory=dict)


@dataclass
class RuleContext:
    scene: Scene
    zone: Zone
    now: float
    tracks: list[Track]                              # все треки кадра: люди, техника, СИЗ, огонь
    frame_shape: tuple[int, int] | None = None       # (высота, ширина) кадра, если известна


class Rule(Protocol):
    type: str
    title: str
    target: str                                      # класс треков, которые проверяет правило

    def evaluate(self, track: Track, ctx: RuleContext) -> Signal | None: ...


RULES: dict[str, type] = {}


def register(name: str, title: str, target: str = PERSON):
    """Новое нарушение = новый класс с этим декоратором; ядро, трекер и менеджер событий не меняются."""
    def deco(cls):
        cls.type, cls.title, cls.target = name, title, target
        RULES[name] = cls
        return cls
    return deco


def build_rule(rule_type: str, params: dict) -> Rule:
    if rule_type not in RULES:
        raise ValueError(f"неизвестное правило {rule_type!r}; доступны: {', '.join(sorted(RULES))}")
    return RULES[rule_type](**params)
