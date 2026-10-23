"""Менеджер событий: превращает покадровые сигналы правил в одно событие на одно нарушение.

Ключ — (зона, тип нарушения, трек). Сигнал копится в окне confirm_s; событие подтверждается,
когда доля кадров с сигналом в окне не меньше ratio. Подтверждение — единственный момент,
когда уходит оповещение. Дальше событие только обновляется: длительность, лучший кадр.
Закрывается, если сигнала нет release_s или трек пропал дольше lost_s. После закрытия
в течение cooldown_s новое срабатывание того же типа в той же зоне рядом с прежней рамкой
приклеивается к старому событию — так переживается смена ID трека.
"""
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .geometry import iou
from .rules.base import Signal
from .types import Detection

Key = tuple[str, str, int]


@dataclass
class Event:
    id: str
    camera: str
    zone: str
    type: str
    title: str
    severity: str
    track_id: int
    started_at: float                # время первого сигнала (часы камеры)
    confirmed_at: float
    last_signal: float
    box: list[float]
    score: float
    details: dict = field(default_factory=dict)
    closed_at: float | None = None
    status: str = "new"              # new | confirmed | false | sent_to_master | resolved
    wall_time: float = field(default_factory=time.time)
    snapshot: str | None = None

    @property
    def duration_s(self) -> float:
        end = self.closed_at if self.closed_at is not None else self.last_signal
        return round(end - self.started_at, 1)

    @property
    def active(self) -> bool:
        return self.closed_at is None


@dataclass
class _Candidate:
    samples: deque = field(default_factory=deque)     # (t, bool)
    since: float | None = None


class EventManager:
    def __init__(self, camera: str, ratio: float = 0.7, release_s: float = 5.0, lost_s: float = 3.0,
                 cooldown_s: float = 60.0, merge_iou: float = 0.2,
                 on_open: Callable[[Event, Detection], None] | None = None,
                 on_update: Callable[[Event, Detection], None] | None = None,
                 on_close: Callable[[Event], None] | None = None):
        self.camera = camera
        self.ratio, self.release_s, self.lost_s = ratio, release_s, lost_s
        self.cooldown_s, self.merge_iou = cooldown_s, merge_iou
        self.on_open, self.on_update, self.on_close = on_open, on_update, on_close
        self.candidates: dict[Key, _Candidate] = {}
        self.active: dict[Key, Event] = {}
        self.recent: list[Event] = []

    def observe(self, now: float, zone: str, rule_type: str, title: str, severity: str, confirm_s: float,
                det: Detection, track_id: int, signal: Signal | None) -> None:
        key = (zone, rule_type, track_id)
        ev = self.active.get(key)
        if ev is not None:
            if signal is not None:
                ev.last_signal = now
                ev.details = signal.details or ev.details
                if signal.score >= ev.score:
                    ev.score, ev.box = signal.score, det.box.tolist()
                    if self.on_update:
                        self.on_update(ev, det)
            return

        cand = self.candidates.setdefault(key, _Candidate())
        cand.samples.append((now, signal is not None))
        while cand.samples and now - cand.samples[0][0] > max(confirm_s, 0.5):
            cand.samples.popleft()
        share = sum(1 for _, s in cand.samples if s) / len(cand.samples)
        if signal is None:
            if share < self.ratio:
                cand.since = None            # одиночные кадры с сигналом тревогу не поднимают
            return
        if cand.since is None:
            cand.since = now
        if share < self.ratio or now - cand.since < confirm_s:
            return

        del self.candidates[key]
        merged = self._merge_target(now, zone, rule_type, det, track_id)
        if merged is not None:
            old_key = (merged.zone, merged.type, merged.track_id)
            self.active.pop(old_key, None)
            merged.track_id, merged.closed_at, merged.last_signal = track_id, None, now
            self.active[key] = merged
            if self.on_update:
                self.on_update(merged, det)
            return
        ev = Event(id=f"{self.camera}-{uuid.uuid4().hex[:8]}", camera=self.camera, zone=zone, type=rule_type,
                   title=title, severity=severity, track_id=track_id, started_at=cand.since, confirmed_at=now,
                   last_signal=now, box=det.box.tolist(), score=signal.score, details=signal.details)
        self.active[key] = ev
        if self.on_open:
            self.on_open(ev, det)

    def _merge_target(self, now: float, zone: str, rule_type: str, det: Detection, track_id: int) -> Event | None:
        """Событие того же типа в той же зоне рядом с текущей рамкой, чей трек пропал или закрылся недавно."""
        best, best_iou = None, self.merge_iou
        pool = [e for e in self.active.values() if e.track_id != track_id and e.last_signal < now]
        pool += [e for e in self.recent if now - (e.closed_at or now) <= self.cooldown_s]
        for e in pool:
            if e.zone != zone or e.type != rule_type:
                continue
            if track_id == 0 and e.track_id == 0:      # событие уровня зоны (огонь, дым): склеиваем без сверки рамок
                best = e
                break
            v = iou(np.asarray(e.box), det.box)
            if v >= best_iou:
                best, best_iou = e, v
        if best is not None and best in self.recent:
            self.recent.remove(best)
        return best

    def tick(self, now: float, lost_for: Callable[[int], float]) -> None:
        """Раз в кадр: закрыть события без сигнала или с пропавшим треком, почистить старые кандидаты."""
        for key, ev in list(self.active.items()):
            if now - ev.last_signal > self.release_s or lost_for(ev.track_id) > self.lost_s:
                ev.closed_at = ev.last_signal
                del self.active[key]
                self.recent.append(ev)
                if self.on_close:
                    self.on_close(ev)
        self.recent = [e for e in self.recent if now - (e.closed_at or now) <= self.cooldown_s]
        for key, cand in list(self.candidates.items()):
            if not cand.samples or now - cand.samples[-1][0] > self.lost_s:
                del self.candidates[key]
