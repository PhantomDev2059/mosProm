"""Правила, которые проверяют человека: зоны, СИЗ, техника, бег, телефон, курение."""
import numpy as np

from ..geometry import ioa
from ..scene import Scene
from ..types import L_ANKLE, L_HIP, L_SHOULDER, L_WRIST, NOSE, PERSON, R_ANKLE, R_HIP, R_SHOULDER, R_WRIST, Detection, Track
from .base import RuleContext, Signal, register

# «machinery» открытой модели СИЗ срабатывает на леса, щиты подстанции и стеллажи — в технику не берём
VEHICLES = ("forklift", "vehicle")
MAX_VEHICLE_AREA = 0.35     # рамка «техники» на треть кадра и больше — почти всегда ложная (кирпичи, лестница крупным планом)


def head_box(det: Detection) -> np.ndarray:
    """Верхняя часть рамки человека, где должна быть голова."""
    x1, y1, x2, y2 = det.box
    return np.array([x1, y1, x2, y1 + (y2 - y1) * 0.28], np.float32)


def torso_box(det: Detection) -> np.ndarray:
    """Корпус: по плечам и бёдрам скелета, если они видны; иначе — доля рамки (на крупном плане рамка обрезана,
    и «18–65 % высоты» попадает куда угодно, только не на жилет)."""
    pts = [det.keypoint(i) for i in (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP)]
    sh = [p for p in pts[:2] if p is not None]
    if len(sh) == 2:
        xs = [p[0] for p in pts if p is not None]
        ys = [p[1] for p in pts if p is not None]
        width = max(xs) - min(xs)
        top = min(p[1] for p in sh)
        bottom = max(ys) if any(p is not None for p in pts[2:]) else min(det.box[3], top + 2.2 * width)
        pad = 0.15 * width
        return np.array([max(det.box[0], min(xs) - pad), top - pad, min(det.box[2], max(xs) + pad), bottom + pad],
                        np.float32)
    x1, y1, x2, y2 = det.box
    return np.array([x1, y1 + (y2 - y1) * 0.18, x2, y1 + (y2 - y1) * 0.65], np.float32)


def overlap_small(a: np.ndarray, b: np.ndarray) -> float:
    """Пересечение относительно меньшей рамки: жилет крупным планом больше «зоны корпуса» — это всё равно жилет."""
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return float(iw * ih / small) if small > 0 else 0.0


def items_near(ctx: RuleContext, classes: tuple[str, ...], region: np.ndarray, min_ioa: float = 0.5) -> list[Detection]:
    return [t.det for t in ctx.tracks
            if t.cls in classes and t.last_seen == ctx.now and ioa(t.det.box, region) >= min_ioa]


@register("zone_intrusion", "Нахождение в опасной зоне")
class ZoneIntrusion:
    def __init__(self, low_posture: bool = False, low_ratio: float = 0.65):
        self.low_posture = low_posture        # пролезание под конвейером или грузом
        self.low_ratio = low_ratio

    def evaluate(self, track: Track, ctx: RuleContext) -> Signal | None:
        if ctx.zone.polygon is None or not ctx.zone.contains(track.det.foot):
            return None
        if self.low_posture:
            tall = float(np.percentile([d.height for _, d in track.history], 90))
            if track.det.height > self.low_ratio * tall:
                return None
        return Signal(1.0)


@register("no_helmet", "Нет каски")
class NoHelmet:
    def __init__(self, min_person_px: int = 70):
        self.min_person_px = min_person_px

    def evaluate(self, track: Track, ctx: RuleContext) -> Signal | None:
        det = track.det
        if det.height < self.min_person_px or not ctx.zone.contains(det.foot):
            return None
        region = head_box(det)
        bare = items_near(ctx, ("head", "no_helmet"), region, 0.4)
        helmet = items_near(ctx, ("helmet",), region, 0.4)
        if bare and not helmet:
            return Signal(max(d.score for d in bare))
        return None


@register("no_vest", "Нет сигнального жилета")
class NoVest:
    def __init__(self, min_person_px: int = 90):
        self.min_person_px = min_person_px

    def evaluate(self, track: Track, ctx: RuleContext) -> Signal | None:
        det = track.det
        if det.height < self.min_person_px or not ctx.zone.contains(det.foot):
            return None
        region = torso_box(det)
        vests = [t.det for t in ctx.tracks if t.cls == "vest" and t.last_seen == ctx.now]
        if any(overlap_small(v.box, region) >= 0.3 for v in vests):
            return None
        no_vest = items_near(ctx, ("no_vest",), region, 0.3)
        if no_vest:
            return Signal(max(d.score for d in no_vest))
        return None


