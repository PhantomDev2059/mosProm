"""Картинки для BotFather и образцы карточек бота: python tools/make_bot_assets.py [--out DIR] [--no-samples].

Пишет в DIR (по умолчанию ../bot_assets рядом с репозиторием):
- avatar.png 640×640 — знак: каска под конусом камеры, монограмма LG; композиция влезает в круг Telegram;
- description.png 640×360 — «картинка описания»: план цеха с камерами, словесный знак и девиз;
- samples/ — образцы карточек из api/cards.py на синтетических данных (кадр тоже рисуется здесь).
Рисование — тем же набором, что и карточки (api.cards.Sheet): планшет, чернильные линии, штриховка.
"""
import argparse
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api.cards import (PAL, Sheet, alarm_card, camera_glyph, cone, ellipse_pts, person_card, rgba,  # noqa: E402
                       shift_card)


# ---------------------------------------------------------------- аватар
def helmet(sh: Sheet, cx, cy, r, seed=1, outline=PAL["navy_deep"], lw=None):
    """Защитная каска: купол, гребень, козырёк; жёлтая заливка, штриховка тени, чернильный контур.

    (cx, cy) — середина нижнего края купола, r — полуширина купола."""
    lw = lw or r * 0.045
    dome = ellipse_pts(cx, cy, r, r * 0.92, math.pi, math.tau, 64)
    sh.poly(dome, fill=PAL["med"])
    # тень справа — штриховка по куполу
    sh.hatch((cx + r * 0.25, cy - r, cx + r, cy), rgba((120, 80, 0), 0.5), gap=r * 0.05, width=r * 0.012, angle=-50,
             seed=seed, mask_poly=dome)
    # блик слева
    sh.ink(ellipse_pts(cx - r * 0.05, cy, r * 0.72, r * 0.70, math.pi * 1.12, math.pi * 1.42, 20), rgba((255, 248, 220), 0.9),
           r * 0.035, seed=seed + 4, amp=0.6)
    # гребень по центру купола
    rid = [(cx - r * 0.13, cy - r * 0.05)] + ellipse_pts(cx, cy, r * 0.13, r * 0.98, math.pi, math.tau, 24)[1:-1] + \
          [(cx + r * 0.13, cy - r * 0.05)]
    sh.poly(rid, fill=(255, 214, 80))
    sh.ink(rid, outline, lw * 0.7, seed=seed + 2, amp=r * 0.004)
    sh.ink(dome + [dome[0]], outline, lw, seed=seed + 1, amp=r * 0.006)
    # козырёк: длинный, чуть изогнутый
    brim = [(cx - r * 1.18, cy - r * 0.02), (cx + r * 1.22, cy - r * 0.02), (cx + r * 1.26, cy + r * 0.10),
            (cx + r * 0.8, cy + r * 0.15), (cx - r * 0.8, cy + r * 0.15), (cx - r * 1.2, cy + r * 0.10)]
    sh.poly(brim, fill=(226, 172, 26))
    sh.ink(brim, outline, lw, seed=seed + 3, amp=r * 0.005, closed=True)


def make_avatar(path: Path):
    S = 640
    sh = Sheet(S, S, PAL["navy"], ss=3)
    sh.plate((0, 0, S, S), center=(S * 0.5, S * 0.42), grid=32, seed=5, corners=False)
    c = S / 2
    # круговые метки как на «чертеже» procedural-film
    for i, rr in enumerate((262, 286)):
        sh.circle(c, c, rr, outline=rgba(PAL["lav"], 0.22 if i else 0.32), width=1.4)
    for i in range(120):
        a = i / 120 * math.tau
        L = 14 if i % 10 == 0 else 6
        sh.line([(c + math.cos(a) * 286, c + math.sin(a) * 286), (c + math.cos(a) * (286 - L), c + math.sin(a) * (286 - L))],
                rgba(PAL["lav"], 0.5 if i % 10 == 0 else 0.28), 1.3 if i % 10 == 0 else 1)
    # камера сверху слева, конус — вниз на каску
    lens = camera_glyph(sh, 118, 156, 132, 34, PAL["chalk"], fill=PAL["navy2"], led=PAL["crit"], width=4.2, seed=7)
    cone(sh, lens, 40, 44, 370, PAL["cyan"], alpha=0.16, seed=12, arcs=3, width=3)
    # кронштейн к «стене»
    sh.ink([(70, 120), (70, 196)], PAL["chalk"], 5, seed=3, amp=0.5)
    sh.ink([(70, 156), (118, 156)], PAL["chalk"], 4.2, seed=4, amp=0.5)
    # каска с монограммой
    hx, hy, hr = c + 18, 418, 178
    helmet(sh, hx, hy, hr, seed=2, lw=7)
    sh.text(hx - 4, hy - 46, "LG", "disp", 150, 800, PAL["navy_deep"], anchor="ms", track=2)
    # сигнальная лента вдоль низа
    sh.tape((c - 150, 474, c + 150, 492), PAL["med"], PAL["navy_deep"], period=22)
    sh.text(c, 540, "LINQ GUARD", "mono", 26, 700, PAL["pale"], anchor="ms", track=6)
    sh.done().save(path, optimize=True)


