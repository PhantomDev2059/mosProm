"""Telegram-бот смены поверх Bot API (httpx, long polling): тревоги начальникам, эскалация руководителю, отчёты.

Токен — LINQ_TG_BOT_TOKEN или файл <LINQ_SECRETS>/tg_bot_token (по умолчанию /workspace/linq/secrets).
Публичный HTTPS-адрес мини-приложения — LINQ_PUBLIC_URL или файл <LINQ_SECRETS>/public_url; перечитывается
при каждом использовании: туннель может перезапуститься с новым адресом. Без токена бот выключен, остальное работает.
Все отправки идут через очередь в отдельном потоке — потоки камер никогда не ждут сеть и отрисовку карточек.

Оформление — parse_mode=HTML, одна система: первая строка — значок и жирный заголовок, дальше короткие строки
со значками (📍 камера, 🕐 время, ⏳ срок, 👤 кто, 📝 отчёт). Картинки-карточки рисует api/cards.py, если он есть.
"""
import html
import json
import logging
import os
import queue
import re
import threading
import time
from pathlib import Path

from .shift import BOSSES, ROLE_TITLES, VIEWERS, ShiftService, esc, fmt_dur, hm, render_card, user_name

log = logging.getLogger("linqguard.telegram")

NOTES = ("Помощь оказана", "Очаг потушен", "Опасность устранена", "Ложная тревога")
LEVELS = {"critical": ("🚨", "КРИТИЧНО"), "high": ("⚠️", "ОПАСНО"), "medium": ("🔸", "ВНИМАНИЕ")}
ROLE_ICONS = {"worker": "👷", "operator": "🖥", "supervisor": "🦺", "director": "💼"}
HTML_METHODS = {"sendMessage", "sendPhoto", "sendDocument", "editMessageText", "editMessageCaption"}
AUX_TTL = 60              # служебные сообщения (подтверждения, ошибки, подсказки) живут столько секунд
DELETE_MAX_AGE = 47.5 * 3600   # Telegram даёт боту удалять свои сообщения не старше 48 ч

CMD = {"start": "Главное меню и приложение", "myshift": "Мои нарушения за смену", "alarms": "Открытые тревоги",
       "shift": "Сводка текущей смены", "report": "Отчёт по смене с файлами", "clear": "Убрать старые сообщения",
       "role": "Сменить роль (демо)", "help": "Что умеет бот"}
ROLE_CMDS = {None: ("start", "help"),
             "worker": ("start", "myshift", "report", "help", "clear"),
             "operator": ("start", "shift", "report", "clear", "help"),
             "supervisor": ("start", "alarms", "shift", "report", "clear", "help"),
             "director": ("start", "alarms", "shift", "report", "clear", "help")}

BOT_NAME = "LinQ Guard · контроль ТБ"
BOT_SHORT = "Нарушения охраны труда с камер: тревога начальнику смены сразу, руководителю — если не устранили в срок."
BOT_ABOUT = ("LinQ Guard следит за охраной труда по камерам: падения, огонь и дым, каски и жилеты, опасные зоны, "
             "транспорт рядом с людьми.\n\n"
             "🦺 Начальник смены получает критичные тревоги сразу и отмечает «Принял» и «Устранено».\n"
             "💼 Руководитель — тревоги, не устранённые в срок, и отчёты смен.\n"
             "🖥 Оператор видит всю ленту и передаёт нарушения начальнику.\n"
             "👷 Рабочий — свои нарушения в конце смены.\n\n"
             "Приватность: лица не связываются с именами, профиль привязывает сам сотрудник.")


