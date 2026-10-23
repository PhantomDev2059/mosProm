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
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engine.config import CONFIRM_DEFAULT, SEVERITY_DEFAULT, load_cameras, parse_camera
from engine.rules import RULES

from .store import STATUSES, EventStore
from .workers import SourceProcessor, VideoWorker, load_detectors

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("LINQ_DATA", "/workspace/linq/data"))
MODELS = Path(os.environ.get("LINQ_MODELS", "/workspace/linq/models"))
CONFIGS = Path(os.environ.get("LINQ_CONFIGS", ROOT / "configs" / "cameras"))
MEDIA = DATA / "media"
MEDIA.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="LinQ Guard")
store = EventStore(DATA / "events.db")


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
detectors = load_detectors(MODELS / "registry.json")
processors: dict[str, SourceProcessor] = {}
workers: dict[str, VideoWorker] = {}


def _models_for(cfg) -> list:
    names = cfg.raw.get("models") or list(detectors)
    return [detectors[n] for n in names if n in detectors]


def _resolve(source: str) -> str:
    if source.startswith(("rtsp://", "http://", "https://", "/")):
        return source
    return str(DATA / "videos" / source)


def start_camera(cfg) -> None:
    proc = SourceProcessor(cfg, _models_for(cfg), store, MEDIA, hub.publish)
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
            "models": ["pose", "coco", "ppe", "cigarette", "fire"],
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
@app.get("/api/events")
def events(type: str | None = None, camera: str | None = None, severity: str | None = None,
           status: str | None = None, hours: float | None = None, limit: int = 300):
    since = time.time() - hours * 3600 if hours else None
    return store.query(type, camera, severity, status, since, None, limit)


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
def events_csv(hours: float = 24 * 7):
    rows = store.query(since=time.time() - hours * 3600, limit=100000)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["id", "время", "камера", "зона", "нарушение", "уровень", "длительность, с", "статус", "кадр"])
    for r in rows:
        w.writerow([r["id"], time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(r["started_at"])), r["camera_title"],
                    r["zone_title"], r["title"], r["severity"], r["duration_s"], r["status"], r["snapshot"]])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=journal.csv"})


@app.get("/api/stats")
def stats(hours: float = 24 * 7):
    return store.stats(time.time() - hours * 3600)


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
            "gpu_backend": next(iter(detectors.values())).backend.name if detectors else None}


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


app.mount("/media", StaticFiles(directory=MEDIA), name="media")
app.mount("/static", StaticFiles(directory=ROOT / "api" / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "api" / "static" / "index.html")