# ---------------------------------------------------------------- картинка описания
def make_description(path: Path):
    W, H = 640, 360
    sh = Sheet(W, H, PAL["navy"], ss=3)
    sh.plate((0, 0, W, H), center=(W * 0.72, H * 0.4), grid=16, seed=9, corners=True)
    # план цеха справа: стены двойной линией
    X0, Y0, X1, Y1 = 300, 34, 612, 326
    w = rgba(PAL["chalk"], 0.85)
    sh.ink([(X0, Y0), (X1, Y0), (X1, Y1), (X0, Y1)], w, 2.2, seed=1, amp=0.7, closed=True)
    sh.ink([(X0 + 7, Y0 + 7), (X1 - 7, Y0 + 7), (X1 - 7, Y1 - 7), (X0 + 7, Y1 - 7)], rgba(PAL["lav"], 0.5), 1, seed=2,
           amp=0.6, closed=True)
    # перегородка с проёмом
    sh.ink([(X0 + 7, 196), (440, 196)], w, 2, seed=3, amp=0.6)
    sh.ink([(486, 196), (X1 - 7, 196)], w, 2, seed=4, amp=0.6)
    sh.arc(486, 196, 46, 180, 270, rgba(PAL["lav"], 0.45), 1)
    # станки — штрихованные прямоугольники
    for i, (a, b, c_, d) in enumerate(((330, 60, 400, 110), (420, 60, 490, 110), (520, 60, 590, 150), (330, 222, 420, 300),
                                       (520, 230, 590, 300))):
        sh.hatch((a, b, c_, d), rgba(PAL["lav"], 0.35), gap=5, width=0.8, seed=i + 2)
        sh.ink([(a, b), (c_, b), (c_, d), (a, d)], rgba(PAL["chalk"], 0.8), 1.4, seed=i + 10, amp=0.5, closed=True)
    # проход: жёлтая разметка
    sh.line([(316, 132), (500, 132)], PAL["med"], 2, dash=(9, 6))
    sh.line([(316, 176), (500, 176)], PAL["med"], 2, dash=(9, 6))
    sh.line([(500, 132), (500, 176)], PAL["med"], 2, dash=(9, 6))
    sh.line([(450, 212), (450, 312)], PAL["med"], 2, dash=(9, 6))
    sh.line([(500, 212), (500, 312)], PAL["med"], 2, dash=(9, 6))
    # камеры на стенах и их конусы
    for (x, y, ang, spread, L, seed) in ((X0 + 7, Y0 + 30, 28, 48, 170, 21), (X1 - 7, 170, 170, 46, 160, 22),
                                         (X0 + 7, 300, -32, 44, 150, 23)):
        lens = camera_glyph(sh, x, y, 24, ang, PAL["chalk"], fill=PAL["navy2"], led=PAL["crit"], width=1.8, seed=seed)
        cone(sh, lens, ang, spread, L, PAL["cyan"], alpha=0.12, seed=seed, arcs=2, width=1.3)
    # нарушение: человек без каски в проходе — красный маячок и выноска
    px, py = 462, 154
    sh.circle(px, py, 16, outline=rgba(PAL["crit"], 0.75), width=1.4)
    sh.circle(px, py, 26, outline=rgba(PAL["crit"], 0.35), width=1)
    sh.glow(px, py, 6, PAL["crit"], 1)
    sh.line([(px + 16, py - 12), (px + 46, py - 40), (px + 120, py - 40)], rgba(PAL["crit"], 0.85), 1.3)
    sh.rect((px + 50, py - 58, px + 142, py - 42), fill=PAL["crit"])
    sh.text(px + 96, py - 46, "НЕТ КАСКИ", "mono", 10, 700, (255, 255, 255), anchor="ms", track=0.6)
    # затемнение под текстом слева
    lay, d, at = sh.layer((0, 0, 330, H))
    import numpy as np
    from PIL import Image
    a = np.clip(1 - np.linspace(0, 1, lay.width) ** 1.6, 0, 1) * 0.78
    arr = np.zeros((lay.height, lay.width, 4), np.uint8)
    arr[..., :3] = PAL["navy_deep"]
    arr[..., 3] = (a[None, :] * 255).astype(np.uint8)
    sh.put(Image.fromarray(arr, "RGBA"), at)
    # словесный знак и девиз
    M = 30
    sh.text(M, 70, "ВИДЕОКОНТРОЛЬ ПРОМБЕЗОПАСНОСТИ", "mono", 10.5, 600, PAL["pale"], track=1.6)
    sh.line([(M, 80), (288, 80)], rgba(PAL["lav"], 0.35), 1)
    k = sh.text(M - 2, 158, "LINQ", "disp", 84, 800, PAL["chalk"], track=1.5)
    sh.text(M + k + 10, 158, "GUARD", "disp", 84, 600, PAL["lav"], track=5)
    sh.tape((M, 176, M + 210, 183), PAL["med"], PAL["navy_deep"], period=14)
    sh.text(M, 226, "Видит нарушение —", "body", 23, 600, PAL["chalk"])
    sh.text(M, 256, "зовёт того, кто устранит", "body", 23, 600, PAL["chalk"])
    sh.text(M, 318, "каски · жилеты · падения · огонь", "mono", 11, 500, PAL["dim"], track=0.4)
    sh.done().save(path, optimize=True)


