"""Веб-сервис: камеры, лента и галерея нарушений, журнал, аналитика, зоны, вебкамера, метрики.

Запуск: uvicorn api.main:app --host 0.0.0.0 --port 8000
"""
import asyncio
import csv
import io
import os
import time
from pathlib import Path

import cv2
import numpy as np
import yaml
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engine.config import CONFIRM_DEFAULT, SEVERITY_DEFAULT, load_cameras, parse_camera
from engine.rules import RULES

from .notify import Notifier
from .people import PeopleIndex
from .tg_routes import install as install_shift
from .review import PRESETS, ReviewManager
from .store import STATUSES, EventStore
from .workers import SourceProcessor, VideoWorker, camera_detectors, load_detectors

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("LINQ_DATA", "/workspace/linq/data"))
MODELS = Path(os.environ.get("LINQ_MODELS", "/workspace/linq/models"))
CONFIGS = Path(os.environ.get("LINQ_CONFIGS", ROOT / "configs" / "cameras"))
MEDIA = DATA / "media"
MEDIA.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="LinQ Guard")

# За прокси Jupyter (стенд хакатона) сайт живёт под префиксом /user/<логин>/proxy/8000, а фронтенд
# и API используют абсолютные пути. LINQ_BASE — этот префикс: дописываем его в HTML/JS/JSON-ответы.
_BASE = os.environ.get("LINQ_BASE", "").rstrip("/")
if _BASE:
    import re as _re
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response as _Resp
    _PATHS = _re.compile(rb"([\"'`(=])/(api|ws|static|media|clips|snapshots|data|files)/")

    class _Prefix(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            resp = await call_next(request)
            ctype = resp.headers.get("content-type", "")
            if not any(t in ctype for t in ("html", "javascript", "json", "css")):
                return resp
            body = b"".join([chunk async for chunk in resp.body_iterator])
            body = _PATHS.sub(lambda m: m.group(1) + _BASE.encode() + b"/" + m.group(2) + b"/", body)
            headers = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
            return _Resp(body, status_code=resp.status_code, headers=headers, media_type=None)

    app.add_middleware(_Prefix)
store = EventStore(DATA / "events.db")
people = PeopleIndex(DATA / "people.db", MEDIA, MODELS)      # опознание нарушителей по лицу (YuNet + SFace)

# Через публичный HTTPS-туннель (мини-апп Telegram) наружу видны только мини-апп, его API, кадры и снимки;
# дашборд, журнал, зоны и разбор смены — только через SSH-туннель.
PUBLIC_PATHS = ("/app/", "/api/tg/", "/media/")


@app.middleware("http")
async def public_guard(request: Request, call_next):
    if request.headers.get("cf-connecting-ip") or request.headers.get("host", "").endswith(".trycloudflare.com"):
        path = request.url.path
        frame = path.startswith("/api/cameras/") and path.endswith("/frame.jpg")
        if not (path == "/app" or path.startswith(PUBLIC_PATHS) or frame):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
    response = await call_next(request)
    if request.url.path.startswith("/app"):        # Telegram держит мини-апп в кэше — без этого видна старая версия
        response.headers["Cache-Control"] = "no-cache"
    return response


class Hub:
    """Рассылка событий всем открытым вкладкам по WebSocket; вызывается из потоков камер."""

    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def publish(self, msg: dict) -> None:
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(self._send(msg), self.loop)

    async def _send(self, msg: dict) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                self.clients.discard(ws)


hub = Hub()
detectors = load_detectors(MODELS / "registry.json", os.environ.get("LINQ_BACKEND", "onnx"))
processors: dict[str, SourceProcessor] = {}
workers: dict[str, VideoWorker] = {}


def _models_for(cfg) -> list:
    names = cfg.raw.get("models") or list(detectors)
    return camera_detectors(names, detectors, MODELS)


def _resolve(source: str) -> str:
    if source.startswith(("rtsp://", "http://", "https://", "/")):
        return source
    return str(DATA / "videos" / source)


# смена, роли, тревоги и бот Telegram с мини-аппом (api/shift.py, api/telegram.py, api/tg_routes.py);
# каждое новое событие уходит и в прежний канал оповещений, и в сервис смены
shift = install_shift(app, store, people, MEDIA, DATA, ROOT / "configs" / "roles.yaml")
notifier = shift.chain(Notifier(MEDIA))


def start_camera(cfg) -> None:
    proc = SourceProcessor(cfg, _models_for(cfg), store, MEDIA, hub.publish, notifier, people)
    processors[cfg.id] = proc
    if cfg.source != "webcam":
        w = VideoWorker(proc, _resolve(cfg.source))
        workers[cfg.id] = w
        w.start()


@app.on_event("startup")
async def startup() -> None:
    hub.loop = asyncio.get_running_loop()
    for cfg in load_cameras(CONFIGS):
        start_camera(cfg)
    if "webcam" not in processors:
        start_camera(parse_camera({
            "camera": "webcam", "title": "Вебкамера", "source": "webcam", "analysis_fps": 5,
            "models": ["pose", "coco", "ppe2", "cigarette", "fire"],
            "rules": [{"type": t} for t in ("fall", "immobile", "no_helmet", "phone", "smoking", "open_fire", "running")],
        }))


# ---------- камеры ----------
@app.get("/api/cameras")
def cameras():
    out = []
    for cid, p in processors.items():
        cfg = p.cfg
        out.append({
            "id": cid, "title": cfg.title, "source": cfg.source, "analysis_fps": cfg.analysis_fps,
            "models": [d.spec.name for d in p.pipeline.detectors],
            "zones": [{"id": z.id, "title": z.title, "polygon": z.polygon.tolist() if z.polygon is not None else None,
                       "exclude": sorted(z.exclude),
                       "rules": [{"type": r.type, "confirm_s": r.confirm_s, "severity": r.severity,
                                  "schedule": f"{r.schedule.start:%H:%M}-{r.schedule.end:%H:%M}" if r.schedule else None,
                                  **r.params} for r in z.rules]} for z in cfg.scene.zones],
            "stats": p.stats(),
            "size": list(p.latest_raw.shape[1::-1]) if p.latest_raw is not None else None,
        })
    return out


@app.get("/api/cameras/{cid}/frame.jpg")
def camera_frame(cid: str):
    p = processors.get(cid)
    if p is None or p.latest_raw is None:
        raise HTTPException(404, "кадра ещё нет")
    ok, buf = cv2.imencode(".jpg", p.latest_raw, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return Response(buf.tobytes(), media_type="image/jpeg")


@app.get("/stream/{cid}")
async def stream(cid: str):
    p = processors.get(cid)
    if p is None:
        raise HTTPException(404, "нет такой камеры")

    async def gen():
        last = None
        while True:
            jpg = p.latest_jpeg
            if jpg is not None and jpg is not last:
                last = jpg
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            await asyncio.sleep(0.05)

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/api/cameras/{cid}/heat")
def camera_heat(cid: str, hours: float = 24 * 7):
    """Точки нарушений на кадре камеры: низ рамки человека (где стоял) или центр объекта."""
    pts = []
    for r in store.query(camera=cid, since=time.time() - hours * 3600, limit=20000):
        b = r.get("box")
        if not b or b[2] <= b[0]:
            continue
        y = b[3] if r["type"] not in ("open_fire", "smoke") else (b[1] + b[3]) / 2
        pts.append({"x": (b[0] + b[2]) / 2, "y": y, "severity": r["severity"], "type": r["title"]})
    return pts


class ZonesIn(BaseModel):
    zones: list[dict]


@app.put("/api/cameras/{cid}/zones")
def save_zones(cid: str, body: ZonesIn):
    p = processors.get(cid)
    if p is None:
        raise HTTPException(404, "нет такой камеры")
    raw = dict(p.cfg.raw)
    raw["zones"] = body.zones
    raw.pop("rules", None)
    try:
        cfg = parse_camera(raw)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    if cid != "webcam":
        (CONFIGS / f"{cid}.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    p.rebuild(cfg)
    return {"ok": True}


@app.get("/api/rules")
def rules():
    return [{"type": t, "title": c.title, "target": c.target, "severity": SEVERITY_DEFAULT.get(t, "high"),
             "confirm_s": CONFIRM_DEFAULT.get(t, 2.0)} for t, c in RULES.items()]


# ---------- события ----------
def _window(since: float | None, until: float | None, hours: float | None) -> tuple[float | None, float | None]:
    """Период журнала: явные границы since/until (секунды Unix) важнее «последних N часов»."""
    if since is None and hours:
        since = time.time() - hours * 3600
    return since, until


@app.get("/api/events")
def events(type: str | None = None, camera: str | None = None, zone: str | None = None, severity: str | None = None,
           status: str | None = None, hours: float | None = None, since: float | None = None,
           until: float | None = None, limit: int = 300):
    since, until = _window(since, until, hours)
    return store.query(type, camera, severity, status, since, until, limit, zone=zone)


class StatusIn(BaseModel):
    status: str
    who: str = "оператор"


@app.post("/api/events/{eid}/status")
def set_status(eid: str, body: StatusIn):
    if body.status not in STATUSES:
        raise HTTPException(400, f"статус должен быть одним из {STATUSES}")
    ev = store.set_status(eid, body.status, body.who)
    if ev is None:
        raise HTTPException(404, "нет такого события")
    hub.publish({"kind": "event_status", "event": ev})
    return ev


@app.get("/api/events.csv")
def events_csv(type: str | None = None, camera: str | None = None, zone: str | None = None,
               severity: str | None = None, status: str | None = None, hours: float | None = None,
               since: float | None = None, until: float | None = None, tz: int | None = None):
    """Те же фильтры, что у журнала; без периода — последняя неделя, как раньше.
    tz — смещение часового пояса браузера в минутах, чтобы время в файле совпало с журналом на экране."""
    if since is None and until is None and hours is None:
        hours = 24 * 7
    since, until = _window(since, until, hours)
    rows = store.query(type, camera, severity, status, since, until, 100000, zone=zone)
    stamp = (lambda t: time.gmtime(t + tz * 60)) if tz is not None else time.localtime
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["id", "время", "камера", "зона", "нарушение", "уровень", "длительность, с", "статус", "кадр"])
    for r in rows:
        w.writerow([r["id"], time.strftime("%d.%m.%Y %H:%M:%S", stamp(r["started_at"])), r["camera_title"],
                    r["zone_title"], r["title"], r["severity"], r["duration_s"], r["status"], r["snapshot"]])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=journal.csv"})


@app.get("/api/stats")
def stats(hours: float = 24 * 7, since: float | None = None, until: float | None = None):
    return store.stats(*_window(since, until, hours))


RECOMMEND = {
    "no_vest": "провести инструктаж по сигнальным жилетам и проверить выдачу СИЗ",
    "no_helmet": "провести инструктаж по каскам и поставить контроль на входе в зону",
    "vehicle_proximity": "разделить пешеходные и транспортные потоки, разметить проезды",
    "running": "проверить, не торопят ли людей сроки, разметить безопасные маршруты",
    "phone": "ввести правило «без телефона в рабочей зоне», оборудовать места для звонков",
    "smoking": "напомнить о местах для курения, проверить знаки",
    "open_fire": "проверить наряды-допуски на огневые работы",
    "smoke": "проверить вентиляцию и источники дыма",
    "fall": "проверить покрытие пола, освещение и уборку на участке",
    "immobile": "отработать с диспетчером порядок вызова медпункта",
    "zone_intrusion": "проверить ограждения и знаки опасной зоны",
}


def plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    word = one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many
    return f"{n} {word}"


@app.get("/api/summary")
def summary(hours: float = 12):
    """Сводка смены текстом по журналу: без языковой модели, только факты и правила рекомендаций."""
    since = time.time() - hours * 3600
    rows = store.query(since=since, limit=100000)
    if not rows:
        return {"text": "За выбранный период нарушений не зафиксировано."}
    s = store.stats(since)
    t0 = time.strftime("%d.%m %H:%M", time.localtime(min(r["started_at"] for r in rows)))
    t1 = time.strftime("%d.%m %H:%M", time.localtime(max(r["started_at"] for r in rows)))
    sev = s["by_severity"]
    lines = [f"Период: {t0} — {t1}. Зафиксировано {plural(s['total'], 'нарушение', 'нарушения', 'нарушений')}: "
             f"критичных {sev.get('critical', 0)}, высокого уровня {sev.get('high', 0)}, среднего {sev.get('medium', 0)}."]
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    top = sorted(by_type.items(), key=lambda kv: -kv[1])[:3]
    titles = {r["type"]: r["title"] for r in rows}
    lines.append("Чаще всего: " + "; ".join(f"{titles[t]} — {n} ({round(100 * n / s['total'])}%)" for t, n in top) + ".")
    cam, cam_n = max(s["by_camera"].items(), key=lambda kv: kv[1])
    cam_types: dict[str, int] = {}
    for r in rows:
        if r["camera_title"] == cam:
            cam_types[r["title"]] = cam_types.get(r["title"], 0) + 1
    lines.append(f"Самый проблемный участок: {cam} — {plural(cam_n, 'нарушение', 'нарушения', 'нарушений')}, "
                 f"в основном «{max(cam_types, key=cam_types.get)}».")
    hours_count = [sum(day[h] for day in s["heat"]) for h in range(24)]
    peak = max(range(24), key=lambda h: hours_count[h])
    lines.append(f"Пик нарушений: {peak:02d}:00–{(peak + 1) % 24:02d}:00.")
    if s["latency_p50"] is not None:
        lines.append(f"Медианное время от начала нарушения до оповещения: {s['latency_p50']:.1f} с.")
    unmarked = s["by_status"].get("new", 0)
    marked = s["total"] - unmarked
    lines.append(f"Отмечено оператором {marked} из {s['total']}"
                 + (f", точность по отметкам {round(s['precision'] * 100)}%." if s["precision"] is not None else "."))
    recs = [f"по «{titles[t]}»: {RECOMMEND[t]}" for t, _ in top if t in RECOMMEND]
    if unmarked > s["total"] / 2:
        recs.append(f"разобрать {plural(unmarked, 'событие', 'события', 'событий')} без отметки, "
                    "чтобы система точнее настроила пороги")
    lines.append("Рекомендации: " + "; ".join(recs) + ".")
    return {"text": "\n".join(lines)}


@app.get("/api/metrics")
def metrics():
    return {"models": [d.stats() for d in detectors.values()],
            "cameras": [p.stats() for p in processors.values()],
            "notify": {"channels": notifier.channels, "levels": sorted(notifier.levels), "recent": notifier.history[-10:]},
            "gpu_backend": next(iter(detectors.values())).backend.name if detectors else None}


# ---------- разбор смены ----------
review = ReviewManager(DATA / "review.db", MEDIA, DATA / "videos", DATA / "uploads", detectors)


@app.get("/api/review/presets")
def review_presets():
    return [{"id": k, "title": v["title"], "rules": v["rules"]} for k, v in PRESETS.items()]


@app.get("/api/review/sources")
def review_sources():
    return review.sources()


@app.post("/api/review/upload")
async def review_upload(request: Request, name: str):
    """Загрузка записи сырым потоком (без multipart): ?name=имя_файла.mp4."""
    safe = "".join(c for c in Path(name).stem if c.isalnum() or c in "-_ ").strip() or "upload"
    path = review.uploads / f"{safe[:60]}-{int(time.time())}.mp4"
    with path.open("wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
    return {"path": str(path), "name": path.name, "size_mb": round(path.stat().st_size / 1e6, 1)}


class ReviewIn(BaseModel):
    source: str
    preset: str = "universal"
    title: str | None = None


@app.post("/api/review/jobs")
def review_start(body: ReviewIn):
    try:
        return {"id": review.start(body.source, body.preset, body.title)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/review/jobs")
def review_jobs():
    return review.jobs_list()


@app.delete("/api/review/jobs/{job_id}")
def review_delete(job_id: str):
    try:
        return {"ok": review.delete(job_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/review/jobs/{job_id}/items")
def review_items(job_id: str):
    return review.items(job_id)


class VerdictIn(BaseModel):
    verdict: str | None = None
    person: str | None = None
    note: str | None = None


@app.post("/api/review/items/{item_id}")
def review_verdict(item_id: str, body: VerdictIn):
    try:
        item = review.set_verdict(item_id, body.verdict, body.person, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if item is None:
        raise HTTPException(404, "нет такого ролика")
    return item


@app.get("/api/review/jobs/{job_id}/report.csv")
def review_csv(job_id: str):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["время в записи", "нарушение", "уровень", "длительность, с", "уверенность", "решение", "сотрудник", "комментарий", "ролик"])
    for r in review.items(job_id):
        w.writerow([_hms(r["start_s"]), r["title"], r["severity"], round(r["end_s"] - r["start_s"], 1), r["score"],
                    {"confirmed": "подтверждено", "rejected": "отклонено"}.get(r["verdict"], "не проверено"),
                    r["person"] or "", r["note"] or "", r["clip"]])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=shift-{job_id}.csv"})


def _hms(s: float) -> str:
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


@app.get("/api/linq")
def linq_programs():
    """Отчёты компиляции моделей в программы .tpu (linq/compile_tpu.py) — та версия, что пойдёт на стенд."""
    import json as _json
    out = {}
    for folder, tag in ((MODELS / "tpu", "v1"), (MODELS / "tpu_v2", "v2")):     # v2, где есть, заменяет v1
        for p in sorted(folder.glob("*_report.json")):
            try:
                out[p.name] = _json.loads(p.read_text()) | {"version": tag}
            except ValueError:
                continue
    return list(out.values())


@app.get("/api/quality")
def quality():
    """Результаты оценки качества: падения на UR Fall, точность после квантования, детектор СИЗ."""
    import json as _json

    def load(p: Path):
        try:
            return _json.loads(p.read_text())
        except (OSError, ValueError):
            return None

    sweep = load(DATA / "eval" / "fall_sweep.json")
    fall_ml = load(DATA / "eval" / "fall_ml.json")
    quant = {}
    for folder, tag in ((MODELS / "tpu", "v1"), (MODELS / "tpu_v2", "v2")):
        for p in folder.glob("*_report.json"):
            r = load(p) or {}
            calib = {"preset": r.get("preset"), "smart": r.get("smart_calib"), "finetune": r.get("finetune_epochs"),
                     "images": r.get("calib_images")}
            if r.get("quant_check"):
                quant.setdefault(r["name"], {})[tag] = r["quant_check"] | calib
            if r.get("quant_check_images"):             # та же программа на тестовых снимках набора, а не на кадрах камер
                quant.setdefault(r["name"], {})[f"{tag} · снимки набора"] = r["quant_check_images"] | calib
    ppe = None
    res = Path("/workspace/linq/train/ppe_v8s_test")
    if (res / "metrics.json").exists():
        ppe = load(res / "metrics.json")
    return {"fall": sweep, "fall_ml": fall_ml, "quant": quant, "ppe": ppe,
            "ppe_compare": load(DATA / "eval" / "ppe_compare.json")}


@app.get("/api/bench")
def bench():
    """Замеры linq/bench.py по платформам: tpu (стенд LinQ), cuda, cpu."""
    import json as _json
    out = {}
    for p in sorted((MODELS / "bench").glob("*.json")):
        try:
            out[p.stem] = _json.loads(p.read_text())
        except ValueError:
            continue
    return out


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    hub.clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.clients.discard(ws)


@app.websocket("/ws/frames")
async def ws_frames(ws: WebSocket):
    """Кадры всех камер по одному соединению: браузер держит не больше 6 HTTP-потоков на сервер.

    Сообщение: 32 байта ID камеры (ASCII, дополнено пробелами) + JPEG. Клиент присылает {"cams": [...], "full": id|null}.
    """
    await ws.accept()
    want: dict = {"cams": list(processors), "full": None}
    sent: dict[str, bytes] = {}

    async def reader():
        while True:
            msg = await ws.receive_json()
            want["cams"] = msg.get("cams", want["cams"])
            want["full"] = msg.get("full")

    task = asyncio.create_task(reader())
    try:
        while not task.done():
            for cid in list(want["cams"]):
                p = processors.get(cid)
                jpg = None if p is None else (p.latest_jpeg if cid == want["full"] else p.latest_preview)
                if jpg is not None and sent.get(cid) is not jpg:
                    sent[cid] = jpg
                    await ws.send_bytes(cid.encode()[:32].ljust(32) + jpg)
            await asyncio.sleep(0.08)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        task.cancel()


@app.websocket("/ws/webcam")
async def ws_webcam(ws: WebSocket):
    """Браузер шлёт JPEG-кадры с вебкамеры, сервер возвращает размеченный кадр."""
    await ws.accept()
    proc = processors["webcam"]
    loop = asyncio.get_running_loop()
    try:
        while True:
            data = await ws.receive_bytes()
            frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue
            now = time.time() - proc._clock_offset
            jpg = await loop.run_in_executor(None, proc.process, frame, now)
            await ws.send_bytes(jpg)
    except WebSocketDisconnect:
        pass


# ---------- лайв-режим: загруженная запись идёт как живая камера ----------
LIVE_RULES = ("fall", "immobile", "no_helmet", "no_vest", "vehicle_proximity", "running", "phone", "smoking",
              "open_fire", "smoke")
live: dict[str, dict] = {}


def _live_user(request: Request) -> dict:
    """Загружать запись может начальник смены или руководитель (проверка — через сервис смены)."""
    user = shift.user_from_request(request) if shift is not None else None
    if user is None or user.get("role") not in ("supervisor", "director"):
        raise HTTPException(403, "запись загружает начальник смены или руководитель")
    return user


@app.post("/api/tg/live/upload")
async def live_upload(request: Request, name: str):
    """Запись с камеры — сырым потоком (?name=файл.mp4): воспроизводится в реальном времени как живая камера,
    тревоги уходят в бот так же, как с настоящих камер."""
    _live_user(request)
    safe = "".join(c for c in Path(name).stem if c.isalnum() or c in "-_ ").strip()[:40] or "запись"
    n = len(live) + 1
    cid = f"live-{int(time.time())}"
    path = DATA / "uploads" / f"{cid}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
    cap = cv2.VideoCapture(str(path))
    ok = cap.isOpened() and cap.read()[0]
    cap.release()
    if not ok:
        path.unlink(missing_ok=True)
        raise HTTPException(400, "не похоже на видеофайл")
    title = f"Лайв {n}: {safe}"
    cfg = parse_camera({"camera": cid, "title": title, "source": str(path), "analysis_fps": 5, "loop": False,
                        "models": ["pose", "ppe2", "ppe", "forklift", "fire", "cigarette", "coco"],
                        "rules": [{"type": t} for t in LIVE_RULES]})
    start_camera(cfg)
    live[cid] = {"camera_id": cid, "title": title, "started": time.time()}
    return {"camera_id": cid, "title": title}


@app.get("/api/tg/thumb")
def tg_thumb(src: str, w: int = 480):
    """Уменьшенный кадр для списков мини-аппа: через туннель 1280-пиксельные снимки идут медленно."""
    name = Path(src).name
    w = max(120, min(int(w), 960))
    path = MEDIA / name
    if not name or not path.is_file():
        raise HTTPException(404, "нет кадра")
    out = MEDIA / "thumbs" / f"{w}-{name}"
    if not out.exists() or out.stat().st_mtime < path.stat().st_mtime:   # кадр события перезаписывается лучшим
        img = cv2.imread(str(path))
        if img is None:
            raise HTTPException(404, "нет кадра")
        out.parent.mkdir(exist_ok=True)
        small = cv2.resize(img, (w, round(img.shape[0] * w / img.shape[1])), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(out), small, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return FileResponse(out, headers={"Cache-Control": "public, max-age=60"})


@app.get("/api/tg/live")
def live_list():
    out = []
    for cid, info in sorted(live.items(), key=lambda kv: -kv[1]["started"]):
        w = workers.get(cid)
        progress = round(w.position / w.duration, 3) if w and w.duration else 0.0
        out.append(info | {"status": "done" if (w is None or w.finished) else "playing", "progress": min(progress, 1.0)})
    return out


app.mount("/media", StaticFiles(directory=MEDIA), name="media")
app.mount("/static", StaticFiles(directory=ROOT / "api" / "static"), name="static")
app.mount("/app", StaticFiles(directory=ROOT / "api" / "static" / "tg", html=True, check_dir=False), name="tg")


@app.get("/")
def index():
    return FileResponse(ROOT / "api" / "static" / "index.html")
