import hashlib
import hmac
import json
import os
import time
from datetime import datetime
from urllib.parse import urlencode

import pytest

from api.shift import ShiftService, cam_label, camera_no, check_init_data
from api.store import EventStore

T = datetime(2026, 10, 14, 12, 0).timestamp()          # дневная смена 14.10, вне перевода часов


class FakePeople:
    def __init__(self):
        self.by_event = {}
        self.items = {"p1": {"id": "p1", "name": "Иванов", "photo": "/media/p1.jpg", "created": T}}

    def person_of(self, event_id):
        pid = self.by_event.get(event_id)
        return self.items[pid] if pid else None

    def people(self):
        return list(self.items.values())

    def get(self, pid):
        return self.items.get(pid)

    def events_of(self, pid):
        return [e for e, p in self.by_event.items() if p == pid]


class FakeBot:
    """Записывает, что сервис просил отправить; в сеть не ходит."""
    enabled, token, username = True, "", "test_bot"

    def __init__(self):
        self.calls = []
        self.pending_note = {}

    def __getattr__(self, name):
        def rec(*args, **kw):
            self.calls.append((name, args))
            return len(args[0]) if args and isinstance(args[0], list) else 1
        return rec


def roles(path, demo=True, users=None):
    lines = [f"demo: {'true' if demo else 'false'}", "users:" if users else "users: {}"]
    lines += [f"  {k}: {{role: {v}, name: \"{k}-name\"}}" for k, v in (users or {}).items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    st = path.stat()                                   # mtime меняется гарантированно — файл перечитается
    os.utime(path, (st.st_atime, st.st_mtime + 1 + time.time() % 1))


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("LINQ_TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("LINQ_SHIFTS", raising=False)
    monkeypatch.delenv("LINQ_ESCALATE_MIN", raising=False)
    monkeypatch.delenv("LINQ_PUBLIC_URL", raising=False)
    monkeypatch.setenv("LINQ_SECRETS", str(tmp_path / "secrets"))
    roles(tmp_path / "roles.yaml")
    s = ShiftService(EventStore(tmp_path / "e.db"), FakePeople(), tmp_path / "media", tmp_path, tmp_path / "roles.yaml")
    s.clock = lambda: T
    return s


def event(svc, eid, severity="critical", at=T - 600, camera="cam-01", type_="fall", title="Падение"):
    row = {"id": eid, "camera": camera, "camera_title": "Цех 1", "zone": "frame", "zone_title": "Весь кадр",
           "type": type_, "title": title, "severity": severity, "track_id": 1, "started_at": at,
           "confirmed_at": at + 1, "duration_s": 5.0, "latency_s": 1.0, "score": 0.9, "details": {},
           "snapshot": f"/media/{eid}.jpg", "clip": None, "box": [0, 0, 10, 10], "status": "new"}
    svc.store.insert(row)
    return svc.store.get(eid)


def staff(svc, tg_id, role, chat_id=None, person=None):
    svc.resolve_user(tg_id, f"user{tg_id}", chat_id=chat_id or tg_id)
    svc.set_role(tg_id, role)
    if person:
        svc.bind(tg_id, person)
    return svc.user(tg_id)


def init_data(token, user, auth_date=None):
    fields = {"auth_date": str(int(auth_date or time.time())), "query_id": "AAH", "user": json.dumps(user)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_init_data_signature():
    token, user = "123456:TEST-token", {"id": 42, "first_name": "Пётр"}
    good = init_data(token, user)
    assert check_init_data(good, token)["id"] == 42
    assert check_init_data(good, "999:other") is None
    assert check_init_data(good.replace("42", "43"), token) is None
    assert check_init_data(init_data(token, user, time.time() - 2 * 86400), token) is None
    assert check_init_data("garbage", token) is None


def test_roles_from_yaml_and_demo(svc, tmp_path):
    roles(tmp_path / "roles.yaml", users={111: "director"})
    u = svc.resolve_user(111, "Тест")
    assert (u["role"], u["demo"], u["name"]) == ("director", 0, "111-name")
    with pytest.raises(PermissionError):
        svc.set_role(111, "worker")
    d = svc.resolve_user(222, "Гость", chat_id=222)
    assert d["role"] is None and d["demo"] == 1 and d["chat_id"] == 222
    assert svc.set_role(222, "supervisor")["role"] == "supervisor"
    assert svc.set_role(222, "worker")["role"] == "worker"
    roles(tmp_path / "roles.yaml", demo=False, users={111: "director"})
    assert svc.resolve_user(333, "Чужой") is None
    assert svc.resolve_user(111, "Тест")["role"] == "director"
    with pytest.raises(PermissionError):
        svc.set_role(222, "director")


def test_alarm_lifecycle_mirrors_store(svc):
    svc.bot = bot = FakeBot()
    staff(svc, 10, "supervisor")
    svc.on_event_opened(event(svc, "e-med", "medium"))
    svc.on_event_opened(event(svc, "e1"))
    assert svc.alarm("e-med") is None
    a = svc.alarm("e1")
    assert a["status"] == "open" and a["deadline"] == T + 30 * 60
    assert bot.calls[0] == ("send_alarm", ([10], a))
    assert svc.ack("e1", "Мастер")["status"] == "ack"
    assert svc.store.get("e1")["status"] == "confirmed"
    r = svc.resolve("e1", "Мастер", "Помощь оказана")
    assert (r["status"], r["note"], r["resolved_by"]) == ("resolved", "Помощь оказана", "Мастер")
    ev = svc.store.get("e1")
    assert ev["status"] == "resolved" and [h["status"] for h in ev["history"]] == ["confirmed", "resolved"]
    assert [c[0] for c in bot.calls] == ["send_alarm", "alarm_updated", "alarm_updated"]
    assert svc.ack("nope", "x") is None


def test_escalation_after_timeout(svc):
    svc.bot = bot = FakeBot()
    staff(svc, 10, "supervisor")
    staff(svc, 20, "director")
    svc.set_escalate_min(2)
    svc.on_event_opened(event(svc, "e1"))
    svc.on_event_opened(event(svc, "e2"))
    svc.resolve("e2", "Мастер", "Ложная тревога")
    assert svc.check_escalations(T + 60) == []
    assert svc.check_escalations(T + 121) == ["e1"]
    assert svc.check_escalations(T + 300) == []                 # руководителю — один раз
    name, (chats, a, responsible, minutes) = next(c for c in bot.calls if c[0] == "send_escalation")
    assert chats == [20] and a["event_id"] == "e1" and minutes == 2 and responsible.startswith("никто не принял")
    svc.ask_report("e1", "Директор")
    assert svc.alarm("e1")["asked_at"] == T and bot.calls[-1][0] == "send_ask" and bot.calls[-1][1][0] == [10]


def test_shift_windows_and_manual_close(svc):
    at = lambda *a: datetime(2026, 10, *a).timestamp()  # noqa: E731
    night = svc.scheduled_shift(at(14, 3, 0))
    assert night == {"id": "13.10 ночь", "start": at(13, 20, 0), "end": at(14, 8, 0)}
    assert svc.scheduled_shift(at(14, 23, 30))["id"] == "14.10 ночь"
    assert svc.scheduled_shift(at(14, 8, 0)) == {"id": "14.10 день", "start": at(14, 8, 0), "end": at(14, 20, 0)}
    assert svc.close_shift("Мастер", now=at(14, 14, 0)) == 0          # бота нет — сообщений нет
    cur = svc.current_shift(at(14, 15, 0))
    assert cur["start"] == at(14, 14, 0) and cur["end"] == at(14, 20, 0) and cur["id"].startswith("14.10 день")
    assert svc.current_shift(at(14, 21, 0))["start"] == at(14, 20, 0)


def test_csv_bom_semicolon_and_person(svc):
    svc.people.by_event["e1"] = "p1"
    event(svc, "e1")
    event(svc, "e2", "medium", type_="phone", title="Телефон")
    data = svc.csv_shift()
    assert data.startswith(b"\xef\xbb\xbf")
    lines = data.decode("utf-8-sig").splitlines()
    assert lines[0].split(";")[:4] == ["время", "камера", "зона", "нарушение"] and len(lines) == 3
    assert "Иванов" in data.decode() and "не опознан" in data.decode()
    assert len(svc.csv_person("p1").decode("utf-8-sig").splitlines()) == 2
    s = svc.summary()
    assert s["totals"] == {"violations": 2, "critical": 1, "open_alarms": 0, "people": 1}
    assert s["by_type"][0]["count"] == 1 and s["by_camera"] == [{"camera": "cam-01", "camera_title": "Цех 1",
                                                                    "camera_no": "К-01", "count": 2}]
    p = svc.people_list()
    assert p[0]["id"] == "p1" and p[0]["events"] == 1 and p[0]["critical"] == 1 and not p[0]["tg_bound"]
    assert [e["person"]["name"] for e in svc.events(person="p1")] == ["Иванов"]


def test_shift_end_reports(svc):
    svc.bot = bot = FakeBot()
    svc.people.by_event["e1"] = "p1"
    event(svc, "e1")
    staff(svc, 1, "worker", person="p1")       # своё: текст + CSV
    staff(svc, 2, "worker")                    # без привязки: общая сводка
    staff(svc, 3, "supervisor")                # сводка + CSV
    assert svc.check_shift_end(T) == 0
    end = datetime(2026, 10, 14, 20, 0).timestamp()
    assert svc.check_shift_end(end + 5) == 5
    texts = {c[1][0]: c[1][1] for c in bot.calls if c[0] == "send_text"}
    assert "ваши нарушения" in texts[1] and "кто где нарушал" in texts[2] and "Иванов — 1" in texts[2]
    assert sorted(c[1][0] for c in bot.calls if c[0] == "send_document") == [1, 3]
    assert svc.current_shift(end + 60)["start"] == end


def test_bot_disabled_without_token(svc):
    from api.telegram import TelegramBot
    bot = TelegramBot(svc)
    assert not bot.enabled and bot.send_text(1, "x") == 0
    bot.start()                                # без токена — просто ничего не запускает


def test_routes(svc, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.tg_routes import install

    app = FastAPI()
    s = install(app, svc.store, svc.people, tmp_path / "media", tmp_path / "d2", tmp_path / "roles.yaml")
    s.clock = lambda: T
    c = TestClient(app)
    worker, boss = {"X-Demo-User": "w", "X-Demo-Role": "worker"}, {"X-Demo-User": "b", "X-Demo-Role": "director"}
    me = c.get("/api/tg/me", headers=worker).json()
    assert me["role"] == "worker" and me["demo"] and me["bot"]["enabled"] is False
    assert c.get("/api/tg/alarms", headers=worker).status_code == 403
    s.on_event_opened(event(s, "e1"))
    assert [a["event_id"] for a in c.get("/api/tg/alarms", headers=boss).json()] == ["e1"]
    assert c.post("/api/tg/alarms/e1/resolve", json={"note": "ok"}, headers=boss).json()["status"] == "resolved"
    shift = c.get("/api/tg/shift", headers=boss).json()
    assert shift["totals"]["violations"] == 1 and shift["sla"]["alarms"] == 1 and "alarms" not in shift
    r = c.get("/api/tg/csv?scope=shift", headers=boss)
    assert r.headers["content-type"].startswith("text/csv") and r.content.startswith(b"\xef\xbb\xbf")
    assert c.get("/api/tg/csv?scope=shift", headers=worker).status_code == 403
    r = c.get("/api/tg/csv?scope=alarms", headers=boss)
    assert r.content.startswith(b"\xef\xbb\xbf") and "в норматив" in r.content.decode("utf-8-sig").splitlines()[0]
    op = {"X-Demo-User": "o", "X-Demo-Role": "operator"}
    event(s, "e5", "medium", type_="phone", title="Телефон")
    assert c.post("/api/tg/events/e5/escalate", headers=worker).status_code == 403
    a = c.post("/api/tg/events/e5/escalate", json={"comment": "без каски у станка"}, headers=op).json()
    assert (a["source"], a["source_by"], a["status"], a["camera_no"]) == ("operator", "Демо: o", "open", "К-01")
    assert c.post("/api/tg/events/e5/escalate", headers=op).json()["already"] is True
    assert c.post("/api/tg/events/nope/escalate", headers=op).status_code == 404
    assert c.get("/api/tg/alarms", headers=op).status_code == 200
    assert c.post("/api/tg/alarms/e5/ack", headers=op).status_code == 403
    ev = c.get("/api/tg/events", headers=op).json()
    assert {e["id"]: (e["camera_no"], e["alarm_status"]) for e in ev}["e5"] == ("К-01", "open")
    assert c.post("/api/tg/csv/send", json={"scope": "shift"}, headers=boss).json()["ok"] is False
    assert c.post("/api/tg/settings", json={"escalate_min": 1}, headers=boss).json()["escalate_min"] == 1
    assert c.post("/api/tg/bind", json={"person_id": "p1"}, headers=worker).json()["person"]["name"] == "Иванов"
    assert c.post("/api/tg/demo/reset", headers=worker).status_code == 403
    assert c.post("/api/tg/demo/reset", headers=boss).json() == {"alarms": 2} and s.alarms(since=0) == []
    s.bot.token = "123:abc"                    # подпись Telegram проверяется, даже если бот не запущен
    u = c.get("/api/tg/me", headers={"X-Tg-Init-Data": init_data("123:abc", {"id": 77, "first_name": "Оля"})}).json()
    assert u["tg_id"] == 77 and u["name"] == "Оля"
    roles(tmp_path / "roles.yaml", demo=False)
    assert c.get("/api/tg/me", headers=worker).status_code == 401


# ---------- чистка чатов: настоящий TelegramBot без токена и без сети, ответы Telegram подставные ----------
def live_bot(svc):
    from api.telegram import TelegramBot
    bot = TelegramBot(svc)                     # без токена; start() не зовём — в сеть ничего не уходит
    bot.enabled = True
    sent, n = [], [1000]

    def call(method, params=None, files=None, client=None):
        sent.append((method, params))
        if method.startswith("send"):
            n[0] += 1
            return {"message_id": n[0]}
        return True

    bot._call = call
    svc.bot = bot

    def flush():
        while not bot.q.empty():
            bot._process(bot.q.get_nowait())
        out = list(sent)
        sent.clear()
        return out
    return bot, flush


def msg(uid, mid, text):
    return {"chat": {"id": uid, "type": "private"}, "from": {"id": uid}, "message_id": mid, "text": text}


def test_bot_alarm_card_edited_not_resent(svc):
    bot, flush = live_bot(svc)
    staff(svc, 10, "supervisor")
    staff(svc, 20, "director")
    svc.on_event_opened(event(svc, "e1"))
    out = flush()
    assert [(m, p["chat_id"]) for m, p in out] == [("sendMessage", 10)]
    card = svc.msg_rows(kinds=("alarm",))[0]
    svc.ack("e1", "Мастер")
    out = flush()
    assert [m for m, _ in out] == ["editMessageText"] and out[0][1]["message_id"] == card["message_id"]
    assert "👀 Принял Мастер" in out[0][1]["text"]
    svc.set_escalate_min(1)
    svc.check_escalations(T + 61)
    svc.ask_report("e1", "Директор")
    flush()
    assert sorted(r["kind"] for r in svc.msg_rows()) == ["alarm", "aux", "escalation"]
    bot._on_callback({"id": "q", "from": {"id": 10}, "message": {"chat": {"id": 10}, "message_id": card["message_id"]},
                      "data": "res:e1"})
    flush()
    bot._on_message(msg(10, 555, "Потушили"))
    out = flush()
    assert not [m for m, _ in out if m.startswith("send")]                 # ни одного нового сообщения
    assert ("deleteMessage", 555) in [(m, p["message_id"]) for m, p in out]
    edits = [p for m, p in out if m == "editMessageText"]
    assert {p["chat_id"] for p in edits} == {10, 20}
    assert all(p["text"].startswith("✅ Устранено: Падение · К-01 · Цех 1 · user10, Потушили")
               and p["reply_markup"] == {"inline_keyboard": []} for p in edits)
    assert sorted(r["kind"] for r in svc.msg_rows()) == ["alarm", "escalation"]   # подсказка и запрос отчёта ушли
    assert {r["expires"] for r in svc.msg_rows()} == {T + 120}               # итоговые карточки живут 120 с
    assert svc.close_shift("Мастер") == 6                                   # сводка + журнал + тревоги CSV, ×2
    out = flush()
    assert [m for m, _ in out][:2] == ["deleteMessage", "deleteMessage"]   # карточки смены — до отчёта
    assert {r["kind"] for r in svc.msg_rows()} == {"report"}


def test_resolved_cards_expire(svc, monkeypatch):
    monkeypatch.setenv("LINQ_TG_RESOLVED_TTL", "30")
    bot, flush = live_bot(svc)
    staff(svc, 10, "supervisor")
    staff(svc, 20, "director")
    svc.on_event_opened(event(svc, "e1"))
    svc.set_escalate_min(1)
    svc.check_escalations(T + 61)
    flush()
    svc.resolve("e1", "Мастер", "Очаг потушен")
    assert [m for m, _ in flush()] == ["editMessageText", "editMessageText"]   # сначала короткая итоговая подпись
    assert bot.sweep(T + 29) == 0 and bot.sweep(T + 30) == 2
    assert {(m, p["chat_id"]) for m, p in flush()} == {("deleteMessage", 10), ("deleteMessage", 20)}
    assert svc.msg_rows() == [] and svc.alarm("e1")["status"] == "resolved"     # история — в приложении и CSV


def test_bot_single_menu_clear_and_expiry(svc):
    bot, flush = live_bot(svc)
    staff(svc, 10, "supervisor")
    bot._on_message(msg(10, 1, "/start"))
    flush()
    first = svc.msg_rows(kinds=("menu",))[0]["message_id"]
    bot._on_message(msg(10, 2, "/start"))
    out = [(m, p.get("message_id")) for m, p in flush()]
    assert ("deleteMessage", first) in out and ("deleteMessage", 2) in out
    assert len(svc.msg_rows(kinds=("menu",))) == 1
    svc.on_event_opened(event(svc, "e1"))
    svc.on_event_opened(event(svc, "e2"))
    svc.resolve("e2", "Мастер", "Ложная тревога")
    svc.send(10, ("text", "отчёт"))
    flush()
    bot._on_message(msg(10, 3, "/clear"))
    flush()
    left = svc.msg_rows()
    assert sorted(r["kind"] for r in left) == ["alarm", "aux", "menu"]           # aux — «убрал N», живёт 10 с
    assert [r["event_id"] for r in left if r["kind"] == "alarm"] == ["e1"]
    assert bot.sweep(T + 11) == 1
    flush()
    assert sorted(r["kind"] for r in svc.msg_rows()) == ["alarm", "menu"]


def test_reset_alarms_and_orphans(svc):
    bot, flush = live_bot(svc)
    staff(svc, 10, "supervisor")
    svc.on_event_opened(event(svc, "e1"))
    svc.on_event_opened(event(svc, "e2"))
    flush()
    with svc.store.lock:                       # как tools/demo_reset.sh: события стёрты, тревоги остались
        svc.store.db.execute("DELETE FROM events WHERE id = 'e2'")
        svc.store.db.commit()
    assert svc.prune_orphans() == 1
    assert [m for m, _ in flush()] == ["deleteMessage"]
    assert svc.alarm("e2") is None and svc.alarm("e1") is not None and len(svc.msg_rows()) == 1
    assert svc.reset_alarms() == 1
    assert [m for m, _ in flush()] == ["deleteMessage"]
    assert svc.alarms(since=0) == [] and svc.msg_rows() == []


def test_camera_numbers():
    assert [camera_no(c) for c in ("cam-03-shop", "cam-07-hall", "live-1729", "webcam", None)] == \
        ["К-03", "К-07", "Лайв", "Вебкамера", ""]
    assert cam_label("cam-03-shop", "Цех") == "К-03 · Цех"
    assert cam_label("webcam", "Вебкамера") == "Вебкамера" and cam_label("live-1", "Лайв 1: запись") == "Лайв 1: запись"


def test_sla_and_alarms_csv(svc):
    svc.set_escalate_min(2)
    for eid in ("a1", "a2", "a3", "a4"):
        svc.on_event_opened(event(svc, eid))
    svc.clock = lambda: T + 30
    svc.ack("a1", "Петров")
    svc.clock = lambda: T + 90
    svc.resolve("a1", "Петров", "Помощь оказана")           # 90 с — в норматив
    svc.clock = lambda: T + 200
    svc.resolve("a2", "Сидоров", "")                         # 200 с — не уложился
    svc.check_escalations(T + 200)
    svc.clock = lambda: T + 300                             # a3, a4 открыты и просрочены
    sla = svc.summary()["sla"]
    assert (sla["alarms"], sla["acked"], sla["resolved"], sla["on_time"], sla["overdue"]) == (4, 2, 2, 1, 2)
    assert sla["median_ack_s"] == 115 and sla["median_resolve_s"] == 145 and sla["on_time_share"] == 0.25
    assert sla["escalated"] == 2 and sla["limit_min"] == 2                       # a3, a4: открыты к проверке
    who = {x["who"]: x for x in sla["by_who"]}
    assert who["Петров"]["on_time_share"] == 1 and who["Сидоров"]["on_time_share"] == 0
    assert who["никто не принял"]["alarms"] == 2
    assert "В норматив: 1 (25%)" in svc.summary_text(svc.summary(), sla=True)
    rows = svc.csv_alarms_shift().decode("utf-8-sig").splitlines()
    assert rows[0].endswith("в норматив 2 мин") and len(rows) == 5
    assert rows[1].split(";")[-1] == "да" and rows[2].split(";")[-1] == "нет" and "К-01 · Цех 1" in rows[1]


def test_operator_role_and_readonly_cards(svc, tmp_path):
    bot, flush = live_bot(svc)
    staff(svc, 10, "supervisor")
    staff(svc, 30, "operator")
    svc.on_event_opened(event(svc, "e1"))
    out = flush()
    by_chat = {p["chat_id"]: p for _, p in out}
    assert by_chat[10]["reply_markup"]["inline_keyboard"] and by_chat[30]["reply_markup"] == {"inline_keyboard": []}
    assert "К-01 · Цех 1" in by_chat[30]["text"]
    svc.ack("e1", "Мастер")
    edits = {p["chat_id"]: p for m, p in flush() if m == "editMessageText"}
    assert edits[10]["reply_markup"]["inline_keyboard"] and edits[30]["reply_markup"] == {"inline_keyboard": []}
    event(svc, "e2", "medium", type_="phone", title="Телефон")
    a = svc.escalate_event("e2", "user30", "звонит у конвейера")
    out = flush()
    assert [p["chat_id"] for _, p in out] == [10] and "📣 Передал user30: звонит у конвейера" in out[0][1]["text"]
    assert a["source"] == "operator" and svc.store.get("e2")["status"] == "sent_to_master"
    assert svc.check_escalations(T + 31 * 60) == ["e1", "e2"]                     # дальше — обычная тревога
    assert [x[0] for x in svc.report_for(svc.user(30))] == ["text", "doc", "doc"]   # оператору — отчёт как начальнику


def test_users_table_migrates_to_operator(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "shift.db")
    db.execute("CREATE TABLE users (tg_id INTEGER PRIMARY KEY, name TEXT, role TEXT CHECK (role IN "
               "('worker', 'supervisor', 'director')), person_id TEXT, demo INTEGER DEFAULT 1, chat_id INTEGER, created REAL)")
    db.execute("INSERT INTO users VALUES (5, 'Старый', 'supervisor', NULL, 1, 5, 0)")
    db.execute("CREATE TABLE alarms (event_id TEXT PRIMARY KEY, created REAL, title TEXT, severity TEXT, camera_title TEXT,"
               " zone_title TEXT, snapshot TEXT, status TEXT DEFAULT 'open', ack_by TEXT, ack_at REAL, resolved_by TEXT,"
               " resolved_at REAL, note TEXT, escalated_at REAL, asked_at REAL)")
    db.commit()
    db.close()
    roles(tmp_path / "roles.yaml")
    s = ShiftService(EventStore(tmp_path / "e.db"), None, tmp_path, tmp_path, tmp_path / "roles.yaml")
    assert s.user(5)["chat_id"] == 5 and s.set_role(5, "operator")["role"] == "operator"
    s.on_event_opened(event(s, "e1"))
    assert s.alarm("e1")["camera_no"] == "К-01" and s.alarm("e1")["source"] == "auto"
