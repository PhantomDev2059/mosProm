"""API мини-приложения Telegram (/api/tg/*): смена, нарушения, сотрудники, тревоги, отчёты, настройки.

Каждый запрос несёт заголовок X-Tg-Init-Data = Telegram.WebApp.initData (проверяется подписью бота).
В демо-режиме (demo: true в configs/roles.yaml) без него входит демо-пользователь: X-Demo-User, X-Demo-Role.
Подключение в api/main.py: shift = install(app, store, people, MEDIA, DATA, ROOT / "configs" / "roles.yaml").
"""
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from .shift import BOSSES, VIEWERS, ShiftService
from .store import EventStore
from .telegram import TelegramBot


class RoleIn(BaseModel):
    role: str


class BindIn(BaseModel):
    person_id: str | None = None


class NoteIn(BaseModel):
    note: str = ""


class EscalateIn(BaseModel):
    comment: str = ""


class CsvIn(BaseModel):
    scope: str = "shift"
    person: str | None = None


class SettingsIn(BaseModel):
    escalate_min: float


def make_router(svc: ShiftService) -> APIRouter:
    r = APIRouter(prefix="/api/tg")

    def user(request: Request) -> dict:
        u = svc.user_from_request(request)
        if u is None:
            if request.headers.get("x-tg-init-data") and getattr(svc.bot, "token", ""):
                raise HTTPException(403, "нет доступа: администратор должен добавить ваш Telegram ID в roles.yaml")
            raise HTTPException(401, "нужен вход через Telegram")
        return u

    def boss(u: dict = Depends(user)) -> dict:
        if u["role"] not in BOSSES:
            raise HTTPException(403, "только для начальника смены и руководителя")
        return u

    def viewer(u: dict = Depends(user)) -> dict:
        if u["role"] not in VIEWERS:
            raise HTTPException(403, "только для оператора, начальника смены и руководителя")
        return u

    def alarm_or_404(a: dict | None) -> dict:
        if a is None:
            raise HTTPException(404, "нет такой тревоги")
        return a

    @r.get("/me")
    def me(u: dict = Depends(user)):
        return svc.me(u)

    @r.post("/role")
    def role(body: RoleIn, u: dict = Depends(user)):
        try:
            return svc.me(svc.set_role(u["tg_id"], body.role))
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @r.post("/bind")
    def bind(body: BindIn, u: dict = Depends(user)):
        try:
            return svc.me(svc.bind(u["tg_id"], body.person_id))
        except KeyError as e:
            raise HTTPException(404, str(e))

    @r.get("/shift")
    def shift(since: float | None = None, u: dict = Depends(user)):
        s = svc.summary(since)
        s.pop("rows")
        s.pop("alarms")
        return s

    @r.get("/events")
    def events(person: str | None = None, camera: str | None = None, severity: str | None = None,
               limit: int = 100, since: float | None = None, u: dict = Depends(user)):
        return svc.events(person, camera, severity, min(limit, 1000), since)

    @r.post("/events/{eid}/escalate")
    def escalate(eid: str, body: EscalateIn | None = None, u: dict = Depends(viewer)):
        """«Передать начальнику смены»: любое нарушение становится тревогой с обычным жизненным циклом."""
        a = svc.escalate_event(eid, u["name"], ((body.comment if body else "") or "").strip()[:300])
        if a is None:
            raise HTTPException(404, "нет такого события")
        return a

    @r.get("/people")
    def people(since: float | None = None, u: dict = Depends(user)):
        return svc.people_list(since)

    @r.get("/people/{pid}")
    def person(pid: str, since: float | None = None, u: dict = Depends(user)):
        d = svc.person_detail(pid, since)
        if d is None:
            raise HTTPException(404, "нет такого сотрудника")
        return d

    @r.get("/alarms")
    def alarms(status: str | None = None, since: float | None = None, u: dict = Depends(viewer)):
        return svc.alarms(status, since)

    @r.post("/alarms/{eid}/ack")
    def ack(eid: str, u: dict = Depends(boss)):
        return alarm_or_404(svc.ack(eid, u["name"]))

    @r.post("/alarms/{eid}/resolve")
    def resolve(eid: str, body: NoteIn, u: dict = Depends(boss)):
        return alarm_or_404(svc.resolve(eid, u["name"], body.note.strip()[:500]))

    @r.post("/alarms/{eid}/ask")
    def ask(eid: str, u: dict = Depends(boss)):
        if u["role"] != "director":
            raise HTTPException(403, "запросить отчёт может руководитель")
        return alarm_or_404(svc.ask_report(eid, u["name"]))

    def build_csv(u: dict, scope: str, pid: str | None, since: float | None) -> tuple[str, bytes]:
        """Рабочий получает только свой CSV; оператор, начальник и руководитель — любой."""
        tag = svc.file_tag(svc.current_shift()["id"]) if since is None else "period"
        if scope == "person":
            pid = pid or u["person_id"]
            if not pid:
                raise HTTPException(400, "не указан сотрудник")
            if u["role"] not in VIEWERS and pid != u["person_id"]:
                raise HTTPException(403, "рабочий может выгрузить только свои нарушения")
            safe = "".join(c for c in pid if c.isalnum() or c in "-_")[:40]
            return f"person-{safe}-{tag}.csv", svc.csv_person(pid, since)
        if scope not in ("shift", "alarms"):
            raise HTTPException(400, "scope: shift, alarms или person")
        if u["role"] not in VIEWERS:
            raise HTTPException(403, "журнал смены — для оператора, начальника смены и руководителя")
        if scope == "alarms":
            return f"alarms-{tag}.csv", svc.csv_alarms_shift(since)
        return f"shift-{tag}.csv", svc.csv_shift(since)

    @r.get("/csv")
    def csv_file(scope: str = "shift", person: str | None = None, since: float | None = None, u: dict = Depends(user)):
        name, data = build_csv(u, scope, person, since)
        return Response(data, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    @r.post("/csv/send")
    def csv_send(body: CsvIn, u: dict = Depends(user)):
        name, data = build_csv(u, body.scope, body.person, None)
        if svc.bot is None or not svc.bot.enabled:
            return {"ok": False, "reason": "бот не подключён"}
        if not u["chat_id"]:
            return {"ok": False, "reason": "нет чата с ботом: откройте бота и нажмите /start"}
        return {"ok": bool(svc.bot.send_document(u["chat_id"], name, data, "Отчёт LinQ Guard"))}

    @r.post("/shift/close")
    def shift_close(u: dict = Depends(boss)):
        return {"sent": svc.close_shift(u["name"])}

    @r.get("/settings")
    def settings(u: dict = Depends(user)):
        return svc.settings()

    @r.post("/settings")
    def settings_set(body: SettingsIn, u: dict = Depends(boss)):
        try:
            return svc.set_escalate_min(body.escalate_min)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @r.post("/demo/reset")
    def demo_reset(u: dict = Depends(boss)):
        """Демо: все тревоги и их сообщения в чатах — долой (журнал событий не трогает)."""
        if not svc.demo:
            raise HTTPException(403, "сброс тревог — только в демо-режиме")
        return {"alarms": svc.reset_alarms()}

    return r


def install(app: FastAPI, store: EventStore, people, media: Path, data: Path, roles_file: Path) -> ShiftService:
    """Сервис смены + бот + маршруты /api/tg. Дальше main.py зовёт service.on_event_opened(row) на каждое новое
    событие (или оборачивает Notifier: notifier = service.chain(notifier))."""
    svc = ShiftService(store, people, media, data, roles_file)
    svc.bot = TelegramBot(svc)
    app.include_router(make_router(svc))
    svc.bot.start()
    svc.start()
    return svc