def fit(text: str, n: int) -> str:
    """Укоротить HTML по целым строкам (каждая строка закрывает свои теги), чтобы не порвать разметку."""
    if len(text) <= n:
        return text
    lines = text.split("\n")
    while lines and len("\n".join(lines)) > n - 2:
        lines.pop()
    if lines:
        return "\n".join(lines + ["…"])
    plain = html.unescape(re.sub(r"<[^>]+>", "", text))       # одна огромная строка — без разметки
    return esc(plain[: n - 10]) + "…"


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
        self._cmds: dict[int, tuple] = {}               # chat_id → (роль, демо) — какое меню команд уже стоит
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

    def app_button(self, text: str = "📱 Открыть в приложении", frag: str = "") -> list[dict]:
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
            if r.status_code == 429 and retry and attempt < 2 and float(retry) <= 60:
                time.sleep(float(retry))
                continue
            raise RuntimeError(f"{method}: {body.get('description', r.status_code)}")

    def _put(self, method: str, params: dict, files: dict | None = None, remember: dict | None = None) -> int:
        """В очередь отправки; remember — что записать в bot_messages после ответа Telegram (вид, тревога, срок)."""
        if not self.enabled or ("chat_id" in params and not params["chat_id"]):
            return 0
        if method in HTML_METHODS:
            params.setdefault("parse_mode", "HTML")
        try:
            self.q.put_nowait((method, params, files, remember))
            return 1
        except queue.Full:
            log.warning("очередь Telegram переполнена, %s в %s пропущено", method, params.get("chat_id"))
            return 0

    def _process(self, item: tuple) -> None:
        method, params, files, remember = item
        try:
            if files:                                    # файлы читаются и карточки рисуются здесь, не в потоке камеры
                files = {k: (n, v() if callable(v) else v) for k, (n, v) in files.items()}
            if "_fallback" in params:                    # картинка не нарисовалась — запасной вызов без неё
                fallback = params.pop("_fallback")
                if any(v is None for _, v in (files or {}).values()):
                    method, params, files = fallback[0], fallback[1], None
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
        btn = {"type": "web_app", "text": "LinQ Guard", "web_app": {"url": url}} if url else {"type": "commands"}
        self._call("setChatMenuButton", {"menu_button": btn}, client=self.http_poll)
        self._menu_url = url

    def sync_profile(self, call=None) -> list[str]:
        """Имя, описание и короткое описание бота — только если отличаются (setMyName сильно ограничен по частоте)."""
        call = call or (lambda m, p=None: self._call(m, p, client=self.http_poll))
        changed = []
        for get, setter, field, value in (("getMyName", "setMyName", "name", BOT_NAME),
                                          ("getMyDescription", "setMyDescription", "description", BOT_ABOUT),
                                          ("getMyShortDescription", "setMyShortDescription", "short_description",
                                           BOT_SHORT)):
            try:
                if (call(get) or {}).get(field) != value:
                    call(setter, {field: value})
                    changed.append(field)
            except Exception as e:
                log.warning("Telegram %s: %s", setter, e)
        try:
            call("setMyCommands", {"commands": self.commands_for(None, False)})
        except Exception as e:
            log.warning("Telegram setMyCommands: %s", e)
        return changed

    def _poll_loop(self) -> None:
        offset, last_menu = 0, 0.0
        while True:
            try:
                if self.username is None:
                    self.username = self._call("getMe", client=self.http_poll).get("username")
                    self._call("deleteWebhook", client=self.http_poll)
                    self.sync_profile()
                    for u in self.svc.chat_users():          # меню команд по ролям — тем, кого бот уже знает
                        self.ensure_commands(u["chat_id"], u)
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

    # ---------- меню команд по ролям ----------
    def commands_for(self, role: str | None, demo: bool) -> list[dict]:
        names = list(ROLE_CMDS.get(role, ROLE_CMDS[None]))
        if demo:                                         # в демо роль меняют сами — команда перед /help
            names.insert(names.index("help"), "role")
        return [{"command": c, "description": CMD[c]} for c in names]

    def ensure_commands(self, chat: int, u: dict) -> None:
        """Меню «/» в чате — под роль пользователя; ставится один раз, переставляется при смене роли."""
        demo = bool(u["demo"] and self.svc.demo)
        key = (u["role"], demo)
        if not chat or self._cmds.get(chat) == key:
            return
        self._cmds[chat] = key
        self._put("setMyCommands", {"commands": self.commands_for(u["role"], demo),
                                    "scope": {"type": "chat", "chat_id": chat}})

    # ---------- отправка: используется ShiftService ----------
    def send_text(self, chat_id: int, text: str, buttons: list[list[dict]] | None = None, kind: str = "report",
                  ttl: float | None = None, event_id: str | None = None) -> int:
        rem = {"kind": kind, "event_id": event_id, "expires": self.svc.clock() + ttl if ttl else None}
        return self._put("sendMessage", {"chat_id": chat_id, "text": fit(text, 4096), "link_preview_options":
                                         {"is_disabled": True},
                                         "reply_markup": {"inline_keyboard": buttons} if buttons else None}, remember=rem)

    def send_photo(self, chat_id: int, filename: str, data: bytes, caption: str = "", kind: str = "report",
                   ttl: float | None = None, buttons: list[list[dict]] | None = None) -> int:
        rem = {"kind": kind, "expires": self.svc.clock() + ttl if ttl else None, "photo": True}
        return self._put("sendPhoto", {"chat_id": chat_id, "caption": fit(caption, 1024),
                                       "reply_markup": {"inline_keyboard": buttons} if buttons else None},
                         {"photo": (filename, data)}, remember=rem)

    def send_document(self, chat_id: int, filename: str, data: bytes, caption: str = "", kind: str = "report",
                      ttl: float | None = None) -> int:
        return self._put("sendDocument", {"chat_id": chat_id, "caption": fit(caption, 1024)},
                         {"document": (filename, data)},
                         remember={"kind": kind, "expires": self.svc.clock() + ttl if ttl else None})

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

    # ---------- карточки тревог ----------
    def _snapshot(self, a: dict) -> Path | None:
        path = self.svc.media / Path(a.get("snapshot") or "none").name
        return path if a.get("snapshot") and path.exists() else None

    def _photo_source(self, a: dict, path: Path):
        """Картинка для sendPhoto: карточка из api/cards.py (рисуется один раз на рассылку, в потоке отправки)
        или сырой кадр, если модуля нет."""
        cache: dict = {}

        def get() -> bytes:
            if "img" not in cache:
                cache["img"] = render_card("alarm_card", path, dict(a)) or path.read_bytes()
            return cache["img"]
        return get

    def _send_card(self, chats: list[int], a: dict, text: str, buttons: list[list[dict]], kind: str) -> int:
        """Кадр нарушения с подписью и кнопками каждому чату; если кадра нет на диске — просто текст."""
        markup = {"inline_keyboard": [b for b in buttons if b]}
        path, n = self._snapshot(a), 0
        photo = self._photo_source(a, path) if path else None
        for chat in chats:
            if photo:
                n += self._put("sendPhoto", {"chat_id": chat, "caption": fit(text, 1024), "reply_markup": markup},
                               {"photo": (f"{a['event_id']}.jpg", photo)},
                               remember={"kind": kind, "event_id": a["event_id"], "photo": True})
            else:
                n += self._put("sendMessage", {"chat_id": chat, "text": text, "reply_markup": markup},
                               remember={"kind": kind, "event_id": a["event_id"]})
        return n

    def _edit(self, r: dict, text: str, buttons: list[list[dict]], image=None) -> int:
        """Правка карточки; image — функция, рисующая новую картинку в потоке отправки (None от неё — правим
        только подпись)."""
        markup = {"inline_keyboard": [b for b in buttons if b]}
        ids = {"chat_id": r["chat_id"], "message_id": r["message_id"], "reply_markup": markup}
        caption = {**ids, "caption": fit(text, 1024), "parse_mode": "HTML"}
        if r["photo"] and image:                         # перерисованная карточка (например, «устранено»)
            return self._put("editMessageMedia", {**ids, "media": {"type": "photo", "media": "attach://card",
                                                                   "caption": fit(text, 1024), "parse_mode": "HTML"},
                                                  "_fallback": ("editMessageCaption", caption)},
                             {"card": ("card.jpg", image)})
        if r["photo"]:
            return self._put("editMessageCaption", caption)
        return self._put("editMessageText", {**ids, "text": text})

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
    def place(a: dict) -> str:
        zone = a.get("zone_title")
        return esc(a["camera_label"]) + (f" · {esc(zone)}" if zone and zone != "Весь кадр" else "")

    @classmethod
    def alarm_text(cls, a: dict) -> str:
        """Подпись карточки тревоги по её состоянию: карточка правится на месте, новых сообщений нет."""
        if a["status"] == "resolved":
            lines = [f"✅ <b>Устранено · {esc(a['title'])}</b>", f"📍 {cls.place(a)}",
                     f"👤 {esc(a['resolved_by'])} · за <code>{fmt_dur(a['resolved_at'] - a['created'])}</code>"]
            if a.get("note"):
                lines.append(f"📝 {esc(a['note'])}")
            return "\n".join(lines)
        icon, level = LEVELS.get(a["severity"], ("🚨", "ТРЕВОГА"))
        lines = [f"{icon} <b>{level} · {esc(a['title'])}</b>", f"📍 {cls.place(a)}",
                 f"🕐 <code>{time.strftime('%H:%M:%S', time.localtime(a['created']))}</code>"]
        if a.get("source") == "operator":
            lines.append(f"📣 Передал {esc(a['source_by'] or 'оператор')}"
                         + (f" · «{esc(a['comment'])}»" if a.get("comment") else ""))
        if a["status"] == "ack":
            lines.append(f"👀 Принял <b>{esc(a['ack_by'])}</b> · <code>{hm(a['ack_at'])}</code>")
            lines.append(f"⏳ Устранить до <code>{hm(a['deadline'])}</code>")
        else:
            lines.append(f"⏳ Устранить до <code>{hm(a['deadline'])}</code> — иначе уйдёт руководителю")
        return "\n".join(lines)

    def send_alarm(self, chats: list[int], a: dict, readonly: bool = False) -> int:
        """Карточка тревоги; readonly — операторам, для сведения, без кнопок."""
        return self._send_card(chats, a, self.alarm_text(a), [] if readonly else self.alarm_buttons(a), "alarm")

    @staticmethod
    def escalation_text(a: dict, responsible: str, minutes: int) -> str:
        return "\n".join([f"❗ <b>Не устранено за {minutes} мин · {esc(a['title'])}</b>",
                          f"📍 {TelegramBot.place(a)}",
                          f"🕐 Тревога с <code>{hm(a['created'])}</code>",
                          f"👤 Ответственный: {esc(responsible)}"])

    def send_escalation(self, chats: list[int], a: dict, responsible: str, minutes: int) -> int:
        buttons = [[{"text": "📋 Запросить отчёт", "callback_data": f"ask:{a['event_id']}"}],
                   self.app_button(frag=f"#alarm={a['event_id']}")]
        return self._send_card(chats, a, self.escalation_text(a, responsible, minutes), buttons, "escalation")

    def send_ask(self, chats: list[int], a: dict, who: str) -> int:
        """Запрос отчёта от руководителя — служебное сообщение, уходит из чата, когда тревогу устранят."""
        text = "\n".join(["📋 <b>Руководитель запрашивает отчёт</b>",
                          f"👤 {esc(who)} ждёт короткий отчёт по тревоге",
                          f"⚠️ {esc(a['title'])} · {self.place(a)}",
                          f"🕐 С <code>{hm(a['created'])}</code> · нажмите «Устранено», когда закончите"])
        buttons = [[{"text": "🛠 Устранено", "callback_data": f"res:{a['event_id']}"}],
                   self.app_button(frag=f"#alarm={a['event_id']}")]
        return sum(self.send_text(c, text, [b for b in buttons if b], kind="aux", event_id=a["event_id"]) for c in chats)

    def alarm_updated(self, a: dict, kind: str) -> int:
        """Тревогу приняли или устранили (из бота или приложения): правим карточки во всех чатах, новых не шлём.
        Устранённая тревога: короткая итоговая подпись (и перерисованная карточка, если её рисует api/cards.py),
        а через LINQ_TG_RESOLVED_TTL с карточки удаляются совсем — история остаётся в приложении, CSV и отчёте."""
        n, roles = 0, self.svc.chat_roles()
        rows = self.svc.msg_rows(kinds=("alarm", "escalation"), event_ids=[a["event_id"]])
        image = None
        if kind == "resolved" and any(r["photo"] for r in rows) and (path := self._snapshot(a)):
            cache: dict = {}

            def draw():                                  # рисуется один раз на все чаты, в потоке отправки
                if "img" not in cache:
                    cache["img"] = render_card("alarm_card", path, dict(a))
                return cache["img"]
            image = draw
        for r in rows:
            if kind == "resolved":
                self.svc.msg_expire(r["chat_id"], r["message_id"], self.svc.clock() + self.resolved_ttl)
            if r["kind"] == "alarm":                     # у оператора карточка без кнопок
                buttons = self.alarm_buttons(a) if roles.get(r["chat_id"]) in BOSSES else []
                n += self._edit(r, self.alarm_text(a), buttons, image)
            elif kind == "resolved":                     # у руководителя — та же короткая итоговая подпись
                n += self._edit(r, self.alarm_text(a), [], image)
        return n

    # ---------- входящие ----------
    def _user(self, frm: dict, chat_id: int) -> dict | None:
        u = self.svc.resolve_user(frm["id"], user_name(frm), chat_id=chat_id)
        if u is not None:
            self.ensure_commands(chat_id, u)
        return u

    def _role_buttons(self, current: str | None = None) -> list[list[dict]]:
        btn = lambda r: {"text": ("• " if r == current else "") + f"{ROLE_ICONS[r]} {ROLE_TITLES[r]}",  # noqa: E731
                         "callback_data": f"role:{r}"}
        return [[btn("worker"), btn("operator")], [btn("supervisor"), btn("director")]]

    def welcome_text(self, u: dict, choose: bool) -> str:
        """Приветствие по роли: что будет приходить и что делать — 3–5 строк."""
        lim = f"<code>{self.svc.escalate_min:g} мин</code>"
        lines = ["🛡 <b>LinQ Guard</b> · контроль охраны труда по камерам", f"Здравствуйте, {esc(u['name'])}."]
        role = u["role"]
        if role:
            lines.append(f"{ROLE_ICONS[role]} Ваша роль: <b>{ROLE_TITLES[role]}</b>")
        lines += {
            "worker": ["📋 В конце смены пришлю ваши нарушения. Привяжите профиль в приложении («Это я»), "
                       "иначе придёт общая сводка.", "👤 Свои нарушения за смену сейчас — /myshift"],
            "operator": ["🔔 Критичные тревоги приходят сюда для сведения.",
                         "📣 Любое нарушение из ленты можно передать начальнику смены в приложении."],
            "supervisor": ["🔔 Критичные тревоги приходят сюда сразу: «Принял», затем «Устранено» с коротким отчётом.",
                           f"⏳ Не устранено за {lim} — тревога уходит руководителю. Открытые — /alarms"],
            "director": [f"🔔 Сюда приходят тревоги, не устранённые за {lim}; можно запросить отчёт у начальника смены.",
                         "📊 Сводка и реакция на тревоги — /shift, отчёт с файлами — /report"],
        }.get(role, [])
        if choose:
            lines.append("Выберите роль — это демо-режим, сменить можно в любой момент: /role")
        return "\n".join(lines)

    def _menu(self, chat: int, u: dict, choose_role: bool = False, edit_mid: int | None = None) -> None:
        """Одно меню на чат: старое удаляется (или правится на месте, если нажали кнопку в нём)."""
        choose = bool(u["demo"] and self.svc.demo and (choose_role or not u["role"]))
        text = self.welcome_text(u, choose)
        buttons = [b for b in [self.app_button("📱 Открыть приложение")] + (self._role_buttons(u["role"]) if choose else [])
                   if b]
        menus = self.svc.msg_rows(chat_id=chat, kinds=("menu",))
        if edit_mid and any(r["message_id"] == edit_mid for r in menus):
            self._put("editMessageText", {"chat_id": chat, "message_id": edit_mid, "text": text,
                                          "reply_markup": {"inline_keyboard": buttons}})
            self.delete_rows([r for r in menus if r["message_id"] != edit_mid])
            return
        self.delete_rows(menus)
        self.send_text(chat, text, buttons, kind="menu")

    def help_text(self, u: dict) -> str:
        cmds = self.commands_for(u["role"], bool(u["demo"] and self.svc.demo))
        lines = ["❓ <b>Что умеет бот</b>"] + [f"/{c['command']} — {c['description']}" for c in cmds]
        lines.append({"worker": "📋 Личный отчёт приходит в конце смены.",
                      "operator": "🔔 Критичные тревоги приходят сами; передать нарушение — в приложении.",
                      "supervisor": "🔔 Тревоги приходят сами; кнопки под карточкой — «Принял» и «Устранено».",
                      "director": "🔔 Сюда приходят просроченные тревоги и отчёты смен."}.get(u["role"],
                                                                                          "Начните с /start."))
        return "\n".join(lines)

    def clear_chat(self, chat: int) -> int:
        """/clear: убрать всё, кроме сообщений по открытым тревогам и последнего меню."""
        rows = self.svc.msg_rows(chat_id=chat)
        menus = [r["message_id"] for r in rows if r["kind"] == "menu"]
        keep_menu = max(menus) if menus else None
        open_ids = self.svc.open_alarm_ids()
        drop = [r for r in rows if r["message_id"] != keep_menu and r["event_id"] not in open_ids]
        self.delete_rows(drop)
        return len(drop)

    def alarms_list(self, chat: int, u: dict) -> None:
        """/alarms: открытые тревоги одним сообщением, под каждой — кнопки (у начальника смены и руководителя)."""
        alarms = self.svc.open_alarms()
        if not alarms:
            self._aux(chat, "✅ <b>Открытых тревог нет</b>\nВсе тревоги смены устранены.")
            return
        lines, buttons = [f"🔔 <b>Открытые тревоги</b> · {len(alarms)}"], []
        for i, a in enumerate(alarms[:8], 1):
            icon = LEVELS.get(a["severity"], ("🚨",))[0]
            state = f"👀 {esc(a['ack_by'])}" if a["status"] == "ack" else "ждёт «Принял»"
            late = " · ❗ просрочена" if self.svc.clock() > a["deadline"] else ""
            lines.append(f"{i}. {icon} <b>{esc(a['title'])}</b> · {self.place(a)}\n"
                         f"    🕐 <code>{hm(a['created'])}</code> · ⏳ до <code>{hm(a['deadline'])}</code> · {state}{late}")
            if u["role"] in BOSSES:
                row = [{"text": f"✅ {i} Принял", "callback_data": f"ack:{a['event_id']}"}] if a["status"] == "open" else []
                buttons.append(row + [{"text": f"🛠 {i} Устранено", "callback_data": f"res:{a['event_id']}"}])
        if len(alarms) > 8:
            lines.append(f"… и ещё {len(alarms) - 8} — в приложении")
        self._aux(chat, "\n".join(lines), [b for b in buttons + [self.app_button()] if b], ttl=300)

    def my_shift(self, chat: int, u: dict) -> None:
        """/myshift: свои нарушения за текущую смену — карточкой (если рисуется) и списком."""
        if not u["person_id"]:
            self._aux(chat, "👤 <b>Профиль не привязан</b>\nОткройте приложение и нажмите «Это я» на своей карточке — "
                            "тогда здесь будут ваши нарушения.", [b for b in [self.app_button("📱 Открыть приложение")] if b],
                      ttl=300)
            return
        s = self.svc.summary()
        for item in self.svc.own_items(u, s, self.svc.file_tag(s["shift"]["id"])):
            if item[0] != "doc":
                self.svc.send(chat, item, kind="aux", ttl=300)

    def shift_brief(self, chat: int, u: dict) -> None:
        """/shift: сводка смены; персоналу — с реакцией на тревоги и картинкой-отчётом."""
        s = self.svc.summary()
        staff = u["role"] in VIEWERS
        text = self.svc.summary_text(s, sla=staff)
        if u["person_id"]:
            own = sum((r["person"] or {}).get("id") == u["person_id"] for r in s["rows"])
            text += f"\n👷 Ваших нарушений: <b>{own}</b> — подробно /myshift"
        card = render_card("shift_card", self.svc.shift_payload(s)) if staff else None
        for item in self.svc.with_card(card, "shift.png", text):
            self.svc.send(chat, item, kind="aux", ttl=300)

    def _on_message(self, msg: dict) -> None:
        chat, frm, mid = msg["chat"], msg.get("from"), msg.get("message_id")
        if chat.get("type") != "private" or not frm:
            return
        chat = chat["id"]
        u = self._user(frm, chat)
        if u is None:
            self._aux(chat, f"⛔ <b>Нет доступа</b>\nПередайте администратору ваш Telegram ID <code>{frm['id']}</code> — "
                            "он добавит вас в список ролей.", ttl=300)
            return
        text = (msg.get("text") or "").strip()
        if not text.startswith("/") and chat in self.pending_note and text:
            a = self.svc.resolve(self.pending_note.pop(chat), u["name"], text[:500])
            self._drop_user(chat, mid)
            if a is None:
                self._aux(chat, "🔎 Тревога не найдена — возможно, её уже закрыли.")
            return
        cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        if cmd == "/start":
            self._menu(chat, u)
        elif cmd == "/role":
            if u["demo"] and self.svc.demo:
                self._menu(chat, u, choose_role=True)
            else:
                self._aux(chat, f"🔒 Роль назначена администратором: <b>{ROLE_TITLES.get(u['role'], 'не задана')}</b>")
        elif not u["role"] and cmd in ("/shift", "/report", "/alarms", "/myshift"):
            self._menu(chat, u)
        elif cmd == "/shift":
            self.shift_brief(chat, u)
        elif cmd == "/myshift":
            self.my_shift(chat, u)
        elif cmd == "/alarms":
            if u["role"] in VIEWERS:
                self.alarms_list(chat, u)
            else:
                self._aux(chat, "🔒 Тревоги видят оператор, начальник смены и руководитель. Ваши нарушения — /myshift")
        elif cmd == "/report":
            for item in self.svc.report_for(u):
                self.svc.send(chat, item)
        elif cmd == "/clear":
            n = self.clear_chat(chat)
            self._aux(chat, f"🧹 Убрано сообщений: <b>{n}</b>. Открытые тревоги и меню на месте.", ttl=10)
        else:
            self._aux(chat, self.help_text(u), [b for b in [self.app_button("📱 Открыть приложение")] if b])
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
            self.ensure_commands(chat, u)
            self._answer(cq, f"Роль: {ROLE_TITLES[arg]}")
            return self._menu(chat, u, edit_mid=msg.get("message_id"))
        if u["role"] not in BOSSES:
            return self._answer(cq, "Только для начальника смены и руководителя")
        if kind == "ack":
            a = self.svc.ack(arg, u["name"])
            self._answer(cq, "Принято. Не забудьте отметить «Устранено»" if a else "Тревога не найдена")
        elif kind == "res":
            a = self.svc.alarm(arg)
            if a is None or a["status"] == "resolved":
                return self._answer(cq, "Уже устранено" if a else "Тревога не найдена")
            self.pending_note[chat] = arg
            self._answer(cq)
            self._aux(chat, "\n".join(["🛠 <b>Что сделано?</b>", f"⚠️ {esc(a['title'])} · {self.place(a)}",
                                       "Выберите вариант или напишите коротко ответным сообщением."]),
                      [[{"text": t, "callback_data": f"note:{arg}:{k}"} for k, t in list(enumerate(NOTES))[i:i + 2]]
                       for i in (0, 2)], ttl=300, event_id=arg)
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
