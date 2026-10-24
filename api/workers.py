"""Обработка источников: видеофайлы и RTSP в отдельных потоках, вебкамера браузера — по кадру."""
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from engine.backends import LinqCloudBackend, LinqTpuBackend, OnnxBackend
from engine.config import CameraConfig
from engine.dets_io import from_json, load_replay, replay_path
from engine.detector import Detector, ModelSpec
from engine.events import Event, EventManager
from engine.geometry import iou
from engine.pipeline import CameraPipeline, FrameResult
from engine.render import draw

from .store import EventStore

log = logging.getLogger("linqguard")
cv2.setNumThreads(2)       # камеры и так работают в своих потоках; внутренний пул OpenCV на все ядра им только мешает

CLIP_PRE_S = 4.0       # секунд до подтверждения нарушения во фрагменте
CLIP_POST_S = 4.0      # и после
REPLAY_TOL_S = 6.0     # видеофайл по кругу: нарушение на той же позиции записи — повтор уже записанного
FRAMES_INFLIGHT = int(os.environ.get("LINQ_FRAMES_INFLIGHT", "4"))   # кадров камеры одновременно на ускорителе


def load_detectors(registry_path: Path, backend: str = "onnx") -> dict[str, Detector]:
    """backend="tpu" — программы .tpu на ускорителе LinQ (стенд), иначе ONNX Runtime на GPU/CPU."""
    reg = json.loads(registry_path.read_text())
    out = {}
    for name, m in reg.items():
        spec = ModelSpec(name=name, kind=m["kind"], path=m["path"], conf=m.get("conf", 0.4), imgsz=m.get("imgsz", 640),
                         class_map={int(k): v for k, v in m["class_map"].items()})
        # tpu_v2 — улучшенное квантование (калибровка с объектами + дообучение порогов), где оно есть
        tpu_dir = next((d for d in (registry_path.parent / "tpu_v2", registry_path.parent / "tpu")
                        if any(d.glob(f"{name}_b1_*_h1m.tpu"))), registry_path.parent / "tpu")
        programs = sorted(tpu_dir.glob(f"{name}_b1_*_h1m.tpu"))
        # LINQ_CPU_MODELS — модели, которые считаем в FP32 на CPU даже в режиме tpu (квантование их портит)
        if backend == "tpu" and programs and name not in os.environ.get("LINQ_CPU_MODELS", "").split(","):
            # /dev/tpu* — ускоритель в машине; на стенде хакатона его нет, там облачный воркер через RabbitMQ
            cls = LinqTpuBackend if list(Path("/dev").glob("tpu*")) else LinqCloudBackend
            be = cls(str(programs[0]), str(tpu_dir / f"{name}_tail.onnx"))
        else:
            be = OnnxBackend(spec.path)
        out[name] = Detector(spec, be)
    return out


