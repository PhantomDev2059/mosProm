"""Кто нарушил: группировка нарушителей по лицу в «сотрудников».

submit() вызывается при открытии события и ничего не ждёт: кадр уходит в очередь,
отдельный поток ищет лицо, сравнивает с известными людьми и либо привязывает
событие к найденному человеку, либо заводит нового «Сотрудник №k».
Правила (подобраны на 7 демо-камерах, см. engine/faces.py):
- совпадение — косинус со средним дескриптором человека ≥ 0.363 (порог SFace; ниже — путаница людей);
- нового человека заводим только по хорошему лицу (крупное, уверенное, резкое), плохое лицо
  может лишь совпасть с уже известным — так нет мусорных профилей;
- два профиля одного человека (анфас и профиль завелись раньше, чем «срослись») сливаются
  автоматически: средние ≥ 0.5, или ≥ 0.4, если оба видны на одной камере;
- фото профиля — лицо с лучшим качеством (крупное, резкое, анфас) среди уверенных совпадений.
submit() по одному событию можно звать несколько раз (на открытии и позже): событие без лица
получит человека, как только лицо появится; уже опознанное — только уточняет профиль.
Без моделей лиц индекс выключен: submit() ничего не делает, чтение отдаёт пустое.
"""
import logging
import queue
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import cv2
import numpy as np

from engine.faces import MATCH_THRESHOLD, FaceEngine

