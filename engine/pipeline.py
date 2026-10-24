"""Конвейер одной камеры: детекторы → трекер → правила по зонам → менеджер событий."""
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from .config import CameraConfig
from .detector import Detector
from .events import Event, EventManager
from .postprocess import letterbox
from .rules import RuleContext, build_rule
from .rules.objects import flame_colors
from .tracking import IouTracker
from .types import PERSON, Detection, Track

ZONE_TRACK = 0          # «трек» для событий уровня зоны: огонь, дым
_POOL = ThreadPoolExecutor(max_workers=int(os.environ.get("LINQ_INFER_WORKERS", "8")), thread_name_prefix="infer")


@dataclass
class FrameResult:
    detections: list[Detection]
    tracks: list[Track]
    active: list[Event]
    violators: set[int]              # ID треков с активным событием


class CameraPipeline:
    def __init__(self, cfg: CameraConfig, detectors: list[Detector], events: EventManager):
        self.cfg = cfg
        self.detectors = detectors
        self.events = events
        self.frame_shape: tuple[int, int] | None = None
        self.tracker = IouTracker()
        self.rules = {(z.id, rc.type): build_rule(rc.type, rc.params) for z in cfg.scene.zones for rc in z.rules}

    def _excluded(self, rule_type: str, track: Track) -> bool:
        return any(rule_type in z.exclude and z.contains(track.det.foot) for z in self.cfg.scene.zones)

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Все модели кадра считаются параллельно: ONNX Runtime и pytpu отпускают GIL на время инференса,
        поэтому несколько сетей одного кадра идут на ускорителе одновременно."""
        prepared: dict[int, tuple] = {}
        for d in self.detectors:
            if d.spec.imgsz not in prepared:
                prepared[d.spec.imgsz] = letterbox(frame, d.spec.imgsz)
        if len(self.detectors) == 1:
            d = self.detectors[0]
            return d(frame, prepared[d.spec.imgsz])
        futures = [_POOL.submit(d, frame, prepared[d.spec.imgsz]) for d in self.detectors]
        return [det for f in futures for det in f.result()]

    def step(self, detections: list[Detection], now: float, wall: datetime | None = None) -> FrameResult:
        """Обработка уже готовых детекций — так же работает режим воспроизведения детекций с LinQ."""
        wall = wall or datetime.now()
        tracks = self.tracker.update(detections, now)
        for zone in self.cfg.scene.zones:
            ctx = RuleContext(self.cfg.scene, zone, now, tracks, self.frame_shape)
            for rc in zone.rules:
                if rc.schedule and not rc.schedule.active(wall):
                    continue
                rule = self.rules[(zone.id, rc.type)]
                if rule.target == PERSON:
                    for tr in tracks:
                        if tr.cls != PERSON:
                            continue
                        sig = rule.evaluate(tr, ctx)
                        if sig is not None and self._excluded(rc.type, tr):
                            sig = None
                        self.events.observe(now, zone.id, rc.type, rule.title, rc.severity, rc.confirm_s,
                                            tr.det, tr.id, sig)
                    continue
                # огонь, дым и прочие объекты без «личности»: одно событие на зону, сильнейший сигнал кадра
                best, best_det = None, None
                for tr in tracks:
                    if tr.cls == rule.target:
                        sig = rule.evaluate(tr, ctx)
                        if sig is not None and not self._excluded(rc.type, tr) and (best is None or sig.score > best.score):
                            best, best_det = sig, tr.det
                det = best_det or Detection(np.zeros(4, np.float32), 0.0, rule.target)
                self.events.observe(now, zone.id, rc.type, rule.title, rc.severity, rc.confirm_s, det, ZONE_TRACK, best)
        self.events.tick(now, lambda tid: 0.0 if tid == ZONE_TRACK else self.tracker.lost_for(tid, now))
        active = list(self.events.active.values())
        return FrameResult(detections, tracks, active, {e.track_id for e in active})

    def process(self, frame: np.ndarray, now: float, wall: datetime | None = None) -> FrameResult:
        self.frame_shape = frame.shape[:2]
        detections = self.detect(frame)
        for d in detections:
            if d.cls == "fire":                     # цвет пламени — для отсева ламп, бликов и жёлтых жилетов
                d.attrs.update(flame_colors(frame, d.box))
        return self.step(detections, now, wall)
