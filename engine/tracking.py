"""Трекер по пересечению рамок. Каркас: интерфейс как у ByteTrack, чтобы заменить без правок вокруг."""
import itertools

import numpy as np

from .geometry import iou
from .types import Detection, Track


class IouTracker:
    def __init__(self, iou_threshold: float = 0.3, max_lost_s: float = 3.0, center_threshold: float = 0.6):
        self.iou_threshold = iou_threshold
        self.max_lost_s = max_lost_s
        self.center_threshold = center_threshold
        self.tracks: dict[int, Track] = {}
        self._ids = itertools.count(1)

    def update(self, detections: list[Detection], now: float) -> list[Track]:
        """Сопоставляет детекции кадра с треками; возвращает треки, увиденные в этом кадре.

        Первый проход — по пересечению рамок. Второй — для оставшихся — по расстоянию между центрами в долях
        размера рамки: когда человек падает, рамка за доли секунды из высокой становится широкой, IoU падает,
        а трек терять нельзя — правило падения смотрит на историю «стоял → лежит».
        """
        seen: list[Track] = []
        free = list(self.tracks.values())
        pending: list[Detection] = []
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
                pending.append(det)
                continue
            free.remove(best)
            self._assign(best, det, now)
            seen.append(best)
        for det in pending:
            best, best_d = None, self.center_threshold
            for tr in free:
                if tr.cls != det.cls or now - tr.last_seen > self.max_lost_s:
                    continue
                size = max(tr.det.width, tr.det.height, det.width, det.height, 1.0)
                d = float(np.hypot(det.center[0] - tr.det.center[0], det.center[1] - tr.det.center[1])) / size
                if d < best_d:
                    best, best_d = tr, d
            if best is None:
                best = Track(id=next(self._ids), cls=det.cls, det=det, first_seen=now, last_seen=now)
                self.tracks[best.id] = best
            else:
                free.remove(best)
            self._assign(best, det, now)
            seen.append(best)
        for tid in [t.id for t in self.tracks.values() if now - t.last_seen > self.max_lost_s]:
            del self.tracks[tid]
        return seen

    @staticmethod
    def _assign(track: Track, det: Detection, now: float) -> None:
        track.det, track.last_seen = det, now
        track.history.append((now, det))

    def lost_for(self, track_id: int, now: float) -> float:
        tr = self.tracks.get(track_id)
        return float("inf") if tr is None else now - tr.last_seen
