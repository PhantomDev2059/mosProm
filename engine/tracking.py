"""Трекер по пересечению рамок. Каркас: интерфейс как у ByteTrack, чтобы заменить без правок вокруг."""
import itertools

from .geometry import iou
from .types import Detection, Track


class IouTracker:
    def __init__(self, iou_threshold: float = 0.3, max_lost_s: float = 3.0):
        self.iou_threshold = iou_threshold
        self.max_lost_s = max_lost_s
        self.tracks: dict[int, Track] = {}
        self._ids = itertools.count(1)

    def update(self, detections: list[Detection], now: float) -> list[Track]:
        """Сопоставляет детекции кадра с треками; возвращает треки, увиденные в этом кадре."""
        seen: list[Track] = []
        free = list(self.tracks.values())
        # жадно: сначала самые уверенные детекции забирают лучшие по IoU треки своего класса
        for det in sorted(detections, key=lambda d: -d.score):
            best, best_iou = None, self.iou_threshold
            for tr in free:
                if tr.cls != det.cls:
                    continue
                v = iou(tr.det.box, det.box)
                if v >= best_iou:
                    best, best_iou = tr, v
            if best is None:
                best = Track(id=next(self._ids), cls=det.cls, det=det, first_seen=now, last_seen=now)
                self.tracks[best.id] = best
            else:
                free.remove(best)
                best.det, best.last_seen = det, now
            best.history.append((now, det))
            seen.append(best)
        for tid in [t.id for t in self.tracks.values() if now - t.last_seen > self.max_lost_s]:
            del self.tracks[tid]
        return seen

    def lost_for(self, track_id: int, now: float) -> float:
        tr = self.tracks.get(track_id)
        return float("inf") if tr is None else now - tr.last_seen
