"""Telegram-бот смены поверх Bot API (httpx, long polling): тревоги начальникам, эскалация руководителю, отчёты.

Токен — LINQ_TG_BOT_TOKEN или файл <LINQ_SECRETS>/tg_bot_token (по умолчанию /workspace/linq/secrets).
Публичный HTTPS-адрес мини-приложения — LINQ_PUBLIC_URL или файл <LINQ_SECRETS>/public_url; перечитывается
при каждом использовании: туннель может перезапуститься с новым адресом. Без токена бот выключен, остальное работает.
Все отправки идут через очередь в отдельном потоке — потоки камер никогда не ждут сеть.
"""
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path

from .shift import BOSSES, ROLE_TITLES, ROLES, ShiftService, hm, user_name

log = logging.getLogger("linqguard.telegram")

NOTES = ("Помощь оказана", "Очаг потушен", "Опасность устранена", "Ложная тревога")
LEVELS = {"critical": "🚨 КРИТИЧНО", "high": "⚠️ ОПАСНО", "medium": "ℹ️ ВНИМАНИЕ"}
COMMANDS = [("start", "Начать и открыть приложение"), ("role", "Выбрать роль (демо)"),
            ("shift", "Сводка текущей смены"), ("report", "Отчёт по смене (CSV)"),
            ("clear", "Убрать старые сообщения бота"), ("help", "Что умеет бот")]
AUX_TTL = 60              # служебные сообщения (подтверждения, ошибки, подсказки) живут столько секунд
DELETE_MAX_AGE = 47.5 * 3600   # Telegram даёт боту удалять свои сообщения не старше 48 ч