log = logging.getLogger("linqguard.people")

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY, name TEXT, photo TEXT, embedding BLOB,
    n INTEGER, quality REAL, created REAL
);
CREATE TABLE IF NOT EXISTS event_people (
    event_id TEXT PRIMARY KEY, person_id TEXT, similarity REAL, created REAL
);
CREATE INDEX IF NOT EXISTS event_people_person ON event_people(person_id);
CREATE TABLE IF NOT EXISTS people_alias (src TEXT PRIMARY KEY, dst TEXT);
"""
PHOTO_SIDE = 160
MEAN_WINDOW = 50          # средний дескриптор «помнит» примерно столько последних лиц
MERGE_SIM = 0.50          # автослияние профилей: близость средних
MERGE_SIM_CAM = 0.40      # … или ниже, если оба человека видны на одной камере (0.3 уже путает людей)
PHOTO_MIN_SIM = 0.50      # фото профиля меняем только на уверенно «своё» лицо: пограничное совпадение
                          # (чужой человек в профиль) не должно стать фотографией, по которой ищут себя
AUTO_NAME = re.compile(r"^Сотрудник №\d+$")


def _norm(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, np.float32).ravel()
    return v / (np.linalg.norm(v) + 1e-9)


def camera_of(event_id: str) -> str:
    """Камера из id события вида «cam-03-shop-ce51e0ff»."""
    return event_id.rsplit("-", 1)[0] if "-" in event_id else ""


class PeopleIndex:
    def __init__(self, db_path: Path, media: Path, models_dir: Path, engine=None,
                 threshold: float = MATCH_THRESHOLD, queue_size: int = 32):
        self.media = Path(media)
        self.threshold = threshold
        self.engine = engine
        if self.engine is None:
            if FaceEngine.available(models_dir):
                self.engine = FaceEngine(models_dir)
            else:
                log.warning("модели лиц не найдены в %s — опознание сотрудников выключено "
                            "(tools/fetch_face_models.sh)", models_dir)
        self.lock = threading.Lock()
        self.q: queue.Queue = queue.Queue(maxsize=queue_size)
        self.db = None
        if not self.enabled:
            return
        self.media.mkdir(parents=True, exist_ok=True)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(event_people)")}
        for col, typ in (("camera", "TEXT"), ("quality", "REAL")):      # база прошлой версии
            if col not in cols:
                self.db.execute(f"ALTER TABLE event_people ADD COLUMN {col} {typ}")
        self.db.commit()
        threading.Thread(target=self._worker, name="people", daemon=True).start()

    @property
    def enabled(self) -> bool:
        return self.engine is not None

    # ---------- запись ----------

    def submit(self, event_id: str, frame: np.ndarray, person_box, camera: str | None = None) -> None:
        """Поставить в очередь опознание человека события; не блокирует, при переполнении молча пропускает."""
        if not self.enabled or frame is None:
            return
        box = np.asarray(person_box, np.float32).ravel()[:4].copy()
        try:
            self.q.put_nowait((event_id, frame.copy(), box, camera or camera_of(event_id)))
        except queue.Full:
            pass

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Дождаться, пока очередь разобрана (для тестов)."""
        deadline = time.monotonic() + timeout
        while self.q.unfinished_tasks:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.005)
        return True

    def _worker(self) -> None:
        while True:
            eid, frame, box, cam = self.q.get()
            try:
                self._process(eid, frame, box, cam)
            except Exception:
                log.exception("опознание по событию %s не удалось", eid)
            finally:
                self.q.task_done()

    def _face(self, frame, box) -> dict | None:
        if hasattr(self.engine, "analyze"):
            return self.engine.analyze(frame, box)
        r = self.engine.embed(frame, box)
        return None if r is None else {"emb": r[0], "photo": r[1], "quality": r[2], "good": True}

    def _process(self, event_id: str, frame: np.ndarray, box: np.ndarray, camera: str) -> None:
        f = self._face(frame, box)
        if f is None:
            return
        emb, crop, quality = _norm(f["emb"]), f["photo"], float(f["quality"])
        now = time.time()
        with self.lock:
            prev = self.db.execute("SELECT person_id, quality FROM event_people WHERE event_id = ?",
                                   (event_id,)).fetchone()
            rows = self.db.execute("SELECT id, embedding, n, quality FROM people").fetchall()
            best, best_sim = None, -1.0
            for r in rows:
                sim = float(np.frombuffer(r["embedding"], np.float32) @ emb)
                if sim > best_sim:
                    best, best_sim = r, sim
            matched = best is not None and best_sim >= self.threshold
            if prev is not None:
                # событие уже опознано: новое лицо лишь уточняет того же человека
                if matched and best["id"] == prev["person_id"]:
                    self._absorb(best, emb, crop, quality, best_sim, count=False)
                    if quality > (prev["quality"] or 0.0):
                        self.db.execute("UPDATE event_people SET similarity = ?, quality = ? WHERE event_id = ?",
                                        (best_sim, quality, event_id))
                    self.db.commit()
                return
            if matched:
                pid, sim = best["id"], best_sim
                self._absorb(best, emb, crop, quality, best_sim, count=True)
            elif f.get("good", True):
                pid, sim = uuid.uuid4().hex[:12], 1.0
                photo = self._save_photo(pid, crop)
                self.db.execute("INSERT INTO people (id, name, photo, embedding, n, quality, created) "
                                "VALUES (?, ?, ?, ?, 1, ?, ?)",
                                (pid, self._new_name(), photo, emb.tobytes(), quality, now))
            else:
                return                                  # мелкое/смазанное лицо незнакомца — профиль не заводим
            self.db.execute("INSERT OR REPLACE INTO event_people (event_id, person_id, similarity, created, camera, "
                            "quality) VALUES (?, ?, ?, ?, ?, ?)", (event_id, pid, sim, now, camera, quality))
            self._auto_merge(pid)
            self.db.commit()

    def _absorb(self, row, emb: np.ndarray, crop: np.ndarray, quality: float, sim: float, count: bool) -> None:
        """Добавить лицо к человеку: сдвинуть средний дескриптор, при лучшем качестве — сменить фото."""
        n = row["n"]
        mean = _norm(np.frombuffer(row["embedding"], np.float32) * min(n, MEAN_WINDOW) + emb)
        self.db.execute("UPDATE people SET embedding = ?, n = ? WHERE id = ?",
                        (mean.tobytes(), n + 1 if count else n, row["id"]))
        if quality > (row["quality"] or 0.0) and sim >= PHOTO_MIN_SIM:
            self._save_photo(row["id"], crop)
            self.db.execute("UPDATE people SET quality = ? WHERE id = ?", (quality, row["id"]))

    def _auto_merge(self, pid: str) -> None:
        """Слить профиль с другим профилем того же человека (под замком)."""
        rows = {r["id"]: r for r in self.db.execute("SELECT id, embedding, created FROM people")}
        if pid not in rows:
            return
        me = np.frombuffer(rows[pid]["embedding"], np.float32)
        cams = {r[0] for r in self.db.execute("SELECT DISTINCT camera FROM event_people WHERE person_id = ?", (pid,))}
        best, margin = None, 0.0
        for oid, r in rows.items():
            if oid == pid:
                continue
            sim = float(np.frombuffer(r["embedding"], np.float32) @ me)
            if sim < MERGE_SIM_CAM:
                continue
            shared = cams & {c[0] for c in self.db.execute(
                "SELECT DISTINCT camera FROM event_people WHERE person_id = ?", (oid,))} - {None, ""}
            thr = MERGE_SIM_CAM if shared else MERGE_SIM
            if sim >= thr and sim - thr >= margin:
                best, margin = oid, sim - thr
        if best is not None:
            src, dst = sorted((pid, best), key=lambda i: rows[i]["created"], reverse=True)   # старший остаётся
            log.info("профили %s и %s — один человек, сливаю", src, dst)
            self._merge(src, dst)

    def _new_name(self) -> str:
        names = {r[0] for r in self.db.execute("SELECT name FROM people")}
        k = len(names) + 1
        while f"Сотрудник №{k}" in names:              # после слияний номера могли сдвинуться
            k += 1
        return f"Сотрудник №{k}"

    def _save_photo(self, pid: str, crop: np.ndarray) -> str:
        name = f"face-{pid}.jpg"
        if crop is not None and crop.size:
            img = cv2.resize(crop, (PHOTO_SIDE, PHOTO_SIDE), interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(self.media / name), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return f"/media/{name}"

    # ---------- чтение и правка ----------

    def _resolve(self, pid: str) -> str:
        """id после слияний: старые id (например, привязанные в Telegram) ведут к живому профилю."""
        for _ in range(16):
            r = self.db.execute("SELECT dst FROM people_alias WHERE src = ?", (pid,)).fetchone()
            if r is None:
                break
            pid = r[0]
        return pid

    def person_of(self, event_id: str) -> dict | None:
        if not self.enabled:
            return None
        with self.lock:
            r = self.db.execute("SELECT p.id, p.name, p.photo FROM event_people e JOIN people p ON p.id = e.person_id "
                                "WHERE e.event_id = ?", (event_id,)).fetchone()
        return dict(r) if r else None

    def people(self) -> list[dict]:
        if not self.enabled:
            return []
        with self.lock:
            rows = self.db.execute("SELECT id, name, photo, created, n FROM people ORDER BY created").fetchall()
        return [dict(r) for r in rows]

    def get(self, pid: str) -> dict | None:
        if not self.enabled:
            return None
        with self.lock:
            r = self.db.execute("SELECT id, name, photo, created, n, quality FROM people WHERE id = ?",
                                (self._resolve(pid),)).fetchone()
        return dict(r) if r else None

    def events_of(self, pid: str) -> list[str]:
        if not self.enabled:
            return []
        with self.lock:
            rows = self.db.execute("SELECT event_id FROM event_people WHERE person_id = ? ORDER BY created DESC",
                                   (self._resolve(pid),)).fetchall()
        return [r[0] for r in rows]

    def rename(self, pid: str, name: str) -> bool:
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
        if not self.enabled or not name:
            return False
        with self.lock:
            cur = self.db.execute("UPDATE people SET name = ? WHERE id = ?", (name, self._resolve(pid)))
            self.db.commit()
        return cur.rowcount > 0

    def merge(self, src_pid: str, dst_pid: str) -> bool:
        """Слить src в dst: события переходят к dst, дескрипторы усредняются, src удаляется
        (его id дальше ведёт к dst)."""
        if not self.enabled:
            return False
        with self.lock:
            ok = self._merge(self._resolve(src_pid), self._resolve(dst_pid))
            self.db.commit()
        return ok

    def _merge(self, src_pid: str, dst_pid: str) -> bool:
        if src_pid == dst_pid:
            return False
        src = self.db.execute("SELECT * FROM people WHERE id = ?", (src_pid,)).fetchone()
        dst = self.db.execute("SELECT * FROM people WHERE id = ?", (dst_pid,)).fetchone()
        if src is None or dst is None:
            return False
        ws, wd = min(src["n"], MEAN_WINDOW), min(dst["n"], MEAN_WINDOW)
        mean = _norm(np.frombuffer(src["embedding"], np.float32) * ws + np.frombuffer(dst["embedding"], np.float32) * wd)
        quality = dst["quality"] or 0.0
        src_file, dst_file = self.media / f"face-{src_pid}.jpg", self.media / f"face-{dst_pid}.jpg"
        if (src["quality"] or 0.0) > quality and src_file.is_file():
            src_file.replace(dst_file)                    # лучшее фото переходит к dst
            quality = src["quality"]
        else:
            src_file.unlink(missing_ok=True)
        # имя, данное человеком, важнее автоматического «Сотрудник №k»
        name = src["name"] if AUTO_NAME.match(dst["name"] or "") and not AUTO_NAME.match(src["name"] or "") else dst["name"]
        self.db.execute("UPDATE people SET embedding = ?, n = ?, quality = ?, name = ? WHERE id = ?",
                        (mean.tobytes(), src["n"] + dst["n"], quality, name, dst_pid))
        self.db.execute("UPDATE event_people SET person_id = ? WHERE person_id = ?", (dst_pid, src_pid))
        self.db.execute("UPDATE people_alias SET dst = ? WHERE dst = ?", (dst_pid, src_pid))
        self.db.execute("INSERT OR REPLACE INTO people_alias (src, dst) VALUES (?, ?)", (src_pid, dst_pid))
        self.db.execute("DELETE FROM people WHERE id = ?", (src_pid,))
        return True
