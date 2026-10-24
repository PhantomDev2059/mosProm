"""Картинки для Telegram-бота: кадр тревоги, отчёт за смену, личные итоги сотрудника.

Стиль — как у Mini App (api/static/tg): тёмно-синий чертёжный планшет с сеткой, белые и голубые
«чернильные» линии с лёгкой дрожью, бумажные карточки; цвет — только у безопасности: тревога (красный),
сигнальный оранжевый, жёлтая разметка, зелёный «устранено». Приёмы (дрожащая линия, штриховка,
планшет с зерном) повторяют art.js, который вырос из procedural-film (MIT).

Шрифты — assets/fonts (OFL, с кириллицей): Fira Sans Extra Condensed — заголовки, Golos Text — текст,
JetBrains Mono — подписи и цифры, Caveat — рукописная пометка.
Рисуем Pillow + numpy с суперсэмплингом ×2: так у линий и штриховки сглаженные края.

Публичные функции никогда не бросают исключений: при любой ошибке — исходный кадр (alarm_card) или None.
"""
from __future__ import annotations

import io
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np

log = logging.getLogger("linqguard.cards")

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "assets" / "fonts"

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Moscow")
except Exception:                                    # нет базы часовых поясов — Москва без перевода часов
    TZ = timezone(timedelta(hours=3), "MSK")

PAL = {
    "navy": (11, 18, 48), "navy_deep": (6, 10, 28), "navy2": (18, 27, 66), "navy3": (24, 35, 77),
    "grid": (58, 74, 134), "chalk": (238, 240, 255), "lav": (200, 193, 239), "pale": (156, 194, 234),
    "cyan": (127, 227, 255), "dim": (110, 121, 174),
    "paper": (239, 227, 201), "paper_hi": (246, 238, 220), "paper_shade": (226, 209, 176),
    "ink": (42, 28, 19), "ink_soft": (91, 67, 49), "ink_faint": (138, 115, 92), "blue_ink": (35, 70, 160),
    "crit": (255, 74, 61), "high": (255, 138, 31), "med": (242, 194, 48), "ok": (61, 190, 139),
    "crit_ink": (184, 38, 26), "ok_ink": (31, 122, 82), "high_ink": (176, 82, 0),
}

# уровень → (подпись, цвет плашки, цвет текста на плашке)
SEVERITY = {
    "critical": ("КРИТИЧНО", PAL["crit"], (255, 255, 255)),
    "high": ("ВЫСОКИЙ", PAL["high"], (26, 14, 4)),
    "medium": ("СРЕДНИЙ", PAL["med"], (26, 20, 0)),
    "low": ("НИЗКИЙ", PAL["pale"], PAL["navy_deep"]),
}

_FONT_FILES = {
    "disp": {500: "FiraSansExtraCondensed-Medium.ttf", 600: "FiraSansExtraCondensed-SemiBold.ttf",
             700: "FiraSansExtraCondensed-Bold.ttf", 800: "FiraSansExtraCondensed-ExtraBold.ttf"},
    "body": "GolosText-VF.ttf",
    "mono": "JetBrainsMono-VF.ttf",
    "hand": "Caveat-VF.ttf",
}


def rgba(c: tuple, a: float = 1.0) -> tuple:
    return (int(c[0]), int(c[1]), int(c[2]), int(round(255 * max(0.0, min(1.0, a)))))


@lru_cache(maxsize=256)
def font(kind: str, size: float, weight: int = 600):
    """Шрифт нужного семейства, кегля и насыщенности; у вариативных шрифтов выставляем ось wght."""
    from PIL import ImageFont
    size = max(1, int(round(size)))
    try:
        f = _FONT_FILES[kind]
        if isinstance(f, dict):
            return ImageFont.truetype(str(FONTS / f[min(f, key=lambda w: abs(w - weight))]), size)
        ft = ImageFont.truetype(str(FONTS / f), size)
        try:
            ax = ft.get_variation_axes()[0]
            ft.set_variation_by_axes([min(max(weight, ax["minimum"]), ax["maximum"])])
        except Exception:                            # FreeType без вариаций — останется начертание по умолчанию
            pass
        return ft
    except Exception:
        log.warning("шрифт %s не найден, беру встроенный", kind)
        try:
            return ImageFont.load_default(size)
        except Exception:
            return ImageFont.load_default()


# ---------------------------------------------------------------- шум и дрожь линии (как noise1 в art.js)
def _hash(i, s: float):
    x = np.sin(np.asarray(i, float) * 12.9898 + s * 78.233) * 43758.5453
    return x - np.floor(x)


def _noise1(x, s: float):
    i = np.floor(x)
    f = x - i
    g0, g1 = _hash(i, s) * 2 - 1, _hash(i + 1, s) * 2 - 1
    u = f * f * f * (f * (f * 6 - 15) + 10)
    return np.clip((g0 * f + (g1 * (f - 1) - g0 * f) * u) * 2, -1, 1)


def wobble(pts, seed: float = 1, amp: float = 1.1, step: float = 4, closed: bool = False, trem: float = 0.22):
    """Ломаная, перерисованная «от руки»: точки через step пикселей сдвинуты по нормали плавным шумом."""
    P = np.asarray(list(pts) + ([pts[0]] if closed else []), float)
    if len(P) < 2:
        return [tuple(p) for p in P]
    parts = []
    for a, b in zip(P[:-1], P[1:]):
        n = max(1, int(math.ceil(math.hypot(*(b - a)) / step)))
        parts.append(a + (b - a) * (np.arange(n) / n)[:, None])
    parts.append(P[-1:])
    Q = np.vstack(parts)
    if len(Q) < 3:
        return [tuple(p) for p in Q]
    s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(Q, axis=0).T))])
    T = np.gradient(Q, axis=0)
    L = np.hypot(T[:, 0], T[:, 1])
    L[L == 0] = 1
    N = np.stack([-T[:, 1] / L, T[:, 0] / L], 1)
    d = _noise1(s / 70 + seed * 7.31, seed) * amp + (_hash(np.arange(len(Q)), seed + 3.3) - 0.5) * 2 * trem
    if closed:                                       # замыкаем без ступеньки
        d = d - (d[-1] - d[0]) * (s / (s[-1] or 1))
    return [tuple(p) for p in Q + N * d[:, None]]


def ellipse_pts(cx, cy, rx, ry, a0=0.0, a1=math.tau, n=48):
    return [(cx + math.cos(a0 + (a1 - a0) * i / n) * rx, cy + math.sin(a0 + (a1 - a0) * i / n) * ry) for i in range(n + 1)]


