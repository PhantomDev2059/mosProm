"""Мгновенные оповещения ответственному о самых опасных событиях (режим Live).

Каналы включаются переменными окружения:
- LINQ_TG_TOKEN и LINQ_TG_CHAT — Telegram-бот: кадр с подписью сразу, затем ролик ±4 с, когда он запишется;
- LINQ_WEBHOOK_URL — POST JSON о событии: так подключается корпоративный мессенджер или СКУД;
- LINQ_NOTIFY_SEVERITY — какие уровни слать (по умолчанию только critical).
Отправка идёт в отдельном потоке: медленная сеть не тормозит обработку камер.
"""
import json
import logging
import os
import queue
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("linqguard.notify")


class Notifier:
    def __init__(self, media: Path):
        self.media = media
        self.token = os.environ.get("LINQ_TG_TOKEN", "").strip()
        self.chat = os.environ.get("LINQ_TG_CHAT", "").strip()
        self.webhook = os.environ.get("LINQ_WEBHOOK_URL", "").strip()
        self.levels = set(os.environ.get("LINQ_NOTIFY_SEVERITY", "critical").split(","))
        self.sent: dict[str, float] = {}
        self.q: queue.Queue = queue.Queue(maxsize=200)
        self.history: list[dict] = []
        threading.Thread(target=self._loop, daemon=True, name="notify").start()

    @property
    def channels(self) -> list[str]:
        return [name for name, on in (("telegram", self.token and self.chat), ("webhook", self.webhook)) if on]

    def event_opened(self, ev: dict) -> None:
        if ev["severity"] in self.levels and self.channels:
            self.sent[ev["id"]] = time.time()
            self._put(("open", ev))

    def clip_ready(self, ev: dict) -> None:
        if ev["id"] in self.sent and self.token and self.chat:
            self._put(("clip", ev))

    def _put(self, item) -> None:
        try:
            self.q.put_nowait(item)
        except queue.Full:
            log.warning("очередь оповещений переполнена, событие %s пропущено", item[1]["id"])

    def _loop(self) -> None:
        while True:
            kind, ev = self.q.get()
            try:
                if kind == "open":
                    if self.token and self.chat:
                        self._tg_file("sendPhoto", "photo", ev["snapshot"], self._caption(ev))
                    if self.webhook:
                        self._post_json(self.webhook, {"kind": "violation", **{k: ev[k] for k in (
                            "id", "camera", "camera_title", "zone_title", "type", "title", "severity", "started_at",
                            "latency_s", "snapshot")}})
                elif kind == "clip" and ev.get("clip"):
                    self._tg_file("sendVideo", "video", ev["clip"], f"Ролик: {ev['title']}, {ev['camera_title']}")
                self.history.append({"at": time.time(), "kind": kind, "event": ev["id"], "ok": True})
            except Exception as e:
                log.warning("оповещение %s не отправлено: %s", ev["id"], e)
                self.history.append({"at": time.time(), "kind": kind, "event": ev["id"], "ok": False, "error": str(e)[:200]})
            self.history = self.history[-100:]

    @staticmethod
    def _caption(ev: dict) -> str:
        t = time.strftime("%H:%M:%S", time.localtime(ev["started_at"]))
        return (f"КРИТИЧНО: {ev['title']}\n{ev['camera_title']} · {ev['zone_title']} · {t}\n"
                f"Тревога через {ev['latency_s']} с после начала нарушения")

    def _tg_file(self, method: str, field: str, media_url: str, caption: str) -> None:
        path = self.media / Path(media_url).name
        boundary = f"linq{int(time.time() * 1000)}"
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{self.chat}\r\n'.encode(),
                 f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode(),
                 f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'
                 f'Content-Type: application/octet-stream\r\n\r\n'.encode() + path.read_bytes() + b"\r\n",
                 f"--{boundary}--\r\n".encode()]
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{method}", data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            if r.status != 200:
                raise RuntimeError(f"Telegram ответил {r.status}")

    @staticmethod
    def _post_json(url: str, payload: dict) -> None:
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False, default=float).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status >= 300:
                raise RuntimeError(f"вебхук ответил {r.status}")
