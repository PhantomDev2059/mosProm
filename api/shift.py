"""Смена: роли пользователей бота, тревоги с эскалацией руководителю, сводки и отчёты в конце смены.

Состояние — в data/shift.db (SQLite). Бот (api/telegram.py) подключается полем `bot`; без него всё работает,
просто сообщения никуда не уходят. Сотрудники по лицам — `people` (PeopleIndex, может быть None).

Переменные окружения:
- LINQ_SHIFTS — расписание смен по местному времени, по умолчанию «08:00-20:00,20:00-08:00»;
- LINQ_ALARM_SEVERITY — какие уровни поднимают тревогу начальникам смены (по умолчанию critical);
- LINQ_ESCALATE_MIN — через сколько минут неустранённая тревога уходит руководителю (по умолчанию 30,
  меняется через API и хранится в shift.db).
"""
import csv
import hashlib
import hmac
import html
import io
import json
import logging
import os
import re
import sqlite3
import statistics
import threading
import time
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

import yaml

from .store import EventStore

log = logging.getLogger("linqguard.shift")

ROLES = ("worker", "operator", "supervisor", "director")
ROLE_TITLES = {"worker": "Рабочий", "operator": "Оператор", "supervisor": "Начальник смены", "director": "Руководитель"}
BOSSES = ("supervisor", "director")                  # принимают и устраняют тревоги, закрывают смену
VIEWERS = ("operator", *BOSSES)                      # видят тревоги и весь журнал смены
SEVERITY_TITLES = {"critical": "критично", "high": "высокий", "medium": "средний"}
STATUS_TITLES = {"new": "новое", "confirmed": "подтверждено", "false": "ложное", "sent_to_master": "передано мастеру",
                 "resolved": "устранено"}

USERS_SQL = """CREATE TABLE IF NOT EXISTS users (
    tg_id INTEGER PRIMARY KEY, name TEXT,
    role TEXT CHECK (role IN ('worker', 'operator', 'supervisor', 'director')),
    person_id TEXT, demo INTEGER DEFAULT 1, chat_id INTEGER, created REAL
);"""
ALARM_COLS = {"camera": "TEXT", "source": "TEXT DEFAULT 'auto'", "source_by": "TEXT", "comment": "TEXT"}
SCHEMA = USERS_SQL + """
CREATE TABLE IF NOT EXISTS shifts (id TEXT, start REAL, end REAL, closed_by TEXT);
CREATE TABLE IF NOT EXISTS alarms (
    event_id TEXT PRIMARY KEY, created REAL, title TEXT, severity TEXT, camera_title TEXT, zone_title TEXT,
    snapshot TEXT, status TEXT DEFAULT 'open', ack_by TEXT, ack_at REAL, resolved_by TEXT, resolved_at REAL,
    note TEXT, escalated_at REAL, asked_at REAL,
    camera TEXT, source TEXT DEFAULT 'auto', source_by TEXT, comment TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS bot_messages (
    chat_id INTEGER, message_id INTEGER, kind TEXT, event_id TEXT, created REAL, expires REAL, photo INTEGER DEFAULT 0,
    PRIMARY KEY (chat_id, message_id)
);
"""
MSG_KINDS = ("alarm", "escalation", "aux", "menu", "report")      # виды сообщений бота в bot_messages


def hm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def camera_no(camera: str | None) -> str:
    """Номер камеры для людей: cam-03-shop → «К-03», live-<ts> → «Лайв», webcam → «Вебкамера»."""
    if not camera:
        return ""
    if camera == "webcam":
        return "Вебкамера"
    if camera.startswith("live-"):
        return "Лайв"
    m = re.match(r"cam-?(\d+)", camera)
    return f"К-{m.group(1)}" if m else camera


def cam_label(camera: str | None, title: str | None) -> str:
    """«К-03 · Цех»; без повтора, если название уже начинается с номера («Вебкамера», «Лайв 1: …»)."""
    no, title = camera_no(camera), title or ""
    return title if not no or title.startswith(no) else f"{no} · {title}" if title else no


def esc(v) -> str:
    """Текст для parse_mode=HTML: названия, имена и заметки экранируются всегда."""
    return html.escape(str(v if v is not None else ""), quote=False)


def render_card(name: str, *args) -> bytes | None:
    """Картинка из api/cards.py (её рисует отдельный модуль). Нет модуля, шрифтов или функция вернула None —
    None: бот шлёт как раньше (сырой кадр или текст)."""
    try:
        from . import cards
    except Exception:
        return None
    fn = getattr(cards, name, None)
    if fn is None:
        return None
    try:
        out = fn(*args)
    except Exception as e:
        log.warning("карточка %s не нарисована: %s", name, e)
        return None
    return bytes(out) if isinstance(out, (bytes, bytearray)) and out else None


def fmt_dur(s: float | None) -> str:
    if s is None:
        return "—"
    s = int(round(s))
    if s < 60:
        return f"{s} с"
    if s < 3600:
        return f"{s // 60} мин {s % 60:02d} с"
    return f"{s // 3600} ч {s % 3600 // 60:02d} мин"


def check_init_data(init_data: str, token: str, max_age: float = 86400, now: float | None = None) -> dict | None:
    """Проверка Telegram.WebApp.initData по документации Telegram; возвращает пользователя Telegram или None."""
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    got = fields.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    if not got or not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), got):
        return None
    try:
        if (time.time() if now is None else now) - int(fields.get("auth_date", 0)) > max_age:
            return None
        user = json.loads(fields["user"])
    except (KeyError, ValueError):
        return None
    return user if isinstance(user, dict) and "id" in user else None


def user_name(u: dict) -> str:
    return " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or u.get("username") or str(u["id"])