# ---------------------------------------------------------------- холст
class Sheet:
    """Холст в логических пикселях; рисуем в ss раз крупнее и в конце уменьшаем — края гладкие."""

    def __init__(self, w: int, h: int, bg=PAL["navy"], ss: int = 2):
        from PIL import Image, ImageDraw
        self.Image, self.ImageDraw = Image, ImageDraw
        self.w, self.h, self.ss = w, h, ss
        self.im = Image.new("RGB", (w * ss, h * ss), bg)
        self.d = ImageDraw.Draw(self.im, "RGBA")

    # --- пересчёт координат
    def _p(self, pts):
        return [(x * self.ss, y * self.ss) for x, y in pts]

    def _b(self, box):
        return [round(v * self.ss) for v in box]

    # --- примитивы
    def rect(self, box, fill=None, outline=None, width: float = 1, radius: float = 0):
        if radius:
            self.d.rounded_rectangle(self._b(box), radius * self.ss, fill=fill, outline=outline,
                                     width=max(1, round(width * self.ss)))
        else:
            self.d.rectangle(self._b(box), fill=fill, outline=outline, width=max(1, round(width * self.ss)))

    def poly(self, pts, fill=None, outline=None, width: float = 1):
        self.d.polygon(self._p(pts), fill=fill, outline=outline, width=max(1, round(width * self.ss)))

    def line(self, pts, color, width: float = 1.4, dash: tuple | None = None):
        pts = self._p(pts)
        w = max(1, round(width * self.ss))
        if not dash:
            self.d.line(pts, fill=color, width=w, joint="curve" if len(color) < 4 or color[3] == 255 else None)
            return
        on, off = dash[0] * self.ss, dash[1] * self.ss
        for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
            L = math.hypot(x1 - x0, y1 - y0)
            t = 0.0
            while t < L:
                t1 = min(L, t + on)
                self.d.line([(x0 + (x1 - x0) * t / L, y0 + (y1 - y0) * t / L),
                             (x0 + (x1 - x0) * t1 / L, y0 + (y1 - y0) * t1 / L)], fill=color, width=w)
                t = t1 + off

    def ink(self, pts, color, width: float = 1.6, seed: float = 1, amp: float = 1.1, closed: bool = False,
            fill=None, double: bool = False, dash=None):
        """Чернильная линия: дрожит, заливка печатается чуть мимо контура, второй тонкий проход — по желанию."""
        if fill is not None:
            self.poly([(x + 1.2, y + 0.9) for x, y in wobble(pts, seed, amp, closed=True)], fill=fill)
        self.line(wobble(pts, seed, amp, closed=closed), color, width, dash)
        if double:
            c = color[:3] + (int((color[3] if len(color) > 3 else 255) * 0.4),)
            self.line(wobble(pts, seed + 97, amp * 1.6, closed=closed, trem=0.4), c, max(0.6, width * 0.45))

    def circle(self, cx, cy, r, fill=None, outline=None, width: float = 1):
        self.d.ellipse(self._b((cx - r, cy - r, cx + r, cy + r)), fill=fill, outline=outline,
                       width=max(1, round(width * self.ss)))

    def arc(self, cx, cy, r, a0, a1, color, width: float = 2):
        """Дуга в градусах, 0 — справа, по часовой."""
        self.d.arc(self._b((cx - r, cy - r, cx + r, cy + r)), a0, a1, fill=color, width=max(1, round(width * self.ss)))

    # --- слой: рисуем отдельно и накладываем по маске (штриховка, полосы, свечение)
    def layer(self, box):
        x0, y0, x1, y1 = self._b(box)
        lay = self.Image.new("RGBA", (max(1, x1 - x0), max(1, y1 - y0)), (0, 0, 0, 0))
        return lay, self.ImageDraw.Draw(lay), (x0, y0)

    def put(self, lay, at, mask=None):
        self.im.paste(lay, at, lay if mask is None else mask)

    def hatch(self, box, color, gap: float = 7, width: float = 1.2, angle: float = -45, seed: float = 3, mask_poly=None):
        """Ручная штриховка: параллельные штрихи с разбросом шага и длины, обрезанные по рамке."""
        lay, d, at = self.layer(box)
        W, H = lay.size
        a = math.radians(angle)
        ca, sa = math.cos(a), math.sin(a)
        R = math.hypot(W, H) / 2 + 4
        g = gap * self.ss
        n = int(2 * R / g) + 2
        jit = (_hash(np.arange(n), seed) - 0.5) * g * 0.45
        e0 = R * (0.8 + 0.2 * _hash(np.arange(n), seed + 1))
        e1 = R * (0.8 + 0.2 * _hash(np.arange(n), seed + 2))
        for i in range(n):
            dd = -R + i * g + jit[i]
            px, py = W / 2 - sa * dd, H / 2 + ca * dd
            d.line([(px - ca * e0[i], py - sa * e0[i]), (px + ca * e1[i], py + sa * e1[i])], fill=color,
                   width=max(1, round(width * self.ss)))
        if mask_poly is not None:
            m = self.Image.new("L", lay.size, 0)
            self.ImageDraw.Draw(m).polygon([(x * self.ss - at[0], y * self.ss - at[1]) for x, y in mask_poly], fill=255)
            from PIL import ImageChops
            lay.putalpha(ImageChops.multiply(lay.getchannel("A"), m))
        self.put(lay, at)

    def tape(self, box, c1, c2, period: float = 26, angle_dx: float | None = None):
        """Сигнальная лента: косые полосы c1/c2 (как разметка опасной зоны)."""
        x0, y0, x1, y1 = box
        self.rect(box, fill=c2)
        lay, d, at = self.layer(box)
        W, H = lay.size
        p = period * self.ss
        dx = H if angle_dx is None else angle_dx * self.ss
        x = -dx - p
        while x < W + p:
            d.polygon([(x, H), (x + p / 2, H), (x + p / 2 + dx, 0), (x + dx, 0)], fill=c1)
            x += p
        self.put(lay, at)

    def glow(self, cx, cy, r, color, k: float = 1.0):
        """Мягкое свечение вокруг точки (тревожный маячок, светодиод камеры)."""
        R = r * 4
        lay, d, at = self.layer((cx - R, cy - R, cx + R, cy + R))
        W, H = lay.size
        yy, xx = np.mgrid[0:H, 0:W]
        dist = np.hypot(xx - W / 2, yy - H / 2) / (R * self.ss)
        a = np.clip(0.55 * k * np.exp(-(dist / 0.22) ** 2) + 0.18 * k * np.clip(1 - dist, 0, 1) ** 2, 0, 1)
        arr = np.zeros((H, W, 4), np.uint8)
        arr[..., :3] = color[:3]
        arr[..., 3] = (a * 255).astype(np.uint8)
        g = self.Image.fromarray(arr, "RGBA")
        self.put(g, at)
        self.circle(cx, cy, r * 0.55, fill=(255, 255, 255, int(230 * min(1, k))))

    def shadow(self, box, offset=(0, 10), blur: float = 18, alpha: float = 0.5, radius: float = 0):
        from PIL import ImageFilter
        pad = blur * 2
        x0, y0, x1, y1 = box
        lay, d, at = self.layer((x0 - pad + offset[0], y0 - pad + offset[1], x1 + pad + offset[0], y1 + pad + offset[1]))
        s = self.ss
        d.rounded_rectangle((pad * s, pad * s, (x1 - x0 + pad) * s, (y1 - y0 + pad) * s), radius * s,
                            fill=(0, 0, 0, int(255 * alpha)))
        self.put(lay.filter(ImageFilter.GaussianBlur(blur * s / 2)), at)

    # --- фоны
    def plate(self, box, center=None, grid: float = 20, seed: int = 1, corners: bool = True, light: float = 1.0):
        """Чертёжный планшет: радиальный градиент navy, зерно, сетка с крупным шагом через 5 клеток."""
        x0, y0, x1, y1 = [int(round(v)) for v in box]
        w, h = max(1, x1 - x0), max(1, y1 - y0)
        cx, cy = center if center is not None else (w / 2, h * 0.45)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        t = np.clip(np.hypot(xx - cx, yy - cy) / (math.hypot(w, h) * 0.62), 0, 1)[..., None]
        c0, c1, c2 = (np.array(PAL[k], np.float32) for k in ("navy3", "navy", "navy_deep"))
        c0 = c1 + (c0 - c1) * light
        img = np.where(t < 0.38, c0 + (c1 - c0) * (t / 0.38), c1 + (c2 - c1) * ((t - 0.38) / 0.62))
        rng = np.random.default_rng(seed)
        img += ((rng.random((h, w)) - 0.5) * 7 + (rng.random((h, w)) > 0.985) * 9)[..., None] * np.array([0.9, 0.95, 1.1])
        tile = self.Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB").resize((w * self.ss, h * self.ss),
                                                                                       self.Image.NEAREST)
        self.im.paste(tile, (x0 * self.ss, y0 * self.ss))
        if grid:
            self._grid(box, grid, PAL["grid"], 0.30, 0.55)
        if corners:
            self.corners(box, rgba(PAL["lav"], 0.35))

    def paper(self, box, seed: int = 2, grid: float = 24, edge: float = 0.07):
        """Бумага: тёплый фон, пятнистость, зерно, едва заметная синяя сетка, потемнение к краям."""
        x0, y0, x1, y1 = [int(round(v)) for v in box]
        w, h = max(1, x1 - x0), max(1, y1 - y0)
        rng = np.random.default_rng(seed)
        base = np.array(PAL["paper"], np.float32)
        blot = np.asarray(self.Image.fromarray((rng.random((max(2, h // 60), max(2, w // 60))) * 255).astype(np.uint8))
                          .resize((w, h), self.Image.BICUBIC), np.float32) / 255 - 0.5
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        vig = np.clip(np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2)) - 0.55, 0, 1) ** 2
        k = 1 + blot * 0.05 + (rng.random((h, w)) - 0.5) * 0.05 - vig * edge
        img = base * k[..., None] + (rng.random((h, w)) > 0.992)[..., None] * -28
        tile = self.Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB").resize((w * self.ss, h * self.ss),
                                                                                       self.Image.NEAREST)
        self.im.paste(tile, (x0 * self.ss, y0 * self.ss))
        if grid:
            self._grid(box, grid, PAL["blue_ink"], 0.07, 0.14)

    def _grid(self, box, gs, color, a_minor, a_major):
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        mi, ma = rgba(color, a_minor), rgba(color, a_major)
        x = (w / 2) % gs
        while x <= w:
            major = round((x - w / 2) / gs) % 5 == 0
            self.d.line(self._p([(x0 + x, y0), (x0 + x, y1)]), fill=ma if major else mi, width=2 if major else 1)
            x += gs
        y = (h / 2) % gs
        while y <= h:
            major = round((y - h / 2) / gs) % 5 == 0
            self.d.line(self._p([(x0, y0 + y), (x1, y0 + y)]), fill=ma if major else mi, width=2 if major else 1)
            y += gs

    def corners(self, box, color, m: float = 12, s: float = 16, width: float = 1.3):
        x0, y0, x1, y1 = box
        for x, y, sx, sy in ((x0 + m, y0 + m, 1, 1), (x1 - m, y0 + m, -1, 1), (x0 + m, y1 - m, 1, -1), (x1 - m, y1 - m, -1, -1)):
            self.line([(x, y + sy * s), (x, y), (x + sx * s, y)], color, width)

    def ruler(self, x0, x1, y, color, fill=None, progress: float = 1.0):
        """Линейка как в шапке Mini App: мелкие риски через 1/12, крупные через 1/4, жёлтая заливка прогресса."""
        self.line([(x0, y), (x1, y)], color, 1.2)
        for i in range(13):
            x = x0 + (x1 - x0) * i / 12
            self.line([(x, y), (x, y - (9 if i % 3 == 0 else 5))], color, 1.1)
        if fill is not None and progress > 0:
            xe = x0 + (x1 - x0) * progress
            self.rect((x0, y - 1.5, xe, y + 1.5), fill=fill)
            c = (xe, y)
            self.poly([(c[0], c[1] - 6), (c[0] + 6, c[1]), (c[0], c[1] + 6), (c[0] - 6, c[1])], fill=PAL["navy"],
                      outline=fill, width=1.6)

    # --- текст
    def tw(self, s: str, kind="body", size=24, weight=500, track: float = 0) -> float:
        f = font(kind, size * self.ss, weight)
        return (f.getlength(s) + (track * self.ss * max(0, len(s) - 1) if track else 0)) / self.ss

    def text(self, x, y, s: str, kind="body", size=24, weight=500, color=PAL["chalk"], anchor="ls", track: float = 0):
        """Текст; track — разрядка в логических пикселях. Возвращает ширину."""
        f = font(kind, size * self.ss, weight)
        if not track:
            self.d.text((x * self.ss, y * self.ss), s, fill=color, font=f, anchor=anchor)
            return self.tw(s, kind, size, weight)
        w = self.tw(s, kind, size, weight, track)
        h0 = anchor[0]
        cx = x - (w if h0 == "r" else w / 2 if h0 == "m" else 0)
        for ch in s:
            self.d.text((cx * self.ss, y * self.ss), ch, fill=color, font=f, anchor="l" + anchor[1])
            cx += f.getlength(ch) / self.ss + track
        return w

    def fit(self, s: str, max_w: float, kind="disp", size=90, weight=800, min_size=28, track: float = 0) -> float:
        """Наибольший кегль не больше size, при котором строка помещается в max_w."""
        while size > min_size and self.tw(s, kind, size, weight, track * size / 100) > max_w:
            size -= 2
        return size

    def clip(self, s: str, max_w: float, kind="body", size=24, weight=500) -> str:
        """Обрезает строку многоточием по ширине."""
        if self.tw(s, kind, size, weight) <= max_w:
            return s
        while s and self.tw(s + "…", kind, size, weight) > max_w:
            s = s[:-1]
        return s.rstrip(" ,·-") + "…"

    def done(self):
        return self.im.resize((self.w, self.h), self.Image.LANCZOS)


# ---------------------------------------------------------------- мотивы
def camera_glyph(sh: Sheet, x, y, size, angle_deg: float, color, fill=None, led=PAL["crit"], seed=5, width=None):
    """Камера наблюдения на кронштейне: (x, y) — пластина на стене, angle — куда смотрит объектив (0 — вправо).

    Корпус висит под шарниром и повёрнут на angle; козырёк сверху, объектив спереди.
    Возвращает координаты объектива, чтобы от него рисовать конус обзора."""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    side = 1 if ca >= 0 else -1                      # смотрит влево — зеркалим, чтобы корпус висел под шарниром
    s = size
    jx, jy = x + side * s * 0.34, y                  # шарнир на конце кронштейна

    def R(u, v):                                     # координаты корпуса (u — вдоль взгляда, v — «вниз») → лист
        u, v = u - s * 0.30, (v + s * 0.06) * side
        return (jx + u * ca - v * sa, jy + u * sa + v * ca)

    w = width or max(1.4, s * 0.07)
    amp = s * 0.008
    # кронштейн: пластина на стене и плечо до шарнира
    sh.ink([(x, y - s * 0.16), (x, y + s * 0.16)], color, w * 1.25, seed=seed, amp=amp)
    sh.ink([(x, y), (jx, jy)], color, w, seed=seed + 1, amp=amp)
    # корпус, козырёк и шейка
    body = [R(s * 0.02, s * 0.10), R(s * 0.80, s * 0.10), R(s * 0.80, s * 0.44), R(s * 0.06, s * 0.44), R(-s * 0.02, s * 0.27)]
    hood = [R(-s * 0.06, -s * 0.01), R(s * 0.96, -s * 0.01), R(s * 0.96, s * 0.09), R(-s * 0.06, s * 0.09)]
    sh.ink([R(s * 0.30, -s * 0.06), R(s * 0.30, -s * 0.01)], color, w, seed=seed + 2, amp=0.2)
    sh.ink(body, color, w, seed=seed + 3, amp=amp, closed=True, fill=fill)
    sh.ink(hood, color, w, seed=seed + 4, amp=amp, closed=True, fill=fill)
    sh.circle(jx, jy, max(1.5, s * 0.055), fill=fill or PAL["navy"], outline=color, width=w * 0.8)
    # объектив: стекло на передней грани
    lens = [R(s * 0.80, s * 0.15), R(s * 0.88, s * 0.17), R(s * 0.88, s * 0.37), R(s * 0.80, s * 0.39)]
    sh.ink(lens, color, w, seed=seed + 5, amp=amp * 0.5, closed=True, fill=rgba(PAL["cyan"], 0.55))
    if led is not None:
        gx, gy = R(s * 0.16, s * 0.27)
        sh.glow(gx, gy, max(2.0, s * 0.05), led, 0.9)
    return R(s * 0.90, s * 0.27)


def cone(sh: Sheet, apex, angle_deg: float, spread_deg: float, length: float, color=PAL["cyan"], alpha=0.14,
         seed=11, arcs: int = 3, width: float = 1.4):
    """Конус обзора камеры: полупрозрачная заливка, дрожащие кромки и дуги сканирования."""
    a0, a1 = math.radians(angle_deg - spread_deg / 2), math.radians(angle_deg + spread_deg / 2)
    ax, ay = apex
    edge = [(ax + math.cos(a0 + (a1 - a0) * i / 24) * length, ay + math.sin(a0 + (a1 - a0) * i / 24) * length)
            for i in range(25)]
    sh.poly([apex] + edge, fill=rgba(color, alpha))
    sh.ink([apex, edge[0]], rgba(color, 0.85), width, seed=seed, amp=0.8)
    sh.ink([apex, edge[-1]], rgba(color, 0.85), width, seed=seed + 1, amp=0.8)
    for i in range(1, arcs + 1):
        r = length * i / (arcs + 0.4)
        pts = [(ax + math.cos(a0 + (a1 - a0) * j / 16) * r, ay + math.sin(a0 + (a1 - a0) * j / 16) * r) for j in range(17)]
        sh.ink(pts, rgba(color, 0.35 + 0.12 * i), width * 0.75, seed=seed + 5 + i, amp=0.6, dash=(6, 5) if i < arcs else None)


def section_head(sh: Sheet, x, y, x_end, num: str, title: str, color, sub_color, meta: str = "", size=34):
    """Заголовок секции как в Mini App: номер, название, размерная линия до правого края, мета."""
    sh.text(x, y, num, "mono", 18, 600, sub_color, track=1.5)
    tx = x + sh.tw(num, "mono", 18, 600, 1.5) + 14
    tw = sh.text(tx, y, title.upper(), "disp", size, 700, color, track=size * 0.04)
    mw = sh.tw(meta, "mono", 20, 500) if meta else 0
    if meta:
        sh.text(x_end, y, meta, "mono", 20, 500, sub_color, anchor="rs")
    lx0, lx1 = tx + tw + 16, x_end - mw - (16 if meta else 0)
    if lx1 - lx0 > 20:
        ly = y - size * 0.32
        sh.line([(lx0, ly), (lx1, ly)], sub_color, 1.1)
        sh.line([(lx0, ly - 5), (lx0, ly + 5)], sub_color, 1.1)
        sh.line([(lx1, ly - 5), (lx1, ly + 5)], sub_color, 1.1)


def _dt(ts) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(ts), TZ)
    except Exception:
        return None


def _hms(ts, fmt="%H:%M:%S") -> str:
    d = _dt(ts)
    return d.strftime(fmt) if d else "—"


def _dur(s) -> str:
    """Секунды → «48 с», «6 мин», «1 ч 05 мин»; None → «—»."""
    if s is None:
        return "—"
    s = int(round(float(s)))
    if s < 60:
        return f"{s} с"
    if s < 3600:
        m, r = divmod(s, 60)
        return f"{m} мин" if s >= 600 or not r else f"{m} мин {r:02d} с"
    return f"{s // 3600} ч {s % 3600 // 60:02d} мин"


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _jpeg(im, limit: int = 250_000) -> bytes:
    """JPEG не больше limit байт: снижаем качество ступенями."""
    out = b""
    for q in (88, 84, 80, 75, 70, 64, 58, 50):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True, subsampling=2 if q < 84 else 1)
        out = buf.getvalue()
        if len(out) <= limit:
            break
    return out