@register("vehicle_proximity", "Опасное сближение с техникой")
class VehicleProximity:
    def __init__(self, min_distance_m: float = 2.5, min_gap_heights: float = 0.5, depth_tolerance: float = 0.25,
                 moving_only: bool = False, min_vehicle_speed: float = 0.15):
        self.min_distance_m = min_distance_m
        self.min_gap_heights = min_gap_heights   # без калибровки пола: зазор в ростах человека
        self.depth_tolerance = depth_tolerance   # ноги человека и низ техники на одной глубине (доля роста)
        self.moving_only = moving_only
        self.min_vehicle_speed = min_vehicle_speed

    def evaluate(self, track: Track, ctx: RuleContext) -> Signal | None:
        person = track.det
        # без видимых ступней нельзя сравнить глубину человека и техники: обрезанный краем кадра человек
        # на переднем плане «касается» погрузчика, который стоит в глубине склада
        if person.keypoints is not None and person.keypoint(L_ANKLE) is None and person.keypoint(R_ANKLE) is None:
            return None
        if ctx.frame_shape and person.box[3] >= 0.98 * ctx.frame_shape[0]:   # ноги за краем кадра, щиколотки «додуманы»
            return None
        frame_area = ctx.frame_shape[0] * ctx.frame_shape[1] if ctx.frame_shape else None
        for v in ctx.tracks:
            if v.cls not in VEHICLES or v.last_seen != ctx.now:
                continue
            if frame_area and v.det.width * v.det.height > MAX_VEHICLE_AREA * frame_area:
                continue
            if ioa(person.box, v.det.box) > 0.6:      # водитель внутри погрузчика
                continue
            if self.moving_only:
                speed = Scene.speed_in_heights(v, ctx.now) or 0.0
                if speed < self.min_vehicle_speed:
                    continue
            dist = ctx.scene.distance_m(person.foot, v.det.foot)
            if dist is not None:
                if dist < self.min_distance_m:
                    return Signal(1.0 - dist / self.min_distance_m, {"distance_m": round(dist, 1), "vehicle": v.cls})
                continue
            # без калибровки пола пересечение рамок в кадре ничего не значит: техника может стоять в глубине.
            # Сравниваем глубину по нижней кромке и считаем зазор только по горизонтали.
            h = max(person.height, 1.0)
            if abs(person.box[3] - v.det.box[3]) > self.depth_tolerance * h:
                continue
            gap = float(max(0.0, max(person.box[0], v.det.box[0]) - min(person.box[2], v.det.box[2])) / h)
            if gap < self.min_gap_heights:
                return Signal(1.0 - gap / self.min_gap_heights, {"gap_heights": round(gap, 2), "vehicle": v.cls})
        return None


@register("running", "Бег")
class Running:
    def __init__(self, max_speed_mps: float = 2.5, max_heights_per_s: float = 1.4, min_track_s: float = 1.0):
        self.max_speed_mps = max_speed_mps
        self.max_heights_per_s = max_heights_per_s
        self.min_track_s = min_track_s

    def evaluate(self, track: Track, ctx: RuleContext) -> Signal | None:
        if track.age < self.min_track_s or not ctx.zone.contains(track.det.foot):
            return None
        v = ctx.scene.floor_speed(track, ctx.now)
        if v is not None:
            return Signal(min(1.0, v / (2 * self.max_speed_mps)), {"speed_mps": round(v, 1)}) if v > self.max_speed_mps else None
        vh = Scene.speed_in_heights(track, ctx.now)
        if vh is not None and vh > self.max_heights_per_s:
            return Signal(min(1.0, vh / (2 * self.max_heights_per_s)), {"heights_per_s": round(vh, 2)})
        return None


def body_size(det: Detection) -> float:
    """Рост человека в пикселях. На крупном плане рамка обрезана краем кадра и рост занижен в разы —
    тогда оцениваем по ширине плеч (у взрослого она около четверти роста)."""
    ls, rs = det.keypoint(L_SHOULDER), det.keypoint(R_SHOULDER)
    shoulders = float(np.hypot(*(ls[:2] - rs[:2]))) if ls is not None and rs is not None else 0.0
    return max(det.height, 4.0 * shoulders)


def _near_hands_or_face(det: Detection, item: Detection, radius: float) -> bool:
    cx, cy = item.center
    for idx in (L_WRIST, R_WRIST, NOSE):
        p = det.keypoint(idx)
        if p is not None and np.hypot(p[0] - cx, p[1] - cy) < radius:
            return True
    return False


@register("phone", "Телефон в рабочей зоне")
class Phone:
    def __init__(self, radius_heights: float = 0.25):
        self.radius_heights = radius_heights

    def evaluate(self, track: Track, ctx: RuleContext) -> Signal | None:
        det = track.det
        if not ctx.zone.contains(det.foot):
            return None
        for item in items_near(ctx, ("phone",), det.box, 0.6):
            if det.keypoints is None or _near_hands_or_face(det, item, self.radius_heights * body_size(det)):
                return Signal(item.score)
        return None


@register("smoking", "Курение")
class Smoking:
    def __init__(self, radius_heights: float = 0.2):
        self.radius_heights = radius_heights

    def evaluate(self, track: Track, ctx: RuleContext) -> Signal | None:
        det = track.det
        if not ctx.zone.contains(det.foot):
            return None
        for item in items_near(ctx, ("cigarette",), det.box, 0.6):
            if det.keypoints is None or _near_hands_or_face(det, item, self.radius_heights * body_size(det)):
                return Signal(item.score)
        return None


__all__ = ["PERSON", "VEHICLES"]