class TelegramBot:
    def __init__(self, service: ShiftService, secrets: Path | None = None):
        self.svc = service
        self.secrets = Path(secrets or os.environ.get("LINQ_SECRETS", "/workspace/linq/secrets"))
        self.token = os.environ.get("LINQ_TG_BOT_TOKEN", "").strip() or self._secret("tg_bot_token")
        self.enabled = bool(self.token)
        self.username: str | None = None
        self.q: queue.Queue = queue.Queue(maxsize=1000)
        self.pending_note: dict[int, str] = {}          # chat_id → тревога, для которой ждём текст отчёта
        self._menu_url: str | None = None
        self.resolved_ttl = float(os.environ.get("LINQ_TG_RESOLVED_TTL", "120"))   # итоговая карточка живёт столько с
        self.history: list[dict] = []

    def _secret(self, name: str) -> str:
        try:
            return (self.secrets / name).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    # ---------- адреса мини-приложения ----------
    def public_url(self) -> str:
        return (os.environ.get("LINQ_PUBLIC_URL", "").strip() or self._secret("public_url")).rstrip("/")

    def app_url(self, frag: str = "") -> str | None:
        """Кнопка web_app требует HTTPS; без публичного адреса кнопки приложения просто не показываем."""
        base = self.public_url()
        return f"{base}/app/{frag}" if base.startswith("https://") else None

    def app_button(self, text: str = "Открыть в приложении", frag: str = "") -> list[dict]:
        url = self.app_url(frag)
        return [{"text": text, "web_app": {"url": url}}] if url else []

    # ---------- запуск ----------
    def start(self) -> None:
        if not self.enabled:
            log.info("Telegram-бот выключен: нет LINQ_TG_BOT_TOKEN и %s/tg_bot_token", self.secrets)
            return
        import httpx                                     # только на сервере: локально без бота модуль не нужен
        self.api = f"https://api.telegram.org/bot{self.token}"
        self.http_send = httpx.Client(timeout=30)
        self.http_poll = httpx.Client(timeout=40)
        threading.Thread(target=self._send_loop, daemon=True, name="tg-send").start()
        threading.Thread(target=self._poll_loop, daemon=True, name="tg-poll").start()
        threading.Thread(target=self._clean_loop, daemon=True, name="tg-clean").start()

    def _call(self, method: str, params: dict | None = None, files: dict | None = None, client=None):
        data = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                for k, v in (params or {}).items() if v is not None}
        for attempt in range(3):
            r = (client or self.http_send).post(f"{self.api}/{method}", data=data, files=files)
            body = r.json()
            if body.get("ok"):
                return body["result"]
            retry = (body.get("parameters") or {}).get("retry_after")
            if r.status_code == 429 and retry and attempt < 2:
                time.sleep(float(retry))
                continue
            raise RuntimeError(f"{method}: {body.get('description', r.status_code)}")

    def _put(self, method: str, params: dict, files: dict | None = None, remember: dict | None = None) -> int:
        """В очередь отправки; remember — что записать в bot_messages после ответа Telegram (вид, тревога, срок)."""
        if not self.enabled or ("chat_id" in params and not params["chat_id"]):
            return 0
        try:
            self.q.put_nowait((method, params, files, remember))
            return 1
        except queue.Full:
            log.warning("очередь Telegram переполнена, %s в %s пропущено", method, params.get("chat_id"))
            return 0

    def _process(self, item: tuple) -> None:
        method, params, files, remember = item
        try:
            if files:                                    # файлы читаются здесь, а не в потоке камеры
                files = {k: (n, v() if callable(v) else v) for k, (n, v) in files.items()}
            res = self._call(method, params, files)
            if remember and isinstance(res, dict) and "message_id" in res:
                self.svc.msg_add(params["chat_id"], res["message_id"], **remember)
            ok, err = True, None
        except Exception as e:
            ok, err = False, str(e)[:200]
            quiet = method.startswith("delete") or (method.startswith("edit") and any(
                x in err for x in ("not modified", "not found", "can't be edited")))
            if not quiet:                                # удалить старое/уже удалённое — не ошибка
                log.warning("Telegram %s не отправлено: %s", method, e)
        self.history = (self.history + [{"at": time.time(), "method": method, "ok": ok, "error": err}])[-100:]

    def _send_loop(self) -> None:
        while True:
            self._process(self.q.get())

    def _clean_loop(self) -> None:
        while True:
            try:
                self.sweep()
            except Exception as e:
                log.warning("Telegram: чистка сообщений: %s", e)
            time.sleep(5)

    def _sync_menu(self) -> None:
        """Кнопка меню чата ведёт в мини-приложение; переустанавливается, когда меняется адрес туннеля."""
        url = self.app_url()
        if url == self._menu_url:
            return
        btn = {"type": "web_app", "text": "Открыть LinQ Guard", "web_app": {"url": url}} if url else {"type": "commands"}
        self._call("setChatMenuButton", {"menu_button": btn}, client=self.http_poll)
        self._menu_url = url

    def _poll_loop(self) -> None:
        offset, last_menu = 0, 0.0
        while True:
            try:
                if self.username is None:
                    self.username = self._call("getMe", client=self.http_poll).get("username")
                    self._call("deleteWebhook", client=self.http_poll)
                    self._call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in COMMANDS]},
                               client=self.http_poll)
                    log.info("Telegram-бот @%s запущен", self.username)
                if time.time() - last_menu > 60:
                    self._sync_menu()
                    last_menu = time.time()
                updates = self._call("getUpdates", {"offset": offset, "timeout": 25,
                                                    "allowed_updates": ["message", "callback_query"]}, client=self.http_poll)
            except Exception as e:
                log.warning("Telegram getUpdates: %s", e)
                time.sleep(10 if "Conflict" in str(e) else 3)
                continue
            for upd in updates:
                offset = upd["update_id"] + 1
                try:
                    if "message" in upd:
                        self._on_message(upd["message"])
                    elif "callback_query" in upd:
                        self._on_callback(upd["callback_query"])
                except Exception as e:
                    log.warning("Telegram: ошибка обработки %s: %s", upd.get("update_id"), e)

    # ---------- отправка: используется ShiftService ----------
    def send_text(self, chat_id: int, text: str, buttons: list[list[dict]] | None = None, kind: str = "report",
                  ttl: float | None = None, event_id: str | None = None) -> int:
        rem = {"kind": kind, "event_id": event_id, "expires": self.svc.clock() + ttl if ttl else None}
        return self._put("sendMessage", {"chat_id": chat_id, "text": text[:4096],
                                         "reply_markup": {"inline_keyboard": buttons} if buttons else None}, remember=rem)

    def send_document(self, chat_id: int, filename: str, data: bytes, caption: str = "") -> int:
        return self._put("sendDocument", {"chat_id": chat_id, "caption": caption[:1024]}, {"document": (filename, data)},
                         remember={"kind": "report"})

    def _aux(self, chat_id: int, text: str, buttons: list[list[dict]] | None = None, ttl: float = AUX_TTL,
             event_id: str | None = None) -> int:
        """Служебное сообщение: само удалится через ttl секунд (или раньше, когда шаг завершится)."""
        return self.send_text(chat_id, text, buttons, kind="aux", ttl=ttl, event_id=event_id)

    def _drop_user(self, chat_id: int, message_id: int | None) -> None:
        """Команду или ответ пользователя убираем, когда бот ответил (в личном чате боту это можно)."""
        if message_id:
            self._put("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    def delete_rows(self, rows: list[dict]) -> None:
        """Удалить сообщения бота из чатов; запись в bot_messages убирается сразу, ошибки Telegram не важны."""
        now = self.svc.clock()
        for r in rows:
            self.svc.msg_drop(r["chat_id"], r["message_id"])
            if now - (r["created"] or now) < DELETE_MAX_AGE:
                self._put("deleteMessage", {"chat_id": r["chat_id"], "message_id": r["message_id"]})

    def sweep(self, now: float | None = None) -> int:
        """Служебные сообщения с истёкшим сроком — долой (переживает перезапуск: сроки в shift.db)."""
        rows = self.svc.msg_rows(expired=self.svc.clock() if now is None else now)
        self.delete_rows(rows)
        return len(rows)

    def _send_card(self, chat_id: int, a: dict, text: str, buttons: list[list[dict]], kind: str) -> int:
        """Кадр нарушения с подписью и кнопками; если кадра нет на диске — просто текст."""
        markup = {"inline_keyboard": [b for b in buttons if b]}
        path = self.svc.media / Path(a.get("snapshot") or "none").name
        if a.get("snapshot") and path.exists():
            return self._put("sendPhoto", {"chat_id": chat_id, "caption": text[:1024], "reply_markup": markup},
                             {"photo": (path.name, path.read_bytes)},
                             remember={"kind": kind, "event_id": a["event_id"], "photo": True})
        return self._put("sendMessage", {"chat_id": chat_id, "text": text, "reply_markup": markup},
                         remember={"kind": kind, "event_id": a["event_id"]})

    def _edit(self, r: dict, text: str, buttons: list[list[dict]]) -> int:
        markup = {"inline_keyboard": [b for b in buttons if b]}
        if r["photo"]:
            return self._put("editMessageCaption", {"chat_id": r["chat_id"], "message_id": r["message_id"],
                                                    "caption": text[:1024], "reply_markup": markup})
        return self._put("editMessageText", {"chat_id": r["chat_id"], "message_id": r["message_id"], "text": text,
                                             "reply_markup": markup})

    def alarm_buttons(self, a: dict) -> list[list[dict]]:
        """Открыта — «Принял» и «Устранено»; принята — «Устранено»; устранена — кнопок нет."""
        eid, row = a["event_id"], []
        if a["status"] == "resolved":
            return []
        if a["status"] == "open":
            row.append({"text": "✅ Принял", "callback_data": f"ack:{eid}"})
        row.append({"text": "🛠 Устранено", "callback_data": f"res:{eid}"})
        return [row, self.app_button(frag=f"#alarm={eid}")]

    @staticmethod
    def alarm_text(a: dict) -> str:
        """Подпись карточки тревоги по её состоянию: карточка правится на месте, новых сообщений нет."""
        if a["status"] == "resolved":
            took = (a["resolved_at"] - a["created"]) / 60
            who = a["resolved_by"] + (f", {a['note']}" if a.get("note") else "")
            return (f"✅ Устранено: {a['title']} · {a['camera_label']} · {who} · "
                    f"за {f'{took:.0f}' if took >= 1 else '<1'} мин")
        head = (f"{LEVELS.get(a['severity'], '🚨')}: {a['title']}\n{a['camera_label']} · {a['zone_title']} · "
                f"{time.strftime('%H:%M:%S', time.localtime(a['created']))}")
        if a.get("source") == "operator":
            head += f"\n📣 Передал {a['source_by'] or 'оператор'}" + (f": {a['comment']}" if a.get("comment") else "")
        if a["status"] == "ack":
            return f"{head}\n👀 Принял {a['ack_by']} · устранить до {hm(a['deadline'])}"
        return f"{head}\nУстранить до {hm(a['deadline'])} — иначе уйдёт руководителю"

    def send_alarm(self, chats: list[int], a: dict, readonly: bool = False) -> int:
        """Карточка тревоги; readonly — операторам, для сведения, без кнопок."""
        buttons = [] if readonly else self.alarm_buttons(a)
        return sum(self._send_card(c, a, self.alarm_text(a), buttons, "alarm") for c in chats)

    def send_escalation(self, chats: list[int], a: dict, responsible: str, minutes: int) -> int:
        text = (f"⚠️ Не устранено за {minutes} мин: {a['title']} — {a['camera_label']}, с {hm(a['created'])}. "
                f"Ответственный: {responsible}")
        buttons = [[{"text": "Запросить отчёт", "callback_data": f"ask:{a['event_id']}"}],
                   self.app_button(frag=f"#alarm={a['event_id']}")]
        return sum(self._send_card(c, a, text, buttons, "escalation") for c in chats)

    def send_ask(self, chats: list[int], a: dict, who: str) -> int:
        """Запрос отчёта от руководителя — служебное сообщение, уходит из чата, когда тревогу устранят."""
        text = (f"📋 Руководитель {who} запрашивает отчёт по тревоге: {a['title']} — {a['camera_label']}, "
                f"с {hm(a['created'])}. Что сделано?")
        buttons = [[{"text": "🛠 Устранено", "callback_data": f"res:{a['event_id']}"}],
                   self.app_button(frag=f"#alarm={a['event_id']}")]
        return sum(self.send_text(c, text, [b for b in buttons if b], kind="aux", event_id=a["event_id"]) for c in chats)

    def alarm_updated(self, a: dict, kind: str) -> int:
        """Тревогу приняли или устранили (из бота или приложения): правим карточки во всех чатах, новых не шлём.
        Устранённая тревога: короткая итоговая подпись, а через LINQ_TG_RESOLVED_TTL с карточки удаляются совсем —
        история остаётся в приложении, CSV тревог и отчёте смены."""
        n, roles = 0, self.svc.chat_roles()
        for r in self.svc.msg_rows(kinds=("alarm", "escalation"), event_ids=[a["event_id"]]):
            if kind == "resolved":
                self.svc.msg_expire(r["chat_id"], r["message_id"], self.svc.clock() + self.resolved_ttl)
            if r["kind"] == "alarm":                     # у оператора карточка без кнопок
                buttons = self.alarm_buttons(a) if roles.get(r["chat_id"]) in BOSSES else []
                n += self._edit(r, self.alarm_text(a), buttons)
            elif kind == "resolved":                     # у руководителя — та же короткая итоговая подпись
                n += self._edit(r, self.alarm_text(a), [])
        return n

    # ---------- входящие ----------
    def _user(self, frm: dict, chat_id: int) -> dict | None:
        return self.svc.resolve_user(frm["id"], user_name(frm), chat_id=chat_id)

    def _role_buttons(self) -> list[list[dict]]:
        return [[{"text": ROLE_TITLES[r], "callback_data": f"role:{r}"}] for r in ROLES]

    def _menu(self, chat: int, u: dict, choose_role: bool = False, edit_mid: int | None = None) -> None:
        """Одно меню на чат: старое удаляется (или правится на месте, если нажали кнопку в нём)."""
        text = f"Здравствуйте, {u['name']}! Это LinQ Guard — контроль нарушений охраны труда по камерам.\n"
        if u["role"]:
            text += f"Ваша роль: {ROLE_TITLES[u['role']]}."
            text += {"worker": " В конце смены пришлю отчёт о нарушениях.",
                     "operator": " Критичные тревоги приходят сюда для сведения; любое нарушение можно передать "
                                 "начальнику смены из приложения."}.get(u["role"], " Критичные тревоги будут приходить "
                                                                                   "сюда сразу.")
        choose = u["demo"] and self.svc.demo and (choose_role or not u["role"])
        if choose:
            text += "\nВыберите роль (демо-режим, сменить можно в любой момент: /role):"
        buttons = [b for b in (self._role_buttons() if choose else []) + [self.app_button("Открыть приложение")] if b]
        menus = self.svc.msg_rows(chat_id=chat, kinds=("menu",))
        if edit_mid and any(r["message_id"] == edit_mid for r in menus):
            self._put("editMessageText", {"chat_id": chat, "message_id": edit_mid, "text": text,
                                          "reply_markup": {"inline_keyboard": buttons}})
            self.delete_rows([r for r in menus if r["message_id"] != edit_mid])
            return
        self.delete_rows(menus)
        self.send_text(chat, text, buttons, kind="menu")

    def clear_chat(self, chat: int) -> int:
        """/clear: убрать всё, кроме сообщений по открытым тревогам и последнего меню."""
        rows = self.svc.msg_rows(chat_id=chat)
        menus = [r["message_id"] for r in rows if r["kind"] == "menu"]
        keep_menu = max(menus) if menus else None
        open_ids = self.svc.open_alarm_ids()
        drop = [r for r in rows if r["message_id"] != keep_menu and r["event_id"] not in open_ids]
        self.delete_rows(drop)
        return len(drop)

    def _on_message(self, msg: dict) -> None:
        chat, frm, mid = msg["chat"], msg.get("from"), msg.get("message_id")
        if chat.get("type") != "private" or not frm:
            return
        chat = chat["id"]
        u = self._user(frm, chat)
        if u is None:
            self._aux(chat, f"Доступ не настроен. Передайте администратору ваш Telegram ID {frm['id']} — "
                            "он добавит его в configs/roles.yaml.", ttl=300)
            return
        text = (msg.get("text") or "").strip()
        if not text.startswith("/") and chat in self.pending_note and text:
            a = self.svc.resolve(self.pending_note.pop(chat), u["name"], text[:500])
            self._drop_user(chat, mid)
            if a is None:
                self._aux(chat, "Тревога не найдена.")
            return
        cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        if cmd == "/start":
            self._menu(chat, u)
        elif cmd == "/role":
            if u["demo"] and self.svc.demo:
                self._menu(chat, u, choose_role=True)
            else:
                self._aux(chat, f"Роль назначена администратором: {ROLE_TITLES.get(u['role'], 'не задана')}.")
        elif cmd == "/shift":
            s = self.svc.summary()
            text = self.svc.summary_text(s, sla=u["role"] not in (None, "worker"))
            if u["person_id"]:
                own = sum((r["person"] or {}).get("id") == u["person_id"] for r in s["rows"])
                text += f"\nВаших нарушений: {own}"
            self._aux(chat, text, [b for b in [self.app_button("Открыть приложение")] if b], ttl=300)
        elif cmd == "/report":
            if not u["role"]:
                self._menu(chat, u)
            else:
                for item in self.svc.report_for(u):
                    self.svc.send(chat, item)
        elif cmd == "/clear":
            n = self.clear_chat(chat)
            self._aux(chat, f"🧹 Убрал сообщений: {n}. Открытые тревоги и меню оставил.", ttl=10)
        else:
            self._aux(chat, "Команды: /start — меню, /shift — сводка смены, /report — отчёт CSV, /clear — убрать "
                            "старые сообщения, /role — сменить роль (демо). Тревоги приходят сюда автоматически.")
        if cmd:
            self._drop_user(chat, mid)

    def _answer(self, cq: dict, text: str = "") -> None:
        self._put("answerCallbackQuery", {"callback_query_id": cq["id"], "text": text or None})

    def _on_callback(self, cq: dict) -> None:
        msg = cq.get("message") or {}
        chat = (msg.get("chat") or {}).get("id") or cq["from"]["id"]
        u = self._user(cq["from"], chat)
        kind, _, arg = (cq.get("data") or "").partition(":")
        if u is None:
            return self._answer(cq, "Нет доступа")
        if kind == "role":
            try:
                u = self.svc.set_role(u["tg_id"], arg)
            except (PermissionError, ValueError) as e:
                return self._answer(cq, str(e))
            self._answer(cq, f"Роль: {ROLE_TITLES[arg]}")
            return self._menu(chat, u, edit_mid=msg.get("message_id"))
        if u["role"] not in BOSSES:
            return self._answer(cq, "Только для начальника смены и руководителя")
        if kind == "ack":
            a = self.svc.ack(arg, u["name"])
            self._answer(cq, "Принято" if a else "Тревога не найдена")
        elif kind == "res":
            a = self.svc.alarm(arg)
            if a is None or a["status"] == "resolved":
                return self._answer(cq, "Уже устранено" if a else "Тревога не найдена")
            self.pending_note[chat] = arg
            self._answer(cq)
            self._aux(chat, f"Коротко: что сделано по «{a['title']}»? Нажмите вариант или напишите ответом.",
                      [[{"text": t, "callback_data": f"note:{arg}:{k}"}] for k, t in enumerate(NOTES)],
                      ttl=300, event_id=arg)
        elif kind == "note":
            eid, _, k = arg.rpartition(":")
            self.pending_note.pop(chat, None)
            a = self.svc.resolve(eid, u["name"], NOTES[int(k)] if k.isdigit() and int(k) < len(NOTES) else "")
            self._answer(cq, "Отмечено как устранено" if a else "Тревога не найдена")
        elif kind == "ask":
            if u["role"] != "director":
                return self._answer(cq, "Только для руководителя")
            a = self.svc.ask_report(arg, u["name"])
            self._answer(cq, "Запрос отправлен начальникам смены" if a else "Тревога не найдена")
        else:
            self._answer(cq)