def _png(im) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ================================================================ 1. кадр тревоги
def alarm_card(snapshot_path: Path, alarm: dict) -> bytes | None:
    """Кадр события в рамке для Telegram: шапка (уровень, время, нарушение, камера) и полоса срока внизу.

    alarm: title, severity (critical/high/medium), camera_label, created (unix), deadline (unix),
    необязательно status (open/ack/resolved), ack_by, resolved_by, resolved_at, event_id.
    Ширина 1280, JPEG до ~250 КБ. При ошибке — исходные байты кадра (или None, если кадра нет).
    """
    try:
        return _alarm_card(Path(snapshot_path), alarm or {})
    except Exception:
        log.exception("alarm_card: не удалось нарисовать карточку, отдаю исходный кадр")
        try:
            return Path(snapshot_path).read_bytes()
        except Exception:
            return None


def _alarm_card(snapshot_path: Path, a: dict) -> bytes:
    from PIL import Image, ImageDraw, ImageEnhance
    W, M = 1280, 44
    snap = Image.open(snapshot_path).convert("RGB")
    sh_h = max(240, min(960, round(snap.height * W / snap.width)))
    snap = snap.resize((W, sh_h), Image.LANCZOS) if snap.size != (W, sh_h) else snap
    status = str(a.get("status") or "open")
    resolved = status == "resolved"
    sev = str(a.get("severity") or "high")
    label, chip, chip_fg = SEVERITY.get(sev, SEVERITY["high"])
    if resolved:
        label, chip, chip_fg = "УСТРАНЕНО", PAL["ok"], PAL["navy_deep"]
    deadline = a.get("deadline")
    overdue = not resolved and deadline is not None and time.time() > float(deadline)

    BAND, STRIP = 268, 104
    H = BAND + sh_h + STRIP
    sh = Sheet(W, H, PAL["navy"])

    # --- шапка: планшет, сверху сигнальная лента цвета уровня
    sh.plate((0, 0, W, BAND), center=(W * 0.72, BAND * 0.2), grid=20, seed=3, corners=False)
    sh.tape((0, 0, W, 12), chip, PAL["navy_deep"], period=30)

    # строка 1: плашка уровня (+ «принял»), справа время МСК
    y1 = 82
    cw = sh.tw(label, "disp", 38, 800, 2.6) + 30 + 26
    sh.rect((M, y1 - 42, M + cw, y1 + 12), fill=chip)
    if sev == "critical" and not resolved:           # критичное — со штриховкой, как .sev.critical
        sh.hatch((M, y1 - 42, M + cw, y1 + 12), rgba((120, 0, 0), 0.22), gap=9, width=2.2)
    if resolved:                                     # галочка
        sh.ink([(M + 15, y1 - 16), (M + 23, y1 - 7), (M + 37, y1 - 27)], chip_fg, 4, seed=2, amp=0.5)
    else:                                            # ромб
        cx, cy = M + 25, y1 - 15
        sh.poly([(cx, cy - 9), (cx + 9, cy), (cx, cy + 9), (cx - 9, cy)], fill=chip_fg)
    sh.text(M + 45, y1, label, "disp", 38, 800, chip_fg, track=2.6)
    x = M + cw + 16
    who = a.get("resolved_by") if resolved else a.get("ack_by") if status == "ack" else None
    if who or status == "ack":
        tag = (f"{'устранил' if resolved else 'принял'}: {who}" if who else "принято").upper()
        tag = sh.clip(tag, 520, "mono", 24, 700)
        tw = sh.tw(tag, "mono", 24, 700)
        col = PAL["ok"] if resolved else PAL["med"]
        sh.rect((x, y1 - 42, x + tw + 28, y1 + 12), outline=col, width=2)
        sh.text(x + 14, y1 - 7, tag, "mono", 24, 700, col)
    t = _hms(a.get("created"))
    tx = sh.text(W - M, y1 + 4, t, "mono", 54, 700, PAL["chalk"], anchor="rs")
    sh.text(W - M - tx - 14, y1 - 4, "МСК", "mono", 20, 600, PAL["pale"], anchor="rs", track=2)
    d = _dt(a.get("created"))
    if d:
        sh.text(W - M - tx - 14, y1 - 30, d.strftime("%d.%m"), "mono", 20, 500, PAL["dim"], anchor="rs")

    # строка 2: что случилось — крупно
    title = str(a.get("title") or "Нарушение").upper()
    size = sh.fit(title, W - 2 * M, "disp", 100, 800, 52, track=1.5)
    y2 = 186
    tw = sh.text(M - 3, y2, title, "disp", size, 800, PAL["chalk"], track=size * 0.015)
    if tw < W - 2 * M - 80:                          # размерная линия до края — мотив чертежа
        lx0, lx1, ly = M + tw + 22, W - M, y2 - size * 0.34
        sh.line([(lx0, ly), (lx1, ly)], rgba(PAL["lav"], 0.45), 1.2)
        for xx in (lx0, lx1):
            sh.line([(xx, ly - 7), (xx, ly + 7)], rgba(PAL["lav"], 0.45), 1.2)

    # строка 3: где — камера
    y3 = 242
    camera_glyph(sh, M + 2, y3 - 28, 44, 14, PAL["pale"], led=None, width=2.2)
    cam = str(a.get("camera_label") or a.get("camera_title") or "камера")
    cam = sh.clip(cam, W - 2 * M - 70, "body", 36, 600)
    sh.text(M + 64, y3, cam, "body", 36, 600, PAL["pale"])

    # --- нижняя полоса: срок устранения
    y0 = BAND + sh_h
    sh.plate((0, y0, W, H), center=(W * 0.2, 0), grid=20, seed=4, corners=False, light=0.6)
    tape_c = PAL["ok"] if resolved else PAL["crit"] if overdue else PAL["med"]
    sh.tape((0, y0, W, y0 + 8), tape_c, PAL["navy_deep"], period=24)
    yb = y0 + 74
    if resolved:
        k = sh.text(M, yb, "УСТРАНЕНО В", "disp", 40, 700, PAL["chalk"], track=1.2)
        k += 14 + sh.text(M + k + 14, yb + 2, _hms(a.get("resolved_at") or time.time(), "%H:%M"), "disp", 58, 800, PAL["ok"])
        if a.get("created") and a.get("resolved_at"):
            sh.text(M + k + 18, yb, "· за " + _dur(float(a["resolved_at"]) - float(a["created"])), "mono", 26, 500, PAL["pale"])
    elif deadline is not None:
        lab = "СРОК ПРОШЁЛ В" if overdue else "НЕОБХОДИМО УСТРАНИТЬ ДО"
        k = sh.text(M, yb, lab, "disp", 40, 700, PAL["chalk"], track=1.2)
        k += 14 + sh.text(M + k + 14, yb + 2, _hms(deadline, "%H:%M"), "disp", 58, 800,
                          PAL["crit"] if overdue else PAL["med"])
        if a.get("created"):
            mins = max(1, round((float(deadline) - float(a["created"])) / 60))
            sh.text(M + k + 18, yb, f"· срок {mins} мин", "mono", 26, 500, PAL["pale"])
    # подпись системы справа
    wm = "LINQ GUARD"
    ww = sh.text(W - M, yb - 8, wm, "disp", 30, 800, PAL["chalk"], anchor="rs", track=3)
    eid = str(a.get("event_id") or a.get("id") or "")
    if eid:
        sh.text(W - M, yb + 20, sh.clip("#" + eid, 260, "mono", 17, 500), "mono", 17, 500, PAL["dim"], anchor="rs")
    camera_glyph(sh, W - M - ww - 62, yb - 34, 38, 16, PAL["chalk"], led=PAL["crit"], width=2)

    out = sh.done()
    # --- сам кадр: вставляем без пересэмплинга; устранённое — приглушаем
    if resolved:
        snap = ImageEnhance.Brightness(ImageEnhance.Color(snap).enhance(0.25)).enhance(0.85)
    out.paste(snap, (0, BAND))
    d = ImageDraw.Draw(out, "RGBA")
    d.line([(0, BAND), (W, BAND)], fill=rgba(PAL["lav"], 0.5), width=2)
    d.line([(0, BAND + sh_h - 1), (W, BAND + sh_h - 1)], fill=rgba(PAL["lav"], 0.5), width=2)
    # уголки видоискателя
    m, s, c = 22, 46, (255, 255, 255, 210)
    for x, y, sx, sy in ((m, BAND + m, 1, 1), (W - m, BAND + m, -1, 1), (m, BAND + sh_h - m, 1, -1), (W - m, BAND + sh_h - m, -1, -1)):
        for dx, dy in ((s, 3), (3, s)):              # горизонтальная и вертикальная полка уголка
            x2, y2 = x + sx * dx, y + sy * dy
            d.rectangle((min(x, x2), min(y, y2), max(x, x2), max(y, y2)), fill=c)
    if resolved:                                     # штамп поверх приглушённого кадра
        _stamp(out, W // 2, BAND + sh_h // 2, "УСТРАНЕНО", PAL["ok"])
    return _jpeg(out)


def _stamp(im, cx, cy, text, color):
    """Штамп «УСТРАНЕНО»: рамка и слово, чуть повёрнуты, как печать на бумаге."""
    from PIL import Image, ImageDraw
    f = font("disp", 120, 800)
    tw = int(f.getlength(text)) + 40
    lay = Image.new("RGBA", (tw + 60, 210), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    d.rectangle((10, 10, tw + 50, 200), outline=color + (235,), width=8)
    d.rectangle((24, 24, tw + 36, 186), outline=color + (150,), width=3)
    d.text(((tw + 60) // 2, 108), text, fill=color + (240,), font=f, anchor="mm")
    lay = lay.rotate(-6, resample=Image.BICUBIC, expand=True)
    im.paste(lay, (cx - lay.width // 2, cy - lay.height // 2), lay)


# ================================================================ 2. отчёт за смену
def _num(d: dict, *keys, default=0):
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return default


def shift_card(summary: dict) -> bytes | None:
    """Итоги смены для руководителя: PNG 1080×1350.

    summary: shift_id, shift_name (или name/title), start/end (unix) — необязательно;
    totals {violations, critical, people, open} (или те же ключи на верхнем уровне; синонимы total, open_alarms);
    by_type [{title, count, severity?}], by_camera [{camera_no?, camera_title, count}],
    sla {median_ack_s, median_resolve_s, on_time_share 0..1|None, escalated, overdue, limit_min?} — необязательно.
    """
    try:
        return _png(_shift_card(summary or {}))
    except Exception:
        log.exception("shift_card: не удалось нарисовать отчёт")
        return None


def _shift_card(s: dict):
    W, H, M = 1080, 1350, 56
    sh = Sheet(W, H, PAL["navy"])
    sh.plate((0, 0, W, H), center=(W * 0.75, H * 0.12), grid=24, seed=7)
    tot = s.get("totals") or {}

    def get(*keys) -> int:                           # число из totals или с верхнего уровня, по первому найденному ключу
        v = _num(tot, *keys, default=None)
        return int(v if v is not None else _num(s, *keys) or 0)

    # --- шапка
    kw = sh.text(M, 74, "LINQ GUARD · ОТЧЁТ ЗА СМЕНУ", "mono", 21, 600, PAL["pale"], track=3.2)
    sh.line([(M + kw + 16, 67), (W - M, 67)], rgba(PAL["lav"], 0.35), 1.2)
    name = str(s.get("shift_name") or s.get("name") or s.get("title") or "Смена").upper()
    size = sh.fit(name, W - 2 * M, "disp", 92, 800, 50, track=1)
    sh.text(M - 3, 160, name, "disp", size, 800, PAL["chalk"], track=size * 0.01)
    st, en = _dt(s.get("start")), _dt(s.get("end"))
    when = st.strftime("%d.%m.%Y · %H:%M") + (f"–{en.strftime('%H:%M')}" if en else "") if st else ""
    sh.text(M, 206, when or " ", "mono", 25, 500, PAL["lav"])
    if s.get("shift_id"):
        sh.text(W - M, 206, f"№ {s['shift_id']}", "mono", 22, 500, PAL["dim"], anchor="rs")
    sh.ruler(M, W - M, 238, rgba(PAL["lav"], 0.6), PAL["med"], 1.0)

    # --- четыре главных числа
    crit, open_n = get("critical"), get("open", "open_alarms")
    kpis = [(get("violations", "total"), "нарушений\nза смену", PAL["chalk"]),
            (crit, "из них\nкритичных", PAL["crit"] if crit else PAL["chalk"]),
            (get("people"), "людей\nв кадре", PAL["chalk"]),
            (open_n, "тревог\nне закрыто", PAL["high"] if open_n else PAL["ok"])]
    ky0, ky1 = 262, 446
    cw = (W - 2 * M) / 4
    for i, (n, lab, col) in enumerate(kpis):
        x = M + i * cw
        if i:
            sh.line([(x, ky0 + 18), (x, ky1 - 14)], rgba(PAL["lav"], 0.25), 1.2)
        nsz = sh.fit(str(n), cw - 34, "disp", 124, 800, 60)
        sh.text(x + (18 if i else 0), ky0 + 120, str(n), "disp", nsz, 800, col)
        for j, ln in enumerate(lab.split("\n")):
            sh.text(x + (20 if i else 2), ky0 + 152 + j * 25, ln.upper(), "mono", 19, 600, PAL["pale"], track=1.2)
    sh.line([(M, ky1), (W - M, ky1)], rgba(PAL["lav"], 0.35), 1.2)

    # --- бумажный лист: нарушения по типам (подпись над полосой — длинные названия не режутся)
    rows = sorted([r for r in (s.get("by_type") or []) if int(r.get("count") or 0) > 0], key=lambda r: -int(r["count"]))
    if len(rows) > 6:                                # хвост — в «прочие», чтобы строки читались
        rows = rows[:5] + [{"title": "Прочие", "count": sum(int(r["count"]) for r in rows[5:])}]
    RH, px0, px1 = 64, M + 26, W - M - 26
    py0 = 474
    py1 = py0 + 84 + max(1, len(rows)) * RH + 12
    sh.shadow((M - 6, py0, W - M + 6, py1), offset=(0, 12), blur=16, alpha=0.55)
    sh.paper((M - 6, py0, W - M + 6, py1), seed=11)
    section_head(sh, px0, py0 + 56, px1, "01", "Нарушения по типам", PAL["ink"], PAL["ink_soft"],
                 meta=f"всего {sum(int(r['count']) for r in rows)}")
    mx = max([int(r["count"]) for r in rows] or [1])
    if not rows:
        sh.text(px0, py0 + 130, "Нарушений не зафиксировано", "body", 30, 500, PAL["ok_ink"])
    for i, r in enumerate(rows):
        y = py0 + 84 + i * RH
        n = int(r["count"])
        crit_row = str(r.get("severity") or "") == "critical"
        lx = px0
        if crit_row:                                 # критичные типы — красный ромб у подписи (текст остаётся чернилами)
            sh.poly([(lx + 7, y + 10), (lx + 14, y + 17), (lx + 7, y + 24), (lx, y + 17)], fill=PAL["crit_ink"])
            lx += 24
        sh.text(lx, y + 27, sh.clip(str(r.get("title") or "—"), px1 - lx - 80, "body", 27, 600), "body", 27, 600, PAL["ink"])
        L = max(8, (px1 - 74 - px0) * n / mx)
        box = (px0, y + 36, px0 + L, y + 54)
        sh.rect(box, fill=rgba(PAL["crit_ink"] if crit_row else PAL["blue_ink"], 0.10))
        sh.hatch(box, rgba(PAL["crit_ink"] if crit_row else PAL["ink"], 0.6), gap=6, width=1.3, seed=i + 3)
        sh.ink([box[:2], (box[2], box[1]), box[2:], (box[0], box[3])], PAL["ink"], 1.9, seed=i + 1, amp=0.8, closed=True)
        sh.text(px0 + L + 14, y + 59, str(n), "disp", 40, 800, PAL["ink"])

    # --- низ: камеры и реакция
    by0 = py1 + 54
    cams = sorted(s.get("by_camera") or [], key=lambda r: -int(r.get("count") or 0))[:4]
    sla = s.get("sla") or None
    split = W - M - 400 if sla else W - M
    section_head(sh, M, by0, split - 36, "02", "Камеры", PAL["chalk"], rgba(PAL["lav"], 0.7), size=32)
    cmx = max([int(c.get("count") or 0) for c in cams] or [1])
    for i, c in enumerate(cams):
        y = by0 + 56 + i * 56
        no = str(c.get("camera_no") or "").strip()
        x = M
        if no:
            nw = sh.tw(no, "mono", 21, 700)
            sh.rect((x, y - 25, x + nw + 16, y + 7), outline=PAL["cyan"], width=1.6)
            sh.text(x + 8, y - 2, no, "mono", 21, 700, PAL["cyan"])
            x += nw + 28
        n = int(c.get("count") or 0)
        t = sh.clip(str(c.get("camera_title") or "—"), split - 36 - x - sh.tw(str(n), "disp", 38, 800) - 20, "body", 26, 500)
        sh.text(x, y, t, "body", 26, 500, PAL["chalk"])
        sh.text(split - 36, y + 3, str(n), "disp", 38, 800, PAL["chalk"], anchor="rs")
        sh.line([(M, y + 17), (split - 36, y + 17)], rgba(PAL["lav"], 0.18), 1)
        sh.line([(M, y + 17), (M + (split - 36 - M) * n / cmx, y + 17)], PAL["med"], 3)
    if not cams:
        sh.text(M, by0 + 56, "нет данных", "body", 26, 500, PAL["dim"])

    if sla:
        x0 = split
        section_head(sh, x0, by0, W - M, "03", "Реакция", PAL["chalk"], rgba(PAL["lav"], 0.7), meta="медиана", size=32)
        share = sla.get("on_time_share")
        cx, cy, r = x0 + 78, by0 + 128, 66
        sh.circle(cx, cy, r, outline=rgba(PAL["lav"], 0.22), width=10)
        if share is not None:
            col = PAL["ok"] if share >= 0.8 else PAL["med"] if share >= 0.5 else PAL["crit"]
            if share > 0:
                sh.arc(cx, cy, r, -90, -90 + 360 * float(share), col, 10)
            sh.text(cx, cy + 16, f"{round(float(share) * 100)}%", "disp", 46, 800, PAL["chalk"], anchor="ms")
        else:
            sh.text(cx, cy + 16, "—", "disp", 46, 800, PAL["dim"], anchor="ms")
        lim = sla.get("limit_min")
        sh.text(cx, cy + r + 32, "В СРОК", "mono", 16, 700, PAL["pale"], anchor="ms", track=1.5)
        if lim:
            sh.text(cx, cy + r + 54, f"срок {lim} мин", "mono", 15, 500, PAL["dim"], anchor="ms")
        esc, over = int(sla.get("escalated") or 0), int(sla.get("overdue") or 0)
        items = [("принято за", _dur(sla.get("median_ack_s")), PAL["chalk"]),
                 ("устранено за", _dur(sla.get("median_resolve_s")), PAL["chalk"]),
                 ("руководителю", str(esc), PAL["high"] if esc else PAL["chalk"]),
                 ("не в срок", str(over), PAL["crit"] if over else PAL["chalk"])]
        sx = x0 + 182
        for i, (k, v, col) in enumerate(items):
            y = by0 + 76 + i * 56
            sh.text(sx, y - 22, k.upper(), "mono", 15, 600, PAL["pale"], track=1)
            sh.text(sx, y + 12, v, "disp", 34, 700, col)

    # --- подвал
    sh.line([(M, H - 58), (W - M, H - 58)], rgba(PAL["lav"], 0.25), 1)
    gen = datetime.now(TZ).strftime("%d.%m.%Y %H:%M")
    sh.text(M, H - 28, f"сформировано {gen} МСК", "mono", 18, 500, PAL["dim"])
    sh.text(W - M, H - 28, "LINQ GUARD", "disp", 24, 800, PAL["lav"], anchor="rs", track=3)
    return sh.done()


# ================================================================ 3. личные итоги сотрудника
def person_card(person: dict, events: list[dict], photo_path: Path | None) -> bytes | None:
    """Итоги смены для самого сотрудника: PNG 1080×1080, спокойный тон — подсказка, а не взыскание.

    person: no (или number/id), name? (по умолчанию «Сотрудник №N»), shift_name?, date? (unix);
    events: [{created|started_at, title, camera_label|camera_title}]; photo_path — фото лица или None.
    """
    try:
        return _png(_person_card(person or {}, list(events or []), photo_path))
    except Exception:
        log.exception("person_card: не удалось нарисовать итоги")
        return None


def _person_card(p: dict, events: list[dict], photo_path):
    from PIL import Image, ImageOps
    W = H = 1080
    M = 60
    sh = Sheet(W, H, PAL["paper"])
    sh.paper((0, 0, W, H), seed=21, grid=24, edge=0.12)
    sh.corners((0, 0, W, H), rgba(PAL["ink"], 0.35), m=18, s=22)

    no = p.get("no") or p.get("number") or p.get("id") or ""
    name = str(p.get("name") or (f"Сотрудник №{no}" if no else "Сотрудник"))
    times = [float(e.get("created") or e.get("started_at") or 0) for e in events if (e.get("created") or e.get("started_at"))]
    day = _dt(p.get("date") or (min(times) if times else time.time()))

    # --- шапка
    kw = sh.text(M, 92, "LINQ GUARD · ЛИЧНЫЕ ИТОГИ", "mono", 21, 600, PAL["ink_soft"], track=3)
    sh.line([(M + kw + 16, 85), (W - M, 85)], rgba(PAL["ink"], 0.3), 1.2)
    sh.text(M - 3, 190, "ИТОГИ СМЕНЫ", "disp", 104, 800, PAL["ink"], track=1.5)
    sub = " · ".join(x for x in ((day.strftime("%d.%m.%Y") if day else ""), str(p.get("shift_name") or "")) if x)
    sh.text(M, 236, sub, "mono", 25, 500, PAL["ink_soft"])

    # --- бейдж с фото (слегка повёрнут, на шнурке)
    bw, bh = 340, 452
    from PIL import ImageDraw
    badge = Sheet(bw + 40, bh + 40, (0, 0, 0), ss=2)
    badge.im = Image.new("RGBA", badge.im.size, (0, 0, 0, 0))
    badge.d = ImageDraw.Draw(badge.im, "RGBA")
    bx, by = 20, 20
    badge.rect((bx, by, bx + bw, by + bh), fill=PAL["navy"], radius=14)
    badge._grid((bx, by, bx + bw, by + bh), 17, PAL["grid"], 0.35, 0.6)
    badge.rect((bx + bw / 2 - 34, by + 18, bx + bw / 2 + 34, by + 32), fill=PAL["navy_deep"], radius=7)  # прорезь
    ph = 252
    px0, py0 = bx + (bw - ph) / 2, by + 56
    badge.rect((px0 - 8, py0 - 8, px0 + ph + 8, py0 + ph + 8), fill=PAL["navy_deep"])
    photo = None
    try:
        if photo_path and Path(photo_path).is_file():
            photo = ImageOps.fit(Image.open(photo_path).convert("RGB"), (ph * 2, ph * 2), Image.LANCZOS)
    except Exception:
        photo = None
    if photo is not None:
        badge.im.paste(photo, (int(px0 * 2), int(py0 * 2)))
    else:                                            # силуэт в каске вместо фото
        cx, cy = px0 + ph / 2, py0 + ph * 0.56
        badge.ink(ellipse_pts(cx, cy + ph * 0.62, ph * 0.40, ph * 0.36, math.pi, math.tau), rgba(PAL["lav"], 0.8), 2.4, seed=3)
        badge.ink(ellipse_pts(cx, cy, ph * 0.17, ph * 0.20), rgba(PAL["lav"], 0.8), 2.4, seed=4, closed=True)
        dome = ellipse_pts(cx, cy - ph * 0.08, ph * 0.21, ph * 0.18, math.pi, math.tau)
        badge.ink(dome + [dome[0]], PAL["med"], 2.6, seed=5, fill=rgba(PAL["med"], 0.9))
        badge.ink([(cx - ph * 0.27, cy - ph * 0.08), (cx + ph * 0.27, cy - ph * 0.08)], PAL["med"], 4, seed=6)
    for x, y, sx, sy in ((px0, py0, 1, 1), (px0 + ph, py0, -1, 1), (px0, py0 + ph, 1, -1), (px0 + ph, py0 + ph, -1, -1)):
        badge.line([(x, y + sy * 26), (x, y), (x + sx * 26, y)], PAL["cyan"], 3)
    badge.text(bx + bw / 2, py0 + ph + 52, "СОТРУДНИК", "mono", 18, 600, PAL["pale"], anchor="ms", track=3)
    nm = str(f"№ {no}" if no else name)
    nsz = badge.fit(nm, bw - 40, "disp", 62, 800, 30)
    badge.text(bx + bw / 2, py0 + ph + 112, nm, "disp", nsz, 800, PAL["chalk"], anchor="ms")
    badge.tape((bx + 14, by + bh - 34, bx + bw - 14, by + bh - 18), PAL["med"], PAL["navy_deep"], period=18)
    b_img = badge.done().rotate(2.5, resample=Image.BICUBIC, expand=True)
    bx_, by_ = M - 14, 292
    # шнурок
    sh.ink([(bx_ + b_img.width / 2 - 70, 250), (bx_ + b_img.width / 2 - 4, by_ + 44)], PAL["blue_ink"], 6, seed=8, amp=1.5)
    sh.ink([(bx_ + b_img.width / 2 + 80, 250), (bx_ + b_img.width / 2 + 10, by_ + 44)], PAL["blue_ink"], 6, seed=9, amp=1.5)
    sh.shadow((bx_ + 24, by_ + 24, bx_ + b_img.width - 24, by_ + b_img.height - 20), offset=(6, 12), blur=14, alpha=0.32, radius=14)
    sh.im.paste(b_img.resize((b_img.width * 2, b_img.height * 2), Image.LANCZOS), (bx_ * 2, by_ * 2),
                b_img.resize((b_img.width * 2, b_img.height * 2), Image.LANCZOS))

    # --- справа: сколько и каких замечаний
    rx = 470
    n = len(events)
    if n:
        nw = sh.text(rx, 420, str(n), "disp", 180, 800, PAL["ink"])
        sh.text(rx + nw + 18, 360, _plural(n, "замечание", "замечания", "замечаний"), "body", 34, 600, PAL["ink"])
        sh.text(rx + nw + 18, 404, "за смену", "body", 30, 500, PAL["ink_soft"])
    else:
        sh.text(rx, 380, "Чистая смена", "disp", 74, 800, PAL["ok_ink"])
        sh.text(rx, 428, "ни одного замечания", "body", 30, 500, PAL["ink_soft"])
    counts: dict[str, int] = {}
    for e in events:
        t = str(e.get("title") or "Нарушение")
        counts[t] = counts.get(t, 0) + 1
    y = 482
    for t, c in sorted(counts.items(), key=lambda kv: -kv[1])[:4]:
        sh.ink([(rx, y - 26), (rx + 52, y - 26), (rx + 52, y + 10), (rx, y + 10)], PAL["ink"], 1.8, seed=y, amp=0.6, closed=True)
        sh.text(rx + 26, y + 2, f"{c}", "disp", 34, 800, PAL["ink"], anchor="ms")
        sh.text(rx + 70, y + 2, sh.clip(t, W - M - rx - 70, "body", 29, 500), "body", 29, 500, PAL["ink"])
        y += 52

    # --- хронология
    ty = 806
    section_head(sh, M, ty, W - M, "", "Когда и где", PAL["ink"], PAL["ink_soft"], size=32)
    evs = sorted(events, key=lambda e: float(e.get("created") or e.get("started_at") or 0))
    shown = evs[:3] if len(evs) > 4 else evs[:4]
    yy = ty + 50
    for e in shown:
        t = _hms(e.get("created") or e.get("started_at"), "%H:%M")
        sh.text(M, yy, t, "mono", 26, 700, PAL["ink"])
        title = str(e.get("title") or "Нарушение")
        tw = sh.text(M + 104, yy, sh.clip(title, 400, "body", 27, 600), "body", 27, 600, PAL["ink"])
        cam = str(e.get("camera_label") or e.get("camera_title") or "")
        if cam:
            sh.text(M + 104 + tw + 16, yy, sh.clip("· " + cam, W - M - (M + 104 + tw + 16), "body", 25, 400), "body", 25, 400,
                    PAL["ink_soft"])
        sh.line([(M, yy + 14), (W - M, yy + 14)], rgba(PAL["ink"], 0.18), 1, dash=(2, 5))
        yy += 44
    if len(evs) > len(shown):
        sh.text(M + 104, yy, f"и ещё {len(evs) - len(shown)} — в журнале смены", "body", 25, 500, PAL["ink_soft"])
    if not evs:
        sh.text(M, yy, "Замечаний не было — так держать.", "body", 27, 500, PAL["ink_soft"])

    # --- рукописная пометка внизу
    note = "Спасибо за смену! Берегите себя." if n else "Спасибо за смену и за аккуратность!"
    sh.text(W - M, H - 40, note, "hand", 44, 700, PAL["blue_ink"], anchor="rs")
    sh.text(M, H - 44, "это не взыскание — подсказка, где был риск" if n else " ", "mono", 17, 500, PAL["ink_faint"])
    return sh.done()