# ---------------------------------------------------------------- образцы карточек
def fake_snapshot(path: Path, seed: int = 1):
    """Синтетический кадр 1280×720: коридор цеха, лежащий человек, рамка детектора и скелет позы."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    W, H = 1280, 720
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    base = 70 + 60 * (yy / H) + 18 * np.cos((xx - 640) / 700)
    img = np.stack([base * 0.92, base * 0.96, base], -1) + rng.normal(0, 4, (H, W, 1))
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")
    d = ImageDraw.Draw(im, "RGBA")
    vx, vy = 700, 250                                     # точка схода
    d.polygon([(0, H), (W, H), (vx + 120, vy + 60), (vx - 140, vy + 60)], fill=(96, 98, 92))
    d.polygon([(0, 0), (vx - 140, vy - 120), (vx - 140, vy + 60), (0, H)], fill=(120, 124, 128))
    d.polygon([(W, 0), (vx + 120, vy - 120), (vx + 120, vy + 60), (W, H)], fill=(108, 112, 118))
    d.rectangle((vx - 140, vy - 120, vx + 120, vy + 60), fill=(150, 156, 160))
    for k in range(8):                                    # лампы на потолке
        t = k / 8
        x = vx + (640 - vx) * (1 - t) * 0.1
        y = vy - 120 - (vy - 120) * (1 - t ** 0.6)
        d.rectangle((x - 60 * (1 - t) - 10, y - 4, x + 60 * (1 - t) + 10, y + 4), fill=(235, 238, 230))
    d.line([(150, H), (vx - 60, vy + 60)], fill=(220, 184, 40), width=10)   # жёлтая разметка
    d.line([(1130, H), (vx + 50, vy + 60)], fill=(220, 184, 40), width=10)
    for x0 in (60, 1040):                                 # стеллажи
        d.rectangle((x0, 260, x0 + 170, 620), outline=(60, 64, 70), width=8)
        for y in range(300, 620, 80):
            d.line([(x0, y), (x0 + 170, y)], fill=(60, 64, 70), width=6)
            d.rectangle((x0 + 14, y - 46, x0 + 90, y - 6), fill=(140, 110, 70))
    im = im.filter(ImageFilter.GaussianBlur(1.2))
    d = ImageDraw.Draw(im, "RGBA")
    # лежащий человек
    d.ellipse((520, 520, 800, 580), fill=(40, 50, 70))
    d.ellipse((790, 512, 840, 556), fill=(200, 160, 130))
    d.polygon([(520, 540), (440, 520), (440, 548), (520, 566)], fill=(30, 36, 50))
    # рамка детектора и скелет
    d.rectangle((420, 490, 860, 600), outline=(255, 60, 50), width=4)
    try:
        f = ImageFont.truetype(str(ROOT / "assets/fonts/JetBrainsMono-VF.ttf"), 22)
    except Exception:
        f = ImageFont.load_default()
    d.rectangle((420, 458, 640, 490), fill=(255, 60, 50))
    d.text((430, 462), "fall 0.93 · id 7", fill=(255, 255, 255), font=f)
    for a, b in (((815, 534), (760, 548)), ((760, 548), (640, 552)), ((640, 552), (530, 545)), ((530, 545), (450, 530)),
                 ((760, 548), (720, 590)), ((640, 552), (600, 590))):
        d.line([a, b], fill=(127, 227, 255), width=4)
    for p in ((815, 534), (760, 548), (640, 552), (530, 545), (450, 530)):
        d.ellipse((p[0] - 6, p[1] - 6, p[0] + 6, p[1] + 6), fill=(127, 227, 255))
    d.text((20, 20), "CAM-04  2026-10-24 14:32:07", fill=(255, 255, 255), font=f)
    im.save(path, quality=90)
    return path


def fake_face(path: Path):
    """Условный «портрет» для образца бейджа: тёплый фон и мягкий силуэт (настоящих лиц в репозитории нет)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    S = 400
    yy, xx = np.mgrid[0:S, 0:S].astype(np.float32)
    bg = np.stack([150 + 30 * yy / S, 160 + 20 * yy / S, 170 + 10 * xx / S], -1)
    im = Image.fromarray(bg.astype(np.uint8), "RGB")
    d = ImageDraw.Draw(im)
    d.ellipse((60, 260, 340, 520), fill=(52, 74, 120))
    d.rectangle((170, 220, 230, 290), fill=(214, 170, 140))
    d.ellipse((120, 90, 280, 270), fill=(226, 184, 152))
    d.chord((110, 60, 290, 230), 180, 360, fill=(242, 194, 48))
    d.rectangle((96, 140, 304, 152), fill=(226, 172, 26))
    im.filter(ImageFilter.GaussianBlur(2)).save(path, quality=90)
    return path


