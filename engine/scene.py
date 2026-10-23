from dataclasses import dataclass, field
from datetime import datetime, time

import numpy as np

from .geometry import apply_homography, homography, point_in_polygon
from .types import Detection, Track


@dataclass
class Schedule:
    """Окно действия правила, например 07:55–12:00. Конец раньше начала — окно через полночь."""
    start: time
    end: time

    @classmethod
    def parse(cls, text: str) -> "Schedule":
        a, b = (part.strip() for part in text.split("-"))
        return cls(time.fromisoformat(a), time.fromisoformat(b))

    def active(self, at: datetime) -> bool:
        t = at.time()
        if self.start <= self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end


@dataclass
class RuleConfig:
    type: str
    params: dict
    confirm_s: float = 2.0
    severity: str = "high"            # critical | high | medium
    schedule: Schedule | None = None


@dataclass
class Zone:
    id: str
    title: str
    polygon: np.ndarray | None        # None — весь кадр
    rules: list[RuleConfig] = field(default_factory=list)
    exclude: set[str] = field(default_factory=set)   # типы нарушений, разрешённые в зоне

    def contains(self, pt: tuple[float, float]) -> bool:
        return self.polygon is None or point_in_polygon(pt, self.polygon)


class FloorMap:
    """Перевод точек кадра в метры на полу по четырём размеченным точкам."""

    def __init__(self, image_pts, world_m):
        self.h = homography(np.asarray(image_pts, float), np.asarray(world_m, float))

    def to_world(self, pt: tuple[float, float]) -> np.ndarray:
        return apply_homography(self.h, pt)


@dataclass
class Scene:
    camera_id: str
    zones: list[Zone]
    floor: FloorMap | None = None

    def distance_m(self, a: tuple[float, float], b: tuple[float, float]) -> float | None:
        if self.floor is None:
            return None
        return float(np.linalg.norm(self.floor.to_world(a) - self.floor.to_world(b)))

    def floor_speed(self, track: Track, now: float, window_s: float = 1.0) -> float | None:
        """Скорость по полу в м/с за последнее окно; None без калибровки или при короткой истории."""
        pts = track.window(now, window_s)
        if self.floor is None or len(pts) < 2 or pts[-1][0] - pts[0][0] < window_s * 0.5:
            return None
        (t0, d0), (t1, d1) = pts[0], pts[-1]
        return self.distance_m(d0.foot, d1.foot) / (t1 - t0)

    @staticmethod
    def speed_in_heights(track: Track, now: float, window_s: float = 1.0) -> float | None:
        """Запасная мера без калибровки: смещение ног в ростах человека за секунду."""
        pts = track.window(now, window_s)
        if len(pts) < 2 or pts[-1][0] - pts[0][0] < window_s * 0.5:
            return None
        (t0, d0), (t1, d1) = pts[0], pts[-1]
        h = float(np.median([d.height for _, d in pts]))
        if h <= 0:
            return None
        shift = float(np.hypot(d1.foot[0] - d0.foot[0], d1.foot[1] - d0.foot[1]))
        return shift / h / (t1 - t0)

    @staticmethod
    def gap_px(a: Detection, b: Detection) -> float:
        """Зазор между рамками по горизонтали и вертикали; 0, если пересекаются."""
        dx = max(0.0, max(a.box[0], b.box[0]) - min(a.box[2], b.box[2]))
        dy = max(0.0, max(a.box[1], b.box[1]) - min(a.box[3], b.box[3]))
        return float(np.hypot(dx, dy))