def parse_shifts(spec: str) -> list[tuple[int, int]]:
    """«08:00-20:00,20:00-08:00» → [(начало, длительность) в минутах]; смена может переходить через полночь."""
    out = []
    for part in spec.split(","):
        a, b = part.strip().split("-")
        start = int(a[:2]) * 60 + int(a[3:5])
        end = int(b[:2]) * 60 + int(b[3:5])
        out.append((start, (end - start) % 1440 or 1440))
    return out


class ShiftService:
    def __init__(self, store: EventStore, people, media: Path, data: Path, roles_file: Path):
        self.store, self.people, self.media, self.roles_file = store, people, media, roles_file
        self.bot = None                                  # TelegramBot, подключает install()
        self.clock = time.time                           # подменяется в тестах
        self.schedule = parse_shifts(os.environ.get("LINQ_SHIFTS", "08:00-20:00,20:00-08:00"))
        self.levels = set(os.environ.get("LINQ_ALARM_SEVERITY", "critical").split(","))
        data.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data / "shift.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        users_sql = self.db.execute("SELECT sql FROM sqlite_master WHERE name = 'users'").fetchone()[0]
        if "'operator'" not in users_sql:                # роль «Оператор» появилась позже: CHECK в SQLite не правится
            self.db.executescript("BEGIN; ALTER TABLE users RENAME TO users_v1; " + USERS_SQL +
                                  " INSERT INTO users SELECT * FROM users_v1; DROP TABLE users_v1; COMMIT;")
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(alarms)")}
        for col, decl in ALARM_COLS.items():             # shift.db прошлой версии
            if col not in cols:
                self.db.execute(f"ALTER TABLE alarms ADD COLUMN {col} {decl}")
        self.db.commit()
        self.lock = threading.RLock()
        self._roles: tuple[float, dict] = (-1.0, {})
        self._watch_end: float | None = None

    # ---------- база ----------
    def _q(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def _x(self, sql: str, args=()) -> None:
        with self.lock:
            self.db.execute(sql, args)
            self.db.commit()

    def _setting(self, key: str, default=None):
        r = self._q("SELECT value FROM settings WHERE key = ?", [key])
        return r[0]["value"] if r else default

    @property
    def escalate_min(self) -> float:
        return float(self._setting("escalate_min", os.environ.get("LINQ_ESCALATE_MIN", "30")))

    def settings(self) -> dict:
        return {"escalate_min": self.escalate_min, "alarm_severity": sorted(self.levels), "demo": self.demo,
                "shifts": [f"{s // 60:02d}:{s % 60:02d}-{(s + d) % 1440 // 60:02d}:{(s + d) % 60:02d}"
                           for s, d in self.schedule]}

    def set_escalate_min(self, minutes: float) -> dict:
        if not 0.1 <= float(minutes) <= 1440:
            raise ValueError("escalate_min должен быть от 0.1 до 1440 минут")
        self._x("INSERT OR REPLACE INTO settings (key, value) VALUES ('escalate_min', ?)", [str(float(minutes))])
        return self.settings()

    # ---------- пользователи и роли ----------
    def _roles_cfg(self) -> dict:
        """configs/roles.yaml перечитывается при изменении файла."""
        try:
            mtime = self.roles_file.stat().st_mtime
        except OSError:
            return {"demo": True, "users": {}}
        if mtime != self._roles[0]:
            raw = yaml.safe_load(self.roles_file.read_text(encoding="utf-8")) or {}
            users = {int(k): v or {} for k, v in (raw.get("users") or {}).items()}
            self._roles = (mtime, {"demo": bool(raw.get("demo", False)), "users": users})
        return self._roles[1]

    @property
    def demo(self) -> bool:
        return self._roles_cfg()["demo"]

    def user(self, tg_id: int) -> dict | None:
        r = self._q("SELECT * FROM users WHERE tg_id = ?", [tg_id])
        return r[0] if r else None

    def resolve_user(self, tg_id: int, name: str = "", chat_id: int | None = None) -> dict | None:
        """Пользователь бота или мини-приложения: роль из roles.yaml, иначе (в демо-режиме) выбирает сам."""
        cfg = self._roles_cfg()
        fixed = cfg["users"].get(int(tg_id))
        if fixed is None and not cfg["demo"]:
            return None
        u = self.user(tg_id)
        if u is None:
            self._x("INSERT INTO users (tg_id, name, role, demo, chat_id, created) VALUES (?, ?, NULL, 1, ?, ?)",
                    [tg_id, name or str(tg_id), chat_id, self.clock()])
        elif chat_id and chat_id != u["chat_id"]:
            self._x("UPDATE users SET chat_id = ? WHERE tg_id = ?", [chat_id, tg_id])
        if fixed is not None:
            role = fixed.get("role")
            if role not in ROLES:
                raise ValueError(f"roles.yaml: у {tg_id} роль должна быть одной из {ROLES}")
            self._x("UPDATE users SET role = ?, demo = 0, name = ? WHERE tg_id = ?",
                    [role, fixed.get("name") or name or (u or {}).get("name") or str(tg_id), tg_id])
        elif u is not None and u["demo"] == 0:           # убрали из roles.yaml — снова демо-пользователь
            self._x("UPDATE users SET demo = 1 WHERE tg_id = ?", [tg_id])
        return self.user(tg_id)

    def demo_user(self, key: str = "demo", role: str | None = None) -> dict:
        """Демо-вход в мини-приложение без Telegram: отрицательный id из ключа, роль из заголовка."""
        tg_id = -(zlib.crc32(key.encode()) & 0x7FFFFFFF) - 1
        u = self.user(tg_id)
        if u is None:
            self._x("INSERT INTO users (tg_id, name, role, demo, created) VALUES (?, ?, NULL, 1, ?)",
                    [tg_id, "Демо-пользователь" if key == "demo" else f"Демо: {key}", self.clock()])
        if role in ROLES:
            self._x("UPDATE users SET role = ? WHERE tg_id = ?", [role, tg_id])
        return self.user(tg_id)

    def user_from_request(self, request) -> dict | None:
        """Пользователь мини-приложения по заголовкам: X-Tg-Init-Data (подпись Telegram), иначе в демо-режиме —
        X-Demo-User / X-Demo-Role. None — доступа нет."""
        h = request.headers
        # ссылка на CSV из Telegram (openLink) не несёт заголовков — подпись приходит параметром ?tg_init=
        init = h.get("x-tg-init-data") or request.query_params.get("tg_init")
        token = getattr(self.bot, "token", "")
        if init and token:
            tu = check_init_data(init, token)
            if tu is not None:
                return self.resolve_user(int(tu["id"]), user_name(tu))
        if self.demo:
            return self.demo_user(h.get("x-demo-user") or "demo", h.get("x-demo-role"))
        return None

    def set_role(self, tg_id: int, role: str) -> dict:
        if role not in ROLES:
            raise ValueError(f"роль должна быть одной из {ROLES}")
        u = self.user(tg_id)
        if u is None or not u["demo"] or not self.demo:
            raise PermissionError("роль назначает администратор в configs/roles.yaml")
        self._x("UPDATE users SET role = ? WHERE tg_id = ?", [role, tg_id])
        return self.user(tg_id)

    def bind(self, tg_id: int, person_id: str | None) -> dict:
        if person_id and self.people is not None and self.people.get(person_id) is None:
            raise KeyError("нет такого сотрудника")
        self._x("UPDATE users SET person_id = ? WHERE tg_id = ?", [person_id or None, tg_id])
        return self.user(tg_id)

    def chats(self, *roles: str) -> list[int]:
        marks = ", ".join("?" for _ in roles)
        return [r["chat_id"] for r in self._q(
            f"SELECT chat_id FROM users WHERE role IN ({marks}) AND chat_id IS NOT NULL", roles)]

    def chat_roles(self) -> dict[int, str]:
        return {r["chat_id"]: r["role"] for r in self._q("SELECT chat_id, role FROM users WHERE chat_id IS NOT NULL")}

    def chat_users(self) -> list[dict]:
        return self._q("SELECT * FROM users WHERE chat_id IS NOT NULL")

    def names(self, role: str) -> list[str]:
        return [r["name"] for r in self._q("SELECT name FROM users WHERE role = ? AND chat_id IS NOT NULL", [role])]

    def me(self, u: dict) -> dict:
        return {"tg_id": u["tg_id"], "name": u["name"], "role": u["role"], "role_title": ROLE_TITLES.get(u["role"]),
                "demo": bool(u["demo"]), "person": self._person(u["person_id"]), "escalate_min": self.escalate_min,
                "shift": self.current_shift(),
                "bot": {"enabled": bool(self.bot and self.bot.enabled), "username": getattr(self.bot, "username", None)}}

    # ---------- смены ----------
    def scheduled_shift(self, now: float | None = None) -> dict:
        now = self.clock() if now is None else now
        t = datetime.fromtimestamp(now)
        midnight = t.replace(hour=0, minute=0, second=0, microsecond=0)
        for days in (0, -1):
            for start_m, dur_m in self.schedule:
                start = midnight + timedelta(days=days, minutes=start_m)
                end = start + timedelta(minutes=dur_m)
                if start <= t < end:
                    part = "день" if 6 * 60 <= start_m < 18 * 60 else "ночь"
                    return {"id": f"{start:%d.%m} {part}", "start": start.timestamp(), "end": end.timestamp()}
        # расписание с дырой: отсчёт от полуночи до ближайшего начала смены
        return {"id": f"{t:%d.%m} вне смены", "start": midnight.timestamp(), "end": (midnight + timedelta(days=1)).timestamp()}

    def current_shift(self, now: float | None = None) -> dict:
        """Текущая смена: с начала по расписанию или с последнего ручного закрытия, что позже."""
        now = self.clock() if now is None else now
        sh = self.scheduled_shift(now)
        last = self._q("SELECT MAX(end) AS e FROM shifts WHERE end <= ?", [now])[0]["e"]
        if last and last > sh["start"]:
            sh = {**sh, "id": f"{sh['id']} с {hm(last)}", "start": last}
        return sh

    def close_shift(self, who: str = "авто", now: float | None = None) -> int:
        """Закрывает смену и рассылает отчёты; новая смена начинается с этого момента. Возвращает число сообщений."""
        now = self.clock() if now is None else now
        sh = self.current_shift(now)
        self._x("INSERT INTO shifts (id, start, end, closed_by) VALUES (?, ?, ?, ?)", [sh["id"], sh["start"], now, who])
        self.drop_alarm_msgs(before=now)                 # карточки тревог смены заменяет отчёт
        return self._reports({**sh, "end": now})

    def check_shift_end(self, now: float | None = None) -> int:
        """Раз в минуту из фона: на конце смены по расписанию — автоматическое закрытие с отчётами."""
        now = self.clock() if now is None else now
        if self._watch_end is None:
            self._watch_end = self.current_shift(now)["end"]
            return 0
        sent = 0
        if now >= self._watch_end:
            end = self._watch_end
            sh = self.current_shift(end - 1)
            self._x("INSERT INTO shifts (id, start, end, closed_by) VALUES (?, ?, ?, 'авто')", [sh["id"], sh["start"], end])
            self.drop_alarm_msgs(before=end)
            if end - sh["start"] >= 300:                 # минутный хвост после ручного закрытия не рассылаем
                sent = self._reports({**sh, "end": end})
            self._watch_end = self.current_shift(now)["end"]
        return sent

    # ---------- тревоги ----------
    def _raise(self, row: dict, source: str = "auto", who: str | None = None, comment: str = "") -> dict | None:
        """Новая тревога по событию; None — по нему тревога уже есть."""
        with self.lock:
            if self.alarm(row["id"]) is not None:
                return None
            self._x("INSERT INTO alarms (event_id, created, title, severity, camera, camera_title, zone_title, snapshot,"
                    " status, source, source_by, comment) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)",
                    [row["id"], self.clock(), row["title"], row["severity"], row.get("camera"), row.get("camera_title"),
                     row.get("zone_title"), row.get("snapshot"), source, who, comment or None])
        a = self.alarm(row["id"])
        if self.bot is not None:
            self.bot.send_alarm(self.chats("supervisor"), a)
            ops = self.chats("operator") if source == "auto" else []
            if ops:                                       # операторам — та же карточка, для сведения, без кнопок
                self.bot.send_alarm(ops, a, readonly=True)
        return a

    def on_event_opened(self, row: dict) -> None:
        """Из потока камеры на каждое новое событие: критичное поднимает тревогу начальникам смены. Не блокирует."""
        try:
            if row.get("severity") in self.levels:
                self._raise(row)
        except Exception as e:
            log.warning("тревога по %s не поднята: %s", row.get("id"), e)

    def escalate_event(self, event_id: str, who: str, comment: str = "") -> dict | None:
        """Оператор передаёт начальнику смены любое (не только критичное) нарушение: дальше обычная тревога.
        None — нет такого события; если тревога по нему уже есть, возвращается она."""
        row = self.store.get(event_id)
        if row is None:
            return None
        a = self._raise(row, "operator", who, comment)
        if a is None:
            return {**self.alarm(event_id), "already": True}
        self.store.set_status(event_id, "sent_to_master", who)
        return a

    def chain(self, notifier):
        """Обёртка над Notifier: workers.py зовёт notifier.event_opened(row) — заодно уходит и тревога смены."""
        svc = self

        class Chained:
            def __getattr__(self, name):
                return getattr(notifier, name)

            def event_opened(self, ev: dict) -> None:
                notifier.event_opened(ev)
                svc.on_event_opened(ev)

        return Chained()

    def alarm(self, event_id: str) -> dict | None:
        r = self._q("SELECT * FROM alarms WHERE event_id = ?", [event_id])
        if not r:
            return None
        a = r[0]
        if not a.get("camera"):                            # тревоги прошлой версии: камера — из журнала
            ev = self.store.get(event_id)
            a["camera"] = ev["camera"] if ev else None
        a["source"] = a.get("source") or "auto"
        a["camera_no"] = camera_no(a["camera"])
        a["camera_label"] = cam_label(a["camera"], a["camera_title"])
        a["deadline"] = a["created"] + self.escalate_min * 60
        a["person"] = self._person_of(event_id)
        return a

    def alarms(self, status: str | None = None, since: float | None = None) -> list[dict]:
        since = self.current_shift()["start"] if since is None else since
        sql, args = "SELECT event_id FROM alarms WHERE created >= ?", [since]
        if status:
            sql, args = sql + " AND status = ?", [*args, status]
        return [self.alarm(r["event_id"]) for r in self._q(sql + " ORDER BY created DESC", args)]

    def ack(self, event_id: str, who: str) -> dict | None:
        a = self.alarm(event_id)
        if a is None:
            return None
        if a["status"] == "open":
            self._x("UPDATE alarms SET status = 'ack', ack_by = ?, ack_at = ? WHERE event_id = ?", [who, self.clock(), event_id])
            self.store.set_status(event_id, "confirmed", who)
            a = self.alarm(event_id)
            if self.bot is not None:
                self.bot.alarm_updated(a, "ack")
        return a

    def resolve(self, event_id: str, who: str, note: str = "") -> dict | None:
        a = self.alarm(event_id)
        if a is None:
            return None
        if a["status"] != "resolved":
            self._x("UPDATE alarms SET status = 'resolved', resolved_by = ?, resolved_at = ?, note = ?,"
                    " ack_by = COALESCE(ack_by, ?), ack_at = COALESCE(ack_at, ?) WHERE event_id = ?",
                    [who, self.clock(), note or "", who, self.clock(), event_id])
            self.store.set_status(event_id, "resolved", who)
            a = self.alarm(event_id)
            if self.bot is not None:
                self.bot.alarm_updated(a, "resolved")
        self.drop_alarm_msgs(event_ids=[event_id], kinds=("aux",))     # запрос отчёта и «что сделано?» больше не нужны
        return a

    def ask_report(self, event_id: str, who: str) -> dict | None:
        a = self.alarm(event_id)
        if a is None:
            return None
        self._x("UPDATE alarms SET asked_at = ? WHERE event_id = ?", [self.clock(), event_id])
        a = self.alarm(event_id)
        if self.bot is not None:
            self.bot.send_ask(self.chats("supervisor"), a, who)
        return a

    def reset_alarms(self) -> int:
        """Демо-сброс: все тревоги и их сообщения во всех чатах удаляются. Возвращает число тревог."""
        n = self._q("SELECT COUNT(*) AS n FROM alarms")[0]["n"]
        self.drop_alarm_msgs()
        self._x("DELETE FROM alarms")
        if self.bot is not None:
            self.bot.pending_note.clear()
        return n

    def prune_orphans(self) -> int:
        """Тревоги, чьих событий уже нет в журнале (tools/demo_reset.sh чистит events.db), и их сообщения — долой."""
        ids = [r["event_id"] for r in self._q("SELECT event_id FROM alarms")]
        gone = [e for e in ids if self.store.get(e) is None]
        alive = set(ids) - set(gone)
        stray = {r["event_id"] for r in self.msg_rows(kinds=("alarm", "escalation", "aux"))
                 if r["event_id"] and r["event_id"] not in alive}
        if stray:
            self.drop_alarm_msgs(event_ids=sorted(stray))
        for e in gone:
            self._x("DELETE FROM alarms WHERE event_id = ?", [e])
        return len(gone)

    def open_alarm_ids(self) -> set[str]:
        return {r["event_id"] for r in self._q("SELECT event_id FROM alarms WHERE status != 'resolved'")}

    def open_alarms(self) -> list[dict]:
        """Все неустранённые тревоги (из любой смены), старые сверху — для /alarms."""
        return [self.alarm(r["event_id"]) for r in
                self._q("SELECT event_id FROM alarms WHERE status != 'resolved' ORDER BY created")]

    # ---------- сообщения бота: что и где лежит, чтобы чистить чаты ----------
    def msg_add(self, chat_id: int, message_id: int, kind: str, event_id: str | None = None,
                expires: float | None = None, photo: bool = False) -> None:
        self._x("INSERT OR REPLACE INTO bot_messages (chat_id, message_id, kind, event_id, created, expires, photo)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)", [chat_id, message_id, kind, event_id, self.clock(), expires, int(photo)])

    def msg_rows(self, chat_id: int | None = None, kinds: tuple | None = None, event_ids: list | None = None,
                 before: float | None = None, expired: float | None = None) -> list[dict]:
        where, args = [], []
        if chat_id is not None:
            where.append("chat_id = ?")
            args.append(chat_id)
        for col, vals in (("kind", kinds), ("event_id", event_ids)):
            if vals is not None:
                where.append(f"{col} IN ({', '.join('?' for _ in vals) or 'NULL'})")
                args += list(vals)
        if before is not None:
            where.append("created <= ?")
            args.append(before)
        if expired is not None:
            where.append("expires IS NOT NULL AND expires <= ?")
            args.append(expired)
        return self._q("SELECT * FROM bot_messages" + (" WHERE " + " AND ".join(where) if where else "")
                       + " ORDER BY created", args)

    def msg_expire(self, chat_id: int, message_id: int, at: float) -> None:
        self._x("UPDATE bot_messages SET expires = ? WHERE chat_id = ? AND message_id = ?", [at, chat_id, message_id])

    def msg_drop(self, chat_id: int, message_id: int) -> None:
        self._x("DELETE FROM bot_messages WHERE chat_id = ? AND message_id = ?", [chat_id, message_id])

    def drop_alarm_msgs(self, event_ids: list | None = None, before: float | None = None,
                        kinds: tuple = ("alarm", "escalation", "aux")) -> int:
        """Удаляет из чатов сообщения, привязанные к тревогам (все или по списку, созданные до before)."""
        rows = [r for r in self.msg_rows(kinds=kinds, event_ids=event_ids, before=before) if r["event_id"]]
        if rows and self.bot is not None:
            self.bot.delete_rows(rows)
        else:
            for r in rows:
                self.msg_drop(r["chat_id"], r["message_id"])
        return len(rows)

    def sla(self, alarms: list[dict], now: float | None = None) -> dict:
        """Уложились ли в норматив: время до «Принял» и до «Устранено», доля устранённых за escalate_min.

        Доля считается от «решённых» тревог: устранённых и тех, чей срок уже истёк (открытая тревога в срок
        ещё не провал). by_who — по ответственному: кто принял (или устранил).
        """
        now = self.clock() if now is None else now
        limit = self.escalate_min * 60

        def stats(group: list[dict]) -> dict:
            ack = [a["ack_at"] - a["created"] for a in group if a["ack_at"]]
            res = [a["resolved_at"] - a["created"] for a in group if a["resolved_at"]]
            on_time = sum(1 for x in res if x <= limit)
            overdue = [a["event_id"] for a in group if not a["resolved_at"] and now - a["created"] > limit]
            decided = len(res) + len(overdue)
            r1 = lambda v: round(v, 1) if v is not None else None  # noqa: E731
            return {"alarms": len(group), "acked": len(ack), "resolved": len(res),
                    "median_ack_s": r1(statistics.median(ack)) if ack else None,
                    "avg_ack_s": r1(statistics.fmean(ack)) if ack else None,
                    "median_resolve_s": r1(statistics.median(res)) if res else None,
                    "avg_resolve_s": r1(statistics.fmean(res)) if res else None,
                    "on_time": on_time, "on_time_share": round(on_time / decided, 3) if decided else None,
                    "escalated": sum(1 for a in group if a["escalated_at"]),
                    "overdue": len(overdue), "overdue_ids": overdue}

        out = {**stats(alarms), "limit_min": self.escalate_min}
        groups: dict[str, list[dict]] = {}
        for a in alarms:
            groups.setdefault(a["ack_by"] or a["resolved_by"] or "никто не принял", []).append(a)
        out["by_who"] = sorted(({"who": w, **{k: v for k, v in stats(g).items() if k in (
            "alarms", "resolved", "median_ack_s", "median_resolve_s", "on_time_share", "overdue")}}
            for w, g in groups.items()), key=lambda x: -x["alarms"])
        return out

    def responsible(self, a: dict) -> str:
        if a.get("ack_by"):
            return a["ack_by"]
        on_shift = self.names("supervisor")
        return "никто не принял" + (f" (на смене: {', '.join(on_shift)})" if on_shift else "")

    def check_escalations(self, now: float | None = None) -> list[str]:
        """Неустранённые за escalate_min тревоги уходят руководителям; возвращает id эскалированных."""
        now = self.clock() if now is None else now
        rows = self._q("SELECT event_id FROM alarms WHERE status != 'resolved' AND escalated_at IS NULL AND created <= ?",
                       [now - self.escalate_min * 60])
        out = []
        for r in rows:
            self._x("UPDATE alarms SET escalated_at = ? WHERE event_id = ?", [now, r["event_id"]])
            a = self.alarm(r["event_id"])
            if self.bot is not None:
                self.bot.send_escalation(self.chats("director"), a, self.responsible(a),
                                         max(1, round((now - a["created"]) / 60)))
            out.append(r["event_id"])
        return out

    def start(self) -> None:
        """Фоновые проверки: эскалация каждые 10 с, конец смены раз в минуту. На старте — тревоги без событий долой."""
        def loop():
            try:
                self.prune_orphans()
            except Exception as e:
                log.warning("чистка тревог без событий: %s", e)
            n = 0
            while True:
                try:
                    self.check_escalations()
                    if n % 6 == 0:
                        self.check_shift_end()
                except Exception as e:
                    log.warning("фоновая проверка смены: %s", e)
                n += 1
                time.sleep(10)

        threading.Thread(target=loop, daemon=True, name="shift").start()

    # ---------- сотрудники ----------
    def _person(self, pid: str | None) -> dict | None:
        if not pid or self.people is None:
            return None
        p = self.people.get(pid)
        return {"id": p["id"], "name": p["name"], "photo": p.get("photo")} if p else None

    def _person_of(self, event_id: str) -> dict | None:
        if self.people is None:
            return None
        try:
            p = self.people.person_of(event_id)
        except Exception:
            return None
        return {"id": p["id"], "name": p["name"], "photo": p.get("photo")} if p else None

    # ---------- выборки для мини-приложения ----------
    def _window(self, since: float | None) -> float:
        return self.current_shift()["start"] if since is None else since

    def events(self, person: str | None = None, camera: str | None = None, severity: str | None = None,
               limit: int = 100, since: float | None = None, until: float | None = None) -> list[dict]:
        rows = self.store.query(camera=camera, severity=severity, since=self._window(since) or None, until=until,
                                limit=100000 if person else limit)
        alarm_of = {r["event_id"]: r["status"] for r in self._q("SELECT event_id, status FROM alarms")}
        out = []
        for r in rows:
            r["person"] = self._person_of(r["id"])
            r["camera_no"] = camera_no(r["camera"])
            r["alarm_status"] = alarm_of.get(r["id"])   # None | open | ack | resolved — можно ли «передать начальнику»
            if person and (r["person"] or {}).get("id") != person:
                continue
            out.append(r)
            if len(out) >= limit:
                break
        return out

    def summary(self, since: float | None = None, shift: dict | None = None) -> dict:
        """Сводка смены (или периода с since): итоги, по типам, по камерам."""
        sh = shift or self.current_shift()
        if since is not None and shift is None:
            sh = {**sh, "start": since}
        until = shift["end"] if shift else None
        rows = self.events(since=sh["start"], until=until, limit=100000)
        by_type, by_camera, persons = {}, {}, set()
        for r in rows:
            t = by_type.setdefault(r["type"], {"type": r["type"], "title": r["title"], "count": 0})
            t["count"] += 1
            c = by_camera.setdefault(r["camera"], {"camera": r["camera"], "camera_title": r["camera_title"],
                                                   "camera_no": r["camera_no"], "count": 0})
            c["count"] += 1
            if r["person"]:
                persons.add(r["person"]["id"])
        alarms = [a for a in self.alarms(since=sh["start"]) if until is None or a["created"] <= until]
        open_alarms = sum(1 for a in alarms if a["status"] != "resolved")
        return {"shift": sh, "sla": self.sla(alarms, until), "alarms": alarms, "totals": {
                    "violations": len(rows), "critical": sum(r["severity"] == "critical" for r in rows),
                    "open_alarms": open_alarms, "people": len(persons)},
                "by_type": sorted(by_type.values(), key=lambda x: -x["count"]),
                "by_camera": sorted(by_camera.values(), key=lambda x: -x["count"]),
                "rows": rows}

    def people_list(self, since: float | None = None) -> list[dict]:
        if self.people is None:
            return []
        stats: dict[str, dict] = {}
        for r in self.events(since=self._window(since), limit=100000):
            if r["person"]:
                s = stats.setdefault(r["person"]["id"], {"events": 0, "critical": 0, "last_at": None})
                s["events"] += 1
                s["critical"] += r["severity"] == "critical"
                s["last_at"] = max(s["last_at"] or 0, r["started_at"])
        bound = {r["person_id"] for r in self._q("SELECT person_id FROM users WHERE person_id IS NOT NULL")}
        out = [{"id": p["id"], "name": p["name"], "photo": p.get("photo"),
                **stats.get(p["id"], {"events": 0, "critical": 0, "last_at": None}), "tg_bound": p["id"] in bound}
               for p in self.people.people()]
        return sorted(out, key=lambda p: (-p["events"], p["name"]))

    def person_detail(self, pid: str, since: float | None = None) -> dict | None:
        p = next((x for x in self.people_list(since) if x["id"] == pid), None)
        if p is None:
            return None
        return {"person": p, "events": self.events(person=pid, since=since, limit=1000)}

    # ---------- CSV и тексты отчётов ----------
    @staticmethod
    def csv_events(rows: list[dict]) -> bytes:
        """CSV для русского Excel: UTF-8 с BOM, разделитель «;»."""
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["время", "камера", "зона", "нарушение", "уровень", "длительность, с", "сотрудник", "статус"])
        for r in sorted(rows, key=lambda r: r["started_at"]):
            w.writerow([time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(r["started_at"])),
                        cam_label(r["camera"], r["camera_title"]),
                        r["zone_title"], r["title"], SEVERITY_TITLES.get(r["severity"], r["severity"]),
                        r["duration_s"] if r["duration_s"] is not None else "",
                        (r.get("person") or {}).get("name", "не опознан"), STATUS_TITLES.get(r["status"], r["status"])])
        return ("\ufeff" + buf.getvalue()).encode("utf-8")

    def csv_alarms(self, alarms: list[dict]) -> bytes:
        """Тревоги смены с временем реакции: уложились ли в норматив escalate_min."""
        limit = self.escalate_min * 60
        t = lambda ts: time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(ts)) if ts else ""  # noqa: E731
        m = lambda a, k: round((a[k] - a["created"]) / 60, 1) if a[k] else ""  # noqa: E731
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["создана", "камера", "зона", "нарушение", "уровень", "источник", "принял", "принята",
                    "до «Принял», мин", "устранил", "устранена", "до «Устранено», мин", "эскалация руководителю",
                    "отчёт", f"в норматив {self.escalate_min:g} мин"])
        for a in sorted(alarms, key=lambda a: a["created"]):
            src = "авто" if a["source"] == "auto" else f"передал {a['source_by'] or ''}".strip() + (
                f": {a['comment']}" if a.get("comment") else "")
            on_time = ("да" if a["resolved_at"] - a["created"] <= limit else "нет") if a["resolved_at"] else (
                "нет" if self.clock() - a["created"] > limit else "в работе")
            w.writerow([t(a["created"]), a["camera_label"], a["zone_title"] or "", a["title"],
                        SEVERITY_TITLES.get(a["severity"], a["severity"]), src, a["ack_by"] or "", t(a["ack_at"]),
                        m(a, "ack_at"), a["resolved_by"] or "", t(a["resolved_at"]), m(a, "resolved_at"),
                        t(a["escalated_at"]), a["note"] or "", on_time])
        return ("\ufeff" + buf.getvalue()).encode("utf-8")

    def csv_shift(self, since: float | None = None) -> bytes:
        return self.csv_events(self.events(since=self._window(since), limit=100000))

    def csv_person(self, pid: str, since: float | None = None) -> bytes:
        return self.csv_events(self.events(person=pid, since=self._window(since), limit=100000))

    def csv_alarms_shift(self, since: float | None = None) -> bytes:
        return self.csv_alarms(self.alarms(since=self._window(since)))

    @staticmethod
    def file_tag(shift_id: str) -> str:
        """«24.10 день» → «24.10-day» для имени файла (латиницей — без проблем с заголовками)."""
        return shift_id.split(" с ")[0].replace("день", "day").replace("ночь", "night").replace(" ", "-")

    @staticmethod
    def sla_text(sla: dict) -> str:
        """Блок «уложились ли в норматив» (HTML)."""
        lim = f"<code>{sla['limit_min']:g} мин</code>"
        if not sla["alarms"]:
            return f"⏱ <b>Реакция на тревоги</b> · норматив {lim}\nТревог за смену не было."
        decided = sla["resolved"] + sla["overdue"]
        share = f" ({round(sla['on_time_share'] * 100)}%)" if sla["on_time_share"] is not None else ""
        lines = [f"⏱ <b>Реакция на тревоги</b> · норматив {lim}",
                 f"Тревог <b>{sla['alarms']}</b> · принято <b>{sla['acked']}</b> · устранено <b>{sla['resolved']}</b>",
                 f"Медиана: до «Принял» <code>{fmt_dur(sla['median_ack_s'])}</code> · "
                 f"до «Устранено» <code>{fmt_dur(sla['median_resolve_s'])}</code>",
                 f"✅ В норматив: <b>{sla['on_time']} из {decided}</b>{share} · ❗ эскалаций: {sla['escalated']}"
                 f" · ⌛ просрочено: {sla['overdue']}"]
        for x in sla["by_who"][:4]:
            lines.append(f"👤 {esc(x['who'])} — {x['alarms']}"
                         + (f", <code>{fmt_dur(x['median_resolve_s'])}</code>" if x["median_resolve_s"] is not None else "")
                         + (f", в срок {round(x['on_time_share'] * 100)}%" if x["on_time_share"] is not None else ""))
        return "\n".join(lines)

    @staticmethod
    def shift_head(sh: dict) -> str:
        return f"<b>Смена {esc(sh['id'])}</b> · <code>{hm(sh['start'])}–{hm(sh['end'])}</code>"

    @classmethod
    def summary_text(cls, s: dict, sla: bool = False) -> str:
        """Сводка смены (HTML): итоги, частые нарушения, камеры и, для персонала, реакция на тревоги."""
        t = s["totals"]
        lines = [f"📊 {cls.shift_head(s['shift'])}",
                 f"⚠️ Нарушений <b>{t['violations']}</b> · критичных <b>{t['critical']}</b>",
                 f"🔔 Открытых тревог <b>{t['open_alarms']}</b> · 👥 сотрудников <b>{t['people']}</b>"]
        if s["by_type"]:
            lines.append("🏷 " + " · ".join(f"{esc(x['title'])} — {x['count']}" for x in s["by_type"][:4]))
        if s["by_camera"]:
            lines.append("📍 " + " · ".join(f"{esc(cam_label(x['camera'], x['camera_title']))} — {x['count']}"
                                           for x in s["by_camera"][:4]))
        if sla and s.get("sla"):
            lines += ["", cls.sla_text(s["sla"])]
        return "\n".join(lines)

    @classmethod
    def who_where_text(cls, s: dict) -> str:
        """Общая сводка для рабочих без привязки к сотруднику: кто где нарушал (HTML)."""
        by_person: dict[str, int] = {}
        for r in s["rows"]:
            name = (r["person"] or {}).get("name", "не опознаны")
            by_person[name] = by_person.get(name, 0) + 1
        lines = [f"📋 {cls.shift_head(s['shift'])} · кто где нарушал", f"Всего нарушений <b>{len(s['rows'])}</b>"]
        if by_person:
            lines.append("👥 " + " · ".join(f"{esc(k)} — {v}" for k, v in
                                           sorted(by_person.items(), key=lambda kv: -kv[1])[:6]))
        if s["by_camera"]:
            lines.append("📍 " + " · ".join(f"{esc(cam_label(x['camera'], x['camera_title']))} — {x['count']}"
                                           for x in s["by_camera"][:6]))
        lines.append("💡 Привяжите свой профиль в приложении («Это я») — тогда в отчёте будут только ваши нарушения.")
        return "\n".join(lines)

    @classmethod
    def own_text(cls, shift: dict, name: str, rows: list[dict]) -> str:
        """Личный отчёт рабочего (HTML)."""
        head = f"👷 {cls.shift_head(shift)} · {esc(name)}"
        if not rows:
            return f"{head}\n✅ Нарушений за вами не зафиксировано. Спасибо за аккуратную работу."
        lines = [head, f"Ваших нарушений <b>{len(rows)}</b>:"]
        for r in sorted(rows, key=lambda r: r["started_at"])[:15]:
            lines.append(f"• <code>{hm(r['started_at'])}</code> {esc(r['title'])} — "
                         f"{esc(cam_label(r['camera'], r['camera_title']))}")
        if len(rows) > 15:
            lines.append(f"… и ещё {len(rows) - 15} — полный список в файле")
        return "\n".join(lines)

    @staticmethod
    def with_card(card: bytes | None, name: str, text: str) -> list[tuple]:
        """Картинка-отчёт с текстом в подписи; длинный текст — отдельным сообщением после картинки."""
        if card is None:
            return [("text", text)]
        if len(text) <= 1000:
            return [("photo", name, card, text)]
        return [("photo", name, card, text.split("\n")[0]), ("text", text)]

    def shift_payload(self, s: dict) -> dict:
        """Сводка в том виде, что отдаёт GET /api/tg/shift (для картинки-отчёта)."""
        return {k: v for k, v in s.items() if k not in ("rows", "alarms")}

    def own_items(self, u: dict, s: dict, tag: str) -> list[tuple]:
        """Личный отчёт рабочего: карточка (если рисуется), текст, CSV."""
        pid = u["person_id"]
        own = [r for r in s["rows"] if (r["person"] or {}).get("id") == pid]
        person = self._person(pid) or {"id": pid, "name": u["name"], "photo": None}
        photo = self.media / Path(person["photo"]).name if person.get("photo") else None
        card = render_card("person_card", person, own, photo)
        out = self.with_card(card, f"me-{tag}.png", self.own_text(s["shift"], person["name"], own))
        if own:
            out.append(("doc", f"my-{tag}.csv", self.csv_events(own), f"📄 <b>Ваши нарушения</b> · {len(own)}"))
        return out

    def report_for(self, u: dict, shift: dict | None = None) -> list[tuple]:
        """Отчёт пользователю по смене: [("text", html) | ("photo"|"doc", имя файла, байты, подпись)].

        Оператору, начальнику и руководителю — карточка и сводка с реакцией на тревоги, журнал и тревоги CSV;
        рабочему с привязкой — свои нарушения (карточка, текст, CSV); без привязки — общая сводка «кто где нарушал».
        """
        if shift is None:
            shift = {**self.current_shift(), "end": self.clock()}
        s = self.summary(shift=shift)
        tag = self.file_tag(shift["id"])
        if u["role"] in VIEWERS:
            card = render_card("shift_card", self.shift_payload(s))
            out = self.with_card(card, f"shift-{tag}.png", self.summary_text(s, sla=True))
            out.append(("doc", f"shift-{tag}.csv", self.csv_events(s["rows"]),
                        f"📄 <b>Журнал смены {esc(shift['id'])}</b> · нарушений: {len(s['rows'])}"))
            if s["alarms"]:
                out.append(("doc", f"alarms-{tag}.csv", self.csv_alarms(s["alarms"]),
                            f"⏱ <b>Тревоги и время реакции</b> · {len(s['alarms'])}"))
            return out
        if u.get("person_id"):
            return self.own_items(u, s, tag)
        return [("text", self.who_where_text(s))]

    def _reports(self, shift: dict) -> int:
        if self.bot is None or not self.bot.enabled:
            return 0
        sent = 0
        for u in self._q("SELECT * FROM users WHERE chat_id IS NOT NULL AND role IS NOT NULL"):
            for item in self.report_for(u, shift):
                sent += self.send(u["chat_id"], item)
        return sent

    def send(self, chat_id: int, item: tuple, kind: str = "report", ttl: float | None = None) -> int:
        """Одно сообщение отчёта через бота; 1 — поставлено в очередь, 0 — бот выключен или нет чата."""
        if self.bot is None or not self.bot.enabled or not chat_id:
            return 0
        if item[0] == "text":
            return self.bot.send_text(chat_id, item[1], kind=kind, ttl=ttl)
        if item[0] == "photo":
            return self.bot.send_photo(chat_id, item[1], item[2], item[3], kind=kind, ttl=ttl)
        return self.bot.send_document(chat_id, item[1], item[2], item[3], kind=kind, ttl=ttl)
