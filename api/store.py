"""Журнал событий в SQLite: доказательная база для службы ОТ и источник статистики."""
import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    camera TEXT, camera_title TEXT, zone TEXT, zone_title TEXT,
    type TEXT, title TEXT, severity TEXT, track_id INTEGER,
    started_at REAL, confirmed_at REAL, duration_s REAL, latency_s REAL,
    score REAL, details TEXT, snapshot TEXT, clip TEXT,
    status TEXT DEFAULT 'new', history TEXT DEFAULT '[]', closed INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_time ON events(started_at);
"""
STATUSES = ("new", "confirmed", "false", "sent_to_master", "resolved")


class EventStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(events)")}
        if "clip" not in cols:                       # журнал старой версии
            self.db.execute("ALTER TABLE events ADD COLUMN clip TEXT")
        self.lock = threading.Lock()

    def insert(self, row: dict) -> None:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        vals = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in row.values()]
        with self.lock:
            self.db.execute(f"INSERT OR REPLACE INTO events ({cols}) VALUES ({marks})", vals)
            self.db.commit()

    def update(self, event_id: str, **fields) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in fields.values()]
        with self.lock:
            self.db.execute(f"UPDATE events SET {sets} WHERE id = ?", [*vals, event_id])
            self.db.commit()

    def set_status(self, event_id: str, status: str, who: str = "оператор") -> dict | None:
        if status not in STATUSES:
            raise ValueError(f"статус должен быть одним из {STATUSES}")
        row = self.get(event_id)
        if row is None:
            return None
        history = row["history"] + [{"status": status, "at": time.time(), "who": who}]
        self.update(event_id, status=status, history=history)
        return self.get(event_id)

    def get(self, event_id: str) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM events WHERE id = ?", [event_id]).fetchone()
        return _row(r) if r else None

    def query(self, type_: str | None = None, camera: str | None = None, severity: str | None = None,
              status: str | None = None, since: float | None = None, until: float | None = None,
              limit: int = 500) -> list[dict]:
        where, args = [], []
        for col, val in (("type", type_), ("camera", camera), ("severity", severity), ("status", status)):
            if val:
                where.append(f"{col} = ?")
                args.append(val)
        if since:
            where.append("started_at >= ?")
            args.append(since)
        if until:
            where.append("started_at <= ?")
            args.append(until)
        sql = "SELECT * FROM events" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY started_at DESC LIMIT ?"
        with self.lock:
            rows = self.db.execute(sql, [*args, limit]).fetchall()
        return [_row(r) for r in rows]

    def stats(self, since: float) -> dict:
        rows = self.query(since=since, limit=100000)
        by_type, by_camera, by_status, by_severity = {}, {}, {}, {}
        heat = [[0] * 24 for _ in range(7)]
        latencies = []
        for r in rows:
            by_type[r["title"]] = by_type.get(r["title"], 0) + 1
            by_camera[r["camera_title"]] = by_camera.get(r["camera_title"], 0) + 1
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
            by_severity[r["severity"]] = by_severity.get(r["severity"], 0) + 1
            dt = datetime.fromtimestamp(r["started_at"])
            heat[dt.weekday()][dt.hour] += 1
            if r["latency_s"] is not None:
                latencies.append(r["latency_s"])
        marked = by_status.get("confirmed", 0) + by_status.get("resolved", 0) + by_status.get("sent_to_master", 0)
        false = by_status.get("false", 0)
        latencies.sort()
        return {
            "total": len(rows), "by_type": by_type, "by_camera": by_camera, "by_status": by_status,
            "by_severity": by_severity, "heat": heat,
            "precision": round(marked / (marked + false), 3) if marked + false else None,
            "latency_p50": latencies[len(latencies) // 2] if latencies else None,
        }


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["details"] = json.loads(d["details"] or "{}")
    d["history"] = json.loads(d["history"] or "[]")
    d["closed"] = bool(d["closed"])
    return d
