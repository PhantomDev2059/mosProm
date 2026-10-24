"""«Разбор смены»: длинная запись → короткие ролики нарушений по категориям, отсортированные по тяжести и уверенности.

Запись обрабатывается быстрее реального времени (без паузы между кадрами), тем же конвейером, что и живые камеры.
Каждое подтверждённое нарушение становится роликом: несколько секунд до начала, всё нарушение (до 20 с) и хвост.
Ответственный в конце смены просматривает ролики по порядку, подтверждает или отклоняет, отмечает сотрудника
и выгружает отчёт.
"""
import json
import sqlite3
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from engine.config import parse_camera
from engine.detector import Detector
from engine.events import Event, EventManager
from engine.pipeline import CameraPipeline
from engine.render import draw

SEVERITY_WEIGHT = {"critical": 3.0, "high": 2.0, "medium": 1.0}
PRE_S, POST_S, MAX_CLIP_S = 3.0, 2.0, 20.0

PRESETS = {
    "universal": {
        "title": "Все нарушения",
        "models": ["pose", "ppe2", "ppe", "coco", "forklift", "fire", "cigarette"],
        "rules": ["fall", "immobile", "no_helmet", "no_vest", "vehicle_proximity", "running", "phone", "smoking",
                  "open_fire", "smoke"],
    },
    "site": {"title": "Стройплощадка", "models": ["pose", "ppe2", "ppe"], "rules": ["no_helmet", "no_vest", "fall", "immobile"]},
    "warehouse": {"title": "Склад", "models": ["pose", "ppe", "forklift"],
                  "rules": ["vehicle_proximity", "no_vest", "running", "fall", "immobile"]},
    "shop": {"title": "Цех", "models": ["pose", "ppe2", "coco"], "rules": ["no_helmet", "running", "phone", "fall", "immobile"]},
    "outdoor": {"title": "Открытая площадка", "models": ["pose", "fire", "cigarette"], "rules": ["open_fire", "smoke", "smoking"]},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS review_jobs (
    id TEXT PRIMARY KEY, title TEXT, source TEXT, preset TEXT, status TEXT, progress REAL,
    video_s REAL, processed_s REAL, created REAL, finished REAL, error TEXT
);
CREATE TABLE IF NOT EXISTS review_items (
    id TEXT PRIMARY KEY, job TEXT, type TEXT, title TEXT, severity TEXT, start_s REAL, end_s REAL,
    score REAL, rank REAL, clip TEXT, snapshot TEXT, details TEXT,
    verdict TEXT DEFAULT 'pending', person TEXT, note TEXT
);
CREATE INDEX IF NOT EXISTS review_items_job ON review_items(job);
"""


class ReviewStore:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def exec(self, sql: str, args=()) -> None:
        with self.lock:
            self.db.execute(sql, args)
            self.db.commit()

    def rows(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]


class ReviewJob(threading.Thread):
    def __init__(self, job_id: str, source: Path, preset: str, detectors: dict[str, Detector], store: ReviewStore,
                 media: Path, analysis_fps: float = 6.0):
        super().__init__(daemon=True, name=f"review-{job_id}")
        self.job_id, self.source, self.preset = job_id, source, PRESETS[preset]
        self.store, self.media = store, media
        self.analysis_fps = analysis_fps
        cfg = parse_camera({"camera": f"review-{job_id}", "title": "Разбор смены", "source": str(source),
                            "analysis_fps": analysis_fps, "rules": [{"type": r} for r in self.preset["rules"]]})
        self.cfg = cfg
        models = [detectors[m] for m in self.preset["models"] if m in detectors]
        self.events = EventManager(cfg.id, on_open=self._on_open, on_update=self._on_update, on_close=self._on_close,
                                   cooldown_s=30.0)
        self.pipeline = CameraPipeline(cfg, models, self.events)
        self.ring: deque = deque(maxlen=int(analysis_fps * PRE_S))
        self.recording: dict[str, dict] = {}
        self._frame: np.ndarray | None = None
        self._jpg: bytes | None = None
        self._res = None
        self._now = 0.0

    # --- события ---
    def _on_open(self, ev: Event, det) -> None:
        self.recording[ev.id] = {"ev": ev, "frames": list(self.ring), "best": None, "snap_now": True,
                                 "until": ev.started_at + MAX_CLIP_S}

    def _on_update(self, ev: Event, det) -> None:
        rec = self.recording.get(ev.id)
        if rec is not None:
            rec["snap_now"] = True                  # уверенность выросла — снимок возьмём из текущего кадра

    def _on_close(self, ev: Event) -> None:
        rec = self.recording.get(ev.id)
        if rec is not None:
            rec["until"] = min(rec["until"], self._now + POST_S)

    def _finalize(self, eid: str, rec: dict) -> None:
        ev: Event = rec["ev"]
        clip_name, snap_name = f"rv-{eid}.mp4", f"rv-{eid}.jpg"
        if rec["best"] is not None:
            (self.media / snap_name).write_bytes(rec["best"])
        elif rec["frames"]:
            (self.media / snap_name).write_bytes(rec["frames"][len(rec["frames"]) // 2])
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", str(round(self.analysis_fps)),
                        "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                        "-movflags", "+faststart", str(self.media / clip_name)],
                       input=b"".join(rec["frames"]), capture_output=True)
        end = ev.closed_at if ev.closed_at is not None else ev.last_signal
        duration = max(0.0, end - ev.started_at)
        rank = SEVERITY_WEIGHT.get(ev.severity, 1.0) * ev.score * (1.0 + min(duration, MAX_CLIP_S) / MAX_CLIP_S)
        self.store.exec(
            "INSERT OR REPLACE INTO review_items (id, job, type, title, severity, start_s, end_s, score, rank, clip, snapshot, details)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, self.job_id, ev.type, ev.title, ev.severity, round(ev.started_at, 2), round(end, 2), round(ev.score, 3),
             round(rank, 3), f"/media/{clip_name}", f"/media/{snap_name}", json.dumps(ev.details, ensure_ascii=False, default=float)))

    # --- обработка ---
    def run(self) -> None:
        cap = cv2.VideoCapture(str(self.source))
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        video_s = total / src_fps
        step = max(1, round(src_fps / self.analysis_fps))
        self.store.exec("UPDATE review_jobs SET status='running', video_s=? WHERE id=?", (round(video_s, 1), self.job_id))
        t0, idx = time.time(), 0
        try:
            while True:
                if not cap.grab():
                    break
                idx += 1
                if idx % step:
                    continue
                ok, frame = cap.retrieve()
                if not ok:
                    break
                if frame.shape[1] > 1280:
                    frame = cv2.resize(frame, (1280, round(frame.shape[0] * 1280 / frame.shape[1])))
                self._now = idx / src_fps
                res = self.pipeline.process(frame, self._now)
                annotated = draw(frame, self.cfg.scene, res)
                small = cv2.resize(annotated, (960, round(annotated.shape[0] * 960 / annotated.shape[1])))
                jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 78])[1].tobytes()
                self.ring.append(jpg)
                for eid, rec in list(self.recording.items()):
                    rec["frames"].append(jpg)
                    if rec["snap_now"]:
                        rec["best"], rec["snap_now"] = jpg, False
                    if self._now >= rec["until"]:
                        del self.recording[eid]
                        self._finalize(eid, rec)
                if idx % (step * 25) == 0:
                    self.store.exec("UPDATE review_jobs SET progress=?, processed_s=? WHERE id=?",
                                    (round(idx / total, 4), round(time.time() - t0, 1), self.job_id))
            self.events.tick(self._now + 3600, lambda tid: 1e9)       # закрыть всё, что осталось открытым
            for eid, rec in list(self.recording.items()):
                del self.recording[eid]
                self._finalize(eid, rec)
            self.store.exec("UPDATE review_jobs SET status='done', progress=1, processed_s=?, finished=? WHERE id=?",
                            (round(time.time() - t0, 1), time.time(), self.job_id))
        except Exception as e:                                         # статус ошибки виден в интерфейсе
            self.store.exec("UPDATE review_jobs SET status='error', error=? WHERE id=?", (str(e)[:500], self.job_id))
            raise
        finally:
            cap.release()


class ReviewManager:
    def __init__(self, db_path: Path, media: Path, videos: Path, uploads: Path, detectors: dict[str, Detector]):
        self.store = ReviewStore(db_path)
        self.media, self.videos, self.uploads = media, videos, uploads
        self.detectors = detectors
        self.jobs: dict[str, ReviewJob] = {}
        uploads.mkdir(parents=True, exist_ok=True)
        # задачи, прерванные перезапуском сервиса, помечаем, чтобы не висели «в работе»
        self.store.exec("UPDATE review_jobs SET status='error', error='прервано перезапуском' WHERE status IN ('queued','running')")

    def sources(self) -> list[dict]:
        out = []
        for p in sorted(list(self.videos.glob("*.mp4")) + list(self.uploads.glob("*.mp4"))):
            cap = cv2.VideoCapture(str(p))
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            dur = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / fps
            cap.release()
            out.append({"path": str(p), "name": p.name, "duration_s": round(dur, 1),
                        "uploaded": p.parent == self.uploads})
        return out

    def start(self, source: str, preset: str, title: str | None = None) -> str:
        path = Path(source)
        if not path.exists() or path.parent not in (self.videos, self.uploads):
            raise ValueError("нет такой записи")
        if preset not in PRESETS:
            raise ValueError(f"профиль должен быть одним из {list(PRESETS)}")
        job_id = uuid.uuid4().hex[:8]
        self.store.exec("INSERT INTO review_jobs (id, title, source, preset, status, progress, created) VALUES (?,?,?,?,?,?,?)",
                        (job_id, title or path.stem, path.name, preset, "queued", 0.0, time.time()))
        job = ReviewJob(job_id, path, preset, self.detectors, self.store, self.media)
        self.jobs[job_id] = job
        job.start()
        return job_id

    def jobs_list(self) -> list[dict]:
        rows = self.store.rows("SELECT * FROM review_jobs ORDER BY created DESC")
        counts = {r["job"]: r for r in self.store.rows(
            "SELECT job, COUNT(*) AS n, SUM(verdict!='pending') AS reviewed, SUM(verdict='confirmed') AS confirmed "
            "FROM review_items GROUP BY job")}
        for r in rows:
            c = counts.get(r["id"], {})
            r.update(items=c.get("n", 0), reviewed=c.get("reviewed", 0) or 0, confirmed=c.get("confirmed", 0) or 0,
                     preset_title=PRESETS.get(r["preset"], {}).get("title", r["preset"]))
        return rows

    def items(self, job_id: str) -> list[dict]:
        rows = self.store.rows("SELECT * FROM review_items WHERE job=? ORDER BY rank DESC", (job_id,))
        for r in rows:
            r["details"] = json.loads(r["details"] or "{}")
        return rows

    def delete(self, job_id: str) -> bool:
        if job_id in self.jobs and self.jobs[job_id].is_alive():
            raise ValueError("разбор ещё идёт — дождитесь окончания")
        items = self.store.rows("SELECT clip, snapshot FROM review_items WHERE job=?", (job_id,))
        for r in items:
            for url in (r["clip"], r["snapshot"]):
                if url:
                    (self.media / Path(url).name).unlink(missing_ok=True)
        self.store.exec("DELETE FROM review_items WHERE job=?", (job_id,))
        self.store.exec("DELETE FROM review_jobs WHERE id=?", (job_id,))
        self.jobs.pop(job_id, None)
        return True

    def set_verdict(self, item_id: str, verdict: str | None, person: str | None, note: str | None) -> dict | None:
        if verdict is not None and verdict not in ("pending", "confirmed", "rejected"):
            raise ValueError("вердикт: pending, confirmed или rejected")
        fields, args = [], []
        for col, val in (("verdict", verdict), ("person", person), ("note", note)):
            if val is not None:
                fields.append(f"{col}=?")
                args.append(val.strip() if col != "verdict" else val)
        if fields:
            self.store.exec(f"UPDATE review_items SET {', '.join(fields)} WHERE id=?", (*args, item_id))
        rows = self.store.rows("SELECT * FROM review_items WHERE id=?", (item_id,))
        return rows[0] if rows else None