def make_samples(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    snap = fake_snapshot(out / "_snapshot.jpg")
    now = time.time()
    t0 = now - 4 * 3600
    base = {"event_id": "a1b2c3d4", "title": "Падение человека", "severity": "critical",
            "camera_label": "К-04 · АБК, коридор 1 этажа", "created": now - 60, "deadline": now - 60 + 15 * 60}
    (out / "alarm_critical.jpg").write_bytes(alarm_card(snap, base))
    (out / "alarm_high_ack.jpg").write_bytes(alarm_card(snap, {**base, "title": "Нет каски в зоне погрузки", "severity": "high",
                                                                "status": "ack", "ack_by": "Петров А.",
                                                                "camera_label": "К-11 · Склад, рампа 3"}))
    (out / "alarm_resolved.jpg").write_bytes(alarm_card(snap, {**base, "status": "resolved", "resolved_by": "Петров А.",
                                                                "ack_by": "Петров А.", "resolved_at": now + 6 * 60}))
    (out / "alarm_overdue.jpg").write_bytes(alarm_card(snap, {**base, "created": now - 30 * 60, "deadline": now - 15 * 60}))
    summary = {
        "shift_id": "2026-10-24/Д", "shift_name": "Дневная смена", "start": t0, "end": t0 + 12 * 3600,
        "totals": {"violations": 37, "critical": 5, "people": 14, "open": 2},
        "by_type": [{"title": "Нет каски", "count": 14}, {"title": "Нет сигнального жилета", "count": 9},
                    {"title": "Падение человека", "count": 2, "severity": "critical"},
                    {"title": "Курение в неположенном месте", "count": 6}, {"title": "Человек в опасной зоне", "count": 3,
                                                                            "severity": "critical"},
                    {"title": "Разговор по телефону", "count": 2}, {"title": "Бег", "count": 1}],
        "by_camera": [{"camera_no": "К-04", "camera_title": "АБК, коридор 1 этажа", "count": 11},
                      {"camera_no": "К-11", "camera_title": "Склад, рампа 3", "count": 9},
                      {"camera_no": "К-02", "camera_title": "Цех №2, участок сварки", "count": 7},
                      {"camera_title": "Вебкамера", "count": 3}],
        "sla": {"median_ack_s": 48, "median_resolve_s": 372, "on_time_share": 0.83, "escalated": 1, "overdue": 1,
                "limit_min": 15},
    }
    (out / "shift.png").write_bytes(shift_card(summary))
    (out / "shift_quiet.png").write_bytes(shift_card({"shift_name": "Ночная смена", "start": t0, "totals": {},
                                                      "by_type": [], "by_camera": []}))
    face = fake_face(out / "_face.jpg")
    evs = [{"created": t0 + 3600, "title": "Нет каски", "camera_label": "К-04 · АБК, коридор 1 этажа"},
           {"created": t0 + 2 * 3600 + 600, "title": "Нет сигнального жилета", "camera_label": "К-11 · Склад, рампа 3"},
           {"created": t0 + 3 * 3600, "title": "Нет каски", "camera_label": "К-02 · Цех №2, участок сварки"}]
    (out / "person.png").write_bytes(person_card({"no": 7, "shift_name": "дневная смена"}, evs, face))
    (out / "person_clean.png").write_bytes(person_card({"no": 12}, [], None))
    (out / "person_many.png").write_bytes(person_card({"no": 3, "shift_name": "дневная смена"}, evs * 3, None))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT.parent / "bot_assets")
    ap.add_argument("--no-samples", action="store_true")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    make_avatar(args.out / "avatar.png")
    make_description(args.out / "description.png")
    if not args.no_samples:
        make_samples(args.out / "samples")
    print(f"готово: {args.out}")


if __name__ == "__main__":
    main()