class SourceProcessor:
    """Общее для всех источников: конвейер, снимки-доказательства, запись в журнал, оповещения."""

    def __init__(self, cfg: CameraConfig, detectors: list[Detector], store: EventStore, media: Path,
                 publish: Callable[[dict], None], notifier=None):
        self.cfg = cfg
        self.store = store
        self.media = media
        self.publish = publish
        self.notifier = notifier
        self._pending: list[tuple[str, Event]] = []
        self.events = EventManager(cfg.id, on_open=self._on_open, on_update=self._on_update, on_close=self._on_close)
        self.pipeline = CameraPipeline(cfg, detectors, self.events)
        self.latest_jpeg: bytes | None = None
        self.latest_preview: bytes | None = None
        self.latest_raw: np.ndarray | None = None
        self.proc_ms: deque = deque(maxlen=120)
        self.frames = 0
        self.fps = 0.0
        self._fps_t = time.time()
        self._fps_n = 0
        self._last_update_pub: dict[str, float] = {}
        self._ring: deque = deque(maxlen=max(1, int(cfg.analysis_fps * CLIP_PRE_S)))
        self._clips: dict[str, dict] = {}
        self._clock_offset = time.time()
        self._pass_no, self._pass_start = 0, 0.0
        self._recorded: list[tuple[str, str, float, np.ndarray, str]] = []   # (зона, тип, позиция, рамка, id)
        self._replay_of: dict[str, str] = {}      # событие повторного круга → исходное событие в журнале

    def rebuild(self, cfg: CameraConfig) -> None:
        detectors = self.pipeline.detectors
        self.cfg = cfg
        self.events = EventManager(cfg.id, on_open=self._on_open, on_update=self._on_update, on_close=self._on_close)
        self.pipeline = CameraPipeline(cfg, detectors, self.events)

    # колбэки менеджера событий вызываются внутри pipeline.process; кадр рисуем после него
    def _on_open(self, ev: Event, det) -> None:
        self._pending.append(("open", ev))

    def _on_update(self, ev: Event, det) -> None:
        self._pending.append(("update", ev))

    def _on_close(self, ev: Event) -> None:
        orig = self._replay_of.pop(ev.id, None)
        if orig is not None:                       # повтор с круга демо-файла: исходное событие снова неактивно
            self.store.update(orig, closed=1)
            self.publish({"kind": "event_close", "event": self.store.get(orig)})
            return
        if self.store.get(ev.id) is None:
            return
        self.store.update(ev.id, duration_s=ev.duration_s, closed=1, score=ev.score)
        self.publish({"kind": "event_close", "event": self.store.get(ev.id)})

    def new_pass(self, start: float) -> None:
        """Видеофайл пошёл на новый круг. События прошлого прохода закрываются и не тянутся через весь
        ролик; нарушения, уже записанные на этой позиции записи, повторно не регистрируются — та же запись,
        то же нарушение. На кадре и в счётчике активных они по-прежнему видны."""
        self.events.close_all()
        self._pass_no += 1
        self._pass_start = start

    def _replay_target(self, ev: Event, pos: float) -> str | None:
        """id уже записанного события, которое повторяет это (тот же тип, зона, место в кадре и в записи)."""
        if not self._pass_no:
            return None
        box = np.asarray(ev.box)
        return next((eid for z, t, p, b, eid in self._recorded if z == ev.zone and t == ev.type
                     and abs(p - pos) <= REPLAY_TOL_S and (ev.track_id == 0 or iou(b, box) >= 0.3)), None)

    def _wall(self, t: float) -> float:
        return self._clock_offset + t

    def process(self, frame: np.ndarray, now: float, detections: list | None = None) -> bytes:
        """detections — уже посчитанные детекции кадра (конвейер VideoWorker держит несколько кадров в полёте)."""
        t0 = time.perf_counter()
        if detections is None:
            res = self.pipeline.process(frame, now, datetime.now())
        else:
            self.pipeline.frame_shape = frame.shape[:2]
            res = self.pipeline.step(self.pipeline.enrich(frame, detections), now, datetime.now())
        annotated = draw(frame, self.cfg.scene, res)
        self._flush_pending(frame, res, now)
        ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
        self.latest_jpeg = buf.tobytes() if ok else self.latest_jpeg
        small = cv2.resize(annotated, (640, round(annotated.shape[0] * 640 / annotated.shape[1])))
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 72])
        self.latest_preview = buf.tobytes() if ok else self.latest_preview
        self._feed_clips(self.latest_preview, now)
        self.latest_raw = frame
        self.proc_ms.append((time.perf_counter() - t0) * 1e3)
        self.frames += 1
        self._fps_n += 1
        if time.time() - self._fps_t >= 2:
            self.fps = self._fps_n / (time.time() - self._fps_t)
            self._fps_t, self._fps_n = time.time(), 0
        return self.latest_jpeg

    def _flush_pending(self, frame: np.ndarray, res: FrameResult, now: float) -> None:
        pending, self._pending = self._pending, []
        for kind, ev in pending:
            if kind == "open":
                pos = ev.started_at - self._pass_start
                orig = self._replay_target(ev, pos)
                if orig is not None:                    # без новой записи и оповещения, исходное — снова активно
                    self._replay_of[ev.id] = orig
                    self.store.update(orig, closed=0)
                    self.publish({"kind": "event_update", "event": self.store.get(orig)})
                    continue
                self._recorded.append((ev.zone, ev.type, pos, np.asarray(ev.box), ev.id))
                snap = self._snapshot(frame, res, ev)
                started = self._wall(ev.started_at)
                zone = next((z for z in self.cfg.scene.zones if z.id == ev.zone), None)
                self.store.insert({
                    "id": ev.id, "camera": self.cfg.id, "camera_title": self.cfg.title, "zone": ev.zone,
                    "zone_title": zone.title if zone else ev.zone, "type": ev.type, "title": ev.title,
                    "severity": ev.severity, "track_id": ev.track_id, "started_at": started,
                    "confirmed_at": self._wall(ev.confirmed_at), "duration_s": ev.duration_s,
                    "latency_s": round(ev.confirmed_at - ev.started_at, 2), "score": ev.score,
                    "details": ev.details, "snapshot": snap, "status": "new", "box": [round(v) for v in ev.box],
                    "history": [{"status": "new", "at": time.time(), "who": "система"}], "closed": 0,
                })
                row = self.store.get(ev.id)
                self.publish({"kind": "event_open", "event": row})
                if self.notifier is not None:
                    self.notifier.event_opened(row)
                self._clips[ev.id] = {"frames": list(self._ring), "until": now + CLIP_POST_S}
            else:
                last = self._last_update_pub.get(ev.id, 0)
                if time.time() - last < 1.0:
                    continue
                self._last_update_pub[ev.id] = time.time()
                if self.store.get(ev.id) is None:
                    continue
                self._snapshot(frame, res, ev)
                self.store.update(ev.id, duration_s=ev.duration_s, score=ev.score, details=ev.details, closed=0)
                self.publish({"kind": "event_update", "event": self.store.get(ev.id)})

    def _feed_clips(self, jpg: bytes, now: float) -> None:
        """Кольцевой буфер последних секунд и запись фрагментов вокруг момента тревоги."""
        self._ring.append(jpg)
        for eid, clip in list(self._clips.items()):
            clip["frames"].append(jpg)
            if now >= clip["until"]:
                del self._clips[eid]
                threading.Thread(target=self._write_clip, args=(eid, clip["frames"]), daemon=True).start()

    def _write_clip(self, eid: str, frames: list[bytes]) -> None:
        name = f"{eid}.mp4"
        fps = str(max(1, round(self.cfg.analysis_fps)))
        proc = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", fps, "-i", "-",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
             "-movflags", "+faststart", str(self.media / name)],
            input=b"".join(frames), capture_output=True)
        if proc.returncode == 0:
            self.store.update(eid, clip=f"/media/{name}")
            row = self.store.get(eid)
            self.publish({"kind": "event_update", "event": row})
            if self.notifier is not None and row is not None:
                self.notifier.clip_ready(row)

    def _snapshot(self, frame: np.ndarray, res: FrameResult, ev: Event) -> str:
        img = draw(frame, self.cfg.scene, res, highlight_track=ev.track_id)
        x1, y1, x2, y2 = (int(v) for v in ev.box)
        cv2.rectangle(img, (x1 - 4, y1 - 4), (x2 + 4, y2 + 4), (40, 40, 230), 4)
        name = f"{ev.id}.jpg"
        cv2.imwrite(str(self.media / name), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return f"/media/{name}"

    def stats(self) -> dict:
        arr = np.asarray(self.proc_ms) if self.proc_ms else None
        return {"camera": self.cfg.id, "title": self.cfg.title, "fps": round(self.fps, 1), "frames": self.frames,
                "proc_p50": round(float(np.percentile(arr, 50)), 1) if arr is not None else None,
                "proc_p95": round(float(np.percentile(arr, 95)), 1) if arr is not None else None,
                "active": len(self.events.active)}


class _Done:
    """Готовый результат в интерфейсе Future — для кадров с детекциями из прогона на TPU."""

    def __init__(self, value):
        self._v = value

    def result(self):
        return self._v

    def done(self):
        return True


class VideoWorker(threading.Thread):
    """Видеофайл (по кругу, в реальном времени) или RTSP-поток."""

    def __init__(self, proc: SourceProcessor, source: str):
        super().__init__(daemon=True, name=f"cam-{proc.cfg.id}")
        self.proc = proc
        self.source = source
        self.stop_flag = threading.Event()
        self._infer = ThreadPoolExecutor(max_workers=FRAMES_INFLIGHT, thread_name_prefix=f"frames-{proc.cfg.id}")

    def _replay(self):
        if os.environ.get("LINQ_REPLAY", "0") != "1":
            return None
        names = [d.spec.name for d in self.proc.pipeline.detectors]
        data = load_replay(replay_path(Path(os.environ.get("LINQ_DATA", "/workspace/linq/data")), self.proc.cfg.id),
                           self.proc.cfg.source, names)
        if data:
            log.info("камера %s: воспроизведение %d кадров, посчитанных на TPU", self.proc.cfg.id, len(data))
        return data

    def _finish(self, item) -> None:
        frame, now, fut = item
        try:
            self.proc.process(frame, now, fut.result())
        except Exception:                               # один сбойный кадр не должен останавливать камеру
            log.exception("камера %s: ошибка обработки кадра", self.proc.cfg.id)

    def run(self) -> None:
        loop_offset = 0.0
        while not self.stop_flag.is_set():
            cap = cv2.VideoCapture(self.source)
            if not cap.isOpened():
                time.sleep(3)
                continue
            src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            step = max(1, round(src_fps / self.proc.cfg.analysis_fps))
            is_file = not self.source.startswith(("rtsp://", "http://", "https://"))
            idx, t_start, video_t = 0, time.time(), 0.0
            inflight: deque = deque()
            replay = self._replay() if is_file else None
            while not self.stop_flag.is_set():
                ok = cap.grab()
                if not ok:
                    break
                idx += 1
                if idx % step:
                    continue
                ok, frame = cap.retrieve()
                if not ok:
                    break
                video_t = idx / src_fps
                if is_file:                                     # держим темп реального времени
                    delay = t_start + video_t - time.time()
                    if delay > 0:
                        time.sleep(delay)
                if frame.shape[1] > 1280:
                    scale = 1280 / frame.shape[1]
                    frame = cv2.resize(frame, (1280, round(frame.shape[0] * scale)))
                if replay is not None and idx in replay:      # детекции этого кадра уже посчитаны на TPU LinQ
                    while inflight:
                        self._finish(inflight.popleft())
                    self._finish((frame, loop_offset + video_t, _Done([from_json(o) for o in replay[idx]])))
                    continue
                # до FRAMES_INFLIGHT кадров одновременно на ускорителе: пока TPU считает кадр N, уже летят N+1…;
                # трекер и правила получают результаты строго по порядку кадров
                inflight.append((frame, loop_offset + video_t, self._infer.submit(self.proc.pipeline.detect, frame)))
                while len(inflight) >= FRAMES_INFLIGHT or (inflight and inflight[0][2].done()):
                    self._finish(inflight.popleft())
            while inflight:
                self._finish(inflight.popleft())
            cap.release()
            loop_offset += video_t + 1.0
            if not self.proc.cfg.loop and is_file:
                break
            if is_file:
                self.proc.new_pass(loop_offset)
