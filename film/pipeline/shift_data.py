"""Данные фильма «Смена» из настоящего прогона конвейера (detect.py): окна шотов вокруг срабатываний,
кадры камер, рамки только треков выбранного события, карточки бота (api/cards.py) — открытая и «устранено».
Пишет src/photos.js (FILM.SHIFT) и src/timeline.js проекта.
python shift_data.py <repo> <project> dets.json [dets2.json ...]
"""
import json
import math
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

repo, proj = Path(sys.argv[1]), Path(sys.argv[2])
dets = {}
for p in sys.argv[3:]:
    dets.update(json.loads(Path(p).read_text()))
sys.path.insert(0, str(repo))
from PIL import Image, ImageDraw  # noqa: E402

import api.cards as cards_mod  # noqa: E402
from api.cards import alarm_card  # noqa: E402
from engine.scene import FloorMap  # noqa: E402

HERE = Path(__file__).resolve().parent
CLIPS = HERE / "clips"
FRAMES = proj / ".tmp" / "frames"
CARDS = proj / ".tmp" / "cards"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
MSK = timezone(timedelta(hours=3))
SHIFT0 = datetime(2026, 10, 24, 8, 0, tzinfo=MSK).timestamp()
FPS = 25

# что показываем: момент тревоги — настоящее событие конвейера (detect.py, с калибровкой пола из cams.json);
# speed — замедление показа, vehicle — рамка погрузчика, maxh — не рисовать рамку крупнее (человек у камеры)
PICKS = [
    dict(shot="v01-ppe", clip="ppe", sev="high", tracks=[1, 2, 4], types=["no_vest", "no_helmet"], inset=0.06, label="НЕТ КАСКИ И ЖИЛЕТА",
         title="Нет каски и жилета", sub="Трое посетителей прошли в цех без СИЗ", cam="К-03 · ЦЕХ №2",
         place="Цех №2 · центральный проход", min=37, sec=14, ack=1, res=4, short="СИЗ", type="no_helmet"),
    dict(shot="v02-phone", clip="phone", sev="medium", tracks=[1], items=["phone"], label="ТЕЛЕФОН",
         title="Телефон в рабочей зоне", sub="Разговор по телефону в проходе склада", cam="К-07 · СКЛАД ГОТ. ПРОДУКЦИИ",
         place="Склад готовой продукции", min=96, sec=41, ack=2, res=6, short="Телефон", type="phone"),
    dict(shot="v03-proximity", clip="proximity", sev="critical", vehicle=True, label="ПЕШЕХОД",
         title="Пешеход у погрузчика", sub="Человек в зоне движения погрузчика ближе 3 м", cam="К-02 · СКЛАД, ПРОЕЗД",
         place="Склад · проезд погрузчиков", min=141, sec=27, ack=1, res=3, short="Погрузчик", type="vehicle_proximity"),
    dict(shot="v04-smoking", clip="smoking", sev="high", tracks=[1], items=["cigarette"], label="КУРЕНИЕ",
         title="Курение на территории", sub="Сигарета вне места для курения", cam="К-05 · ТЕРРИТОРИЯ",
         place="Территория у склада", min=188, sec=5, ack=2, res=5, short="Курение", type="smoking"),
    dict(shot="v05-fall", clip="fall", sev="critical", tracks=[1], label="ПАДЕНИЕ", title="Падение человека",
         sub="Сотрудник упал в коридоре АБК", cam="К-04 · АБК, КОРИДОР", place="АБК · коридор 2 этажа",
         min=243, sec=22, ack=1, res=3, plate="navy", short="Падение", type="fall"),
    dict(shot="v06-immobile", clip="fall", sev="critical", tracks=[1], label="ЛЕЖИТ БЕЗ ДВИЖЕНИЯ",
         title="Лежит без движения", sub="Не встаёт больше 10 секунд — нужна помощь", cam="К-04 · АБК, КОРИДОР",
         place="АБК · коридор 2 этажа", min=243, sec=33, ack=1, res=3, plate="navy", short="Без движения", type="immobile"),
    dict(shot="v07-running", clip="running", sev="medium", maxh=560, label="БЕГ",
         title="Бег по цеху", sub="Бегом через склад — риск столкновения и падения", cam="К-07 · СКЛАД ГОТ. ПРОДУКЦИИ",
         place="Склад готовой продукции · ворота", min=318, sec=50, ack=2, res=5, short="Бег", type="running"),
    dict(shot="v08-fire", clip="fire", sev="critical", zone="fire", speed=0.4, label="ОТКРЫТЫЙ ОГОНЬ",
         title="Открытый огонь", sub="Возгорание у ёмкости — потушено огнетушителем", cam="К-06 · ПРОМПЛОЩАДКА",
         place="Промплощадка · участок огневых работ", min=391, sec=9, ack=1, res=2, plate="navy", short="Огонь", type="open_fire"),
    dict(shot="v09-smoke", clip="smoke", sev="high", zone="smoke", label="ЗАДЫМЛЕНИЕ", title="Задымление",
         sub="Дым над участком разливки — проверить вытяжку", cam="К-01 · ЦЕХ №1", place="Цех №1 · участок разливки",
         min=497, sec=48, ack=2, res=7, short="Дым", type="smoke"),
]
T_OPEN = 2.0   # тревога на 2-й секунде шота
CONFIRM = 1.0  # рамка — за секунду до тревоги, кольцо подтверждения


def smooth(samples, a=0.5):
    """Сглаживание рамок вперёд-назад (без сдвига по времени): без дрожи трекера."""
    if len(samples) < 3:
        return samples
    out = [list(x) for x in samples]
    for rng in (range(1, len(out)), range(len(out) - 2, -1, -1)):
        prev = None
        for i in rng:
            j = i - 1 if rng.step == 1 else i + 1
            for k in range(1, 5):
                out[i][k] = a * out[i][k] + (1 - a) * out[j][k]
    return [[x[0]] + [round(v, 1) for v in x[1:]] for x in out]


def samples_people(d, tracks, t0, t1, maxh=None):
    out = {k: [] for k in tracks}
    for f in d["frames"]:
        if not (t0 <= f["t"] <= t1):
            continue
        for p in f["people"]:
            if p["id"] in out and (maxh is None or p["box"][3] - p["box"][1] <= maxh):
                out[p["id"]].append([f["t"], *p["box"]])
    return out


def samples_cls(d, classes, t0, t1, minscore=0.3):
    """Самая крупная рамка класса в кадре (огонь, дым, погрузчик)."""
    res = []
    for f in d["frames"]:
        if not (t0 <= f["t"] <= t1):
            continue
        bs = [i["box"] for i in f["items"] if i["cls"] in classes and i["score"] >= minscore]
        if bs:
            res.append([f["t"], *max(bs, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))])
    return res


def samples_items(d, classes, person, t0, t1):
    """Предмет рядом с нарушителем: лучший по уверенности, центр внутри расширенной рамки человека."""
    res = []
    pbox = {s[0]: s[1:] for s in person}
    for f in d["frames"]:
        if not (t0 <= f["t"] <= t1) or f["t"] not in pbox:
            continue
        x1, y1, x2, y2 = pbox[f["t"]]
        pad = 0.15 * (x2 - x1)
        cand = [i for i in f["items"] if i["cls"] in classes
                and x1 - pad <= (i["box"][0] + i["box"][2]) / 2 <= x2 + pad and y1 - pad <= (i["box"][1] + i["box"][3]) / 2 <= y2]
        if cand:
            res.append([f["t"], *max(cand, key=lambda i: i["score"])["box"]])
    return res


def frame_img(clip, t):
    out = HERE / ".snap.jpg"
    subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(CLIPS / f"{clip}.mp4"),
                    "-frames:v", "1", "-q:v", "2", str(out)], check=True)
    return Image.open(out).convert("RGB")


CAMS = json.loads((HERE / "cams.json").read_text()) if (HERE / "cams.json").exists() else {}


def metric_series(pk, person, vehicle, floor):
    """Цифры по калибровке пола: расстояние до края техники (м) или скорость человека (м/с)."""
    fm = FloorMap(floor["image_pts"], floor["world_m"])
    w = lambda pt: fm.to_world(pt)  # noqa: E731
    out = []
    if vehicle is not None:
        vb = {round(x[0], 1): x[1:] for x in vehicle}
        for x in person:
            v = vb.get(round(x[0], 1))
            if v is None:
                continue
            fx, fy = (x[1] + x[3]) / 2, x[4]
            near = (min(max(fx, v[0]), v[2]), v[3])
            out.append([x[0], round(float(((w((fx, fy)) - w(near)) ** 2).sum() ** 0.5), 2)])
    else:
        for i, x in enumerate(person):
            if x[4] > 700:                       # ноги за краем кадра — скорость по ним не считаем
                continue
            j = next((k for k in range(i, -1, -1) if x[0] - person[k][0] >= 0.8), None)
            if j is None:
                continue
            a, b = person[j], x
            pa, pb = w(((a[1] + a[3]) / 2, a[4])), w(((b[1] + b[3]) / 2, b[4]))
            out.append([x[0], round(float(((pb - pa) ** 2).sum() ** 0.5) / (b[0] - a[0]), 2)])
    return out


events, images, shots = [], {}, []
FRAMES.mkdir(parents=True, exist_ok=True)
CARDS.mkdir(parents=True, exist_ok=True)
for n, pk in enumerate(PICKS):
    d = dets[pk["clip"]]
    clip_dur = d["frames_total"] / d["fps"]
    sp_ = pk.get("speed", 1.0)
    # тревога — настоящее событие конвейера этого типа (для людей — по их трекам)
    types = pk.get("types", [pk["type"]])
    evs = [e for e in d["events"] if e["type"] in types and (pk.get("zone") or not pk.get("tracks") or e["track"] in pk["tracks"])]
    assert evs, f"{pk['shot']}: конвейер не выдал {types}"
    ev0 = min(evs, key=lambda e: e["opened"])
    opened = ev0["opened"]
    if not pk.get("zone") and not pk.get("tracks"):
        pk["tracks"] = [ev0["track"]]
    win0 = max(0.0, opened - T_OPEN * sp_)
    loc = lambda tc: round((tc - win0) / sp_, 2)  # noqa: E731
    t_open = loc(opened)
    t_start = max(0.05, t_open - CONFIRM)
    dur = max(6.0, math.ceil((t_open + 3.7) * 2) / 2)
    hold = loc(clip_dur) if win0 + dur * sp_ > clip_dur else None
    t_ack, t_res = t_open + 1.5, t_open + 2.7
    c0 = win0 + t_start * sp_ - 0.21      # первый кадр анализа для рамки
    c1 = win0 + dur * sp_ + 0.3

    def conv(ss, maxh=None):
        out = smooth([[loc(x[0]), *x[1:]] for x in ss])
        if hold is not None and out:
            t = out[-1][0]
            while t < dur + 0.5:          # стоп-кадр: рамка стоит до конца шота
                t = round(t + 0.2, 2)
                out.append([t, *out[-1][1:]])
        return out

    tracks, items = [], []
    if pk.get("zone"):
        tracks.append({"label": pk["label"], "boxes": conv(samples_cls(d, {pk["zone"]}, c0, c1))})
    else:
        ps = samples_people(d, pk["tracks"], c0, c1, pk.get("maxh"))
        for k in pk["tracks"]:
            if ps[k]:
                bx = conv(ps[k])
                if pk.get("inset"):
                    for b in bx:
                        w = b[3] - b[1]
                        b[1], b[3] = round(b[1] + w * pk["inset"], 1), round(b[3] - w * pk["inset"], 1)
                tracks.append({"label": pk["label"], "boxes": bx})
                if pk.get("items"):
                    its = samples_items(d, set(pk["items"]), ps[k], c0, c1)
                    if its:
                        items.append({"boxes": conv(its)})
    vehicle = None
    raw_v = None
    if pk.get("vehicle"):
        raw_v = samples_cls(d, {"forklift"}, c0, c1, 0.5)
        vehicle = {"label": "ПОГРУЗЧИК", "boxes": conv(raw_v)}
    metric = None
    if pk["clip"] in CAMS and not pk.get("zone"):
        raw_p = samples_people(d, pk["tracks"][:1], c0, c1)[pk["tracks"][0]]
        ms = metric_series(pk, raw_p, raw_v, CAMS[pk["clip"]]["floor"])
        metric = {"unit": "м" if raw_v is not None else "м/с", "values": [[loc(t), v] for t, v in ms]}
    # кадры камеры под окно шота (с замедлением — растянутые)
    fdir = FRAMES / pk["shot"]
    if fdir.exists():
        for f in fdir.glob("*.jpg"):
            f.unlink()
    fdir.mkdir(parents=True, exist_ok=True)
    vf = (f"setpts={1 / sp_:.4f}*PTS," if sp_ != 1.0 else "") + "fps=25,scale=1280:720"
    subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-y", "-ss", f"{win0:.3f}", "-t", f"{(dur + 0.3) * sp_:.3f}",
                    "-i", str(CLIPS / f"{pk['clip']}.mp4"), "-vf", vf, "-q:v", "3", str(fdir / "f%05d.jpg")], check=True)
    nfr = len(list(fdir.glob("*.jpg")))
    # карточки бота: кадр тревоги с рамками нарушителей
    snap = frame_img(pk["clip"], opened)
    dr = ImageDraw.Draw(snap)
    for tr in tracks + ([vehicle] if vehicle else []):
        if tr["boxes"]:
            b = min(tr["boxes"], key=lambda x: abs(x[0] - t_open))
            dr.rectangle(b[1:], outline=(229, 72, 77) if tr is not vehicle else (127, 208, 232), width=5)
    spath = CARDS / f"{pk['shot']}_snap.jpg"
    snap.save(spath, quality=92)
    created = SHIFT0 + pk["min"] * 60 + pk["sec"]
    sev = pk["sev"]
    alarm = {"title": pk["title"], "severity": sev, "camera_label": pk["cam"].split(" · ")[0],
             "camera_title": pk["cam"].split(" · ", 1)[-1].capitalize(), "created": created, "deadline": created + 30 * 60,
             "status": "open", "event_id": f"ev{n + 1:03d}"}
    c_open = CARDS / f"{pk['shot']}_open.jpg"
    # карточка смотрит на часы: в кадре — момент тревоги, а не реальное «сейчас» (иначе «срок прошёл»)
    cards_mod.time.time = lambda c=created: c + 20
    c_open.write_bytes(alarm_card(spath, alarm))
    c_res = CARDS / f"{pk['shot']}_res.jpg"
    c_res.write_bytes(alarm_card(spath, dict(alarm, status="resolved", ack_by="Начальник смены", resolved_by="Начальник смены",
                                             resolved_at=created + pk["res"] * 60)))
    images[pk["shot"] + "_open"] = str(c_open)
    images[pk["shot"] + "_res"] = str(c_res)
    events.append({
        "shot": pk["shot"], "type": pk["type"], "title": pk["title"], "sub": pk["sub"], "sev": sev,
        "cam": pk["cam"], "place": pk["place"], "min": pk["min"], "sec": pk["sec"], "ackMin": pk["ack"], "resMin": pk["res"],
        "plate": pk.get("plate", "paper"), "short2": pk["short"],
        "tStart": round(t_start, 2), "tOpen": round(t_open, 2), "tAck": round(t_ack, 2), "tRes": round(t_res, 2),
        "confirm": CONFIRM, "hold": hold, "tracks": tracks, "items": items, "vehicle": vehicle, "metric": metric,
        "card": pk["shot"] + "_open", "cardRes": pk["shot"] + "_res",
    })
    shots.append({"id": pk["shot"], "file": "10-violations.js", "dur": dur, "mode": "schematic" if pk.get("plate") == "navy" else "illustrated",
                  "title": pk["title"], "transitionIn": {"kind": "fade", "dur": 0.25},
                  "video": {"n": nfr, "fps": FPS}, "brief": f"{pk['title']}: {pk['clip']} {win0:.2f}+{dur}"})
    print(f"{pk['shot']:14s} win {win0:6.2f}+{dur:.1f}  start {t_start:.2f} open {t_open:.2f}  tracks {len(tracks)} items {len(items)} frames {nfr} sev {sev}")

ack = sum(e["ackMin"] for e in events) / len(events)
res = sum(e["resMin"] for e in events) / len(events)


def mmss(m):
    s = round(m * 60)
    return f"{s // 60} мин {s % 60:02d} с" if s % 60 else f"{s // 60} мин"


types = {pk["type"] for pk in PICKS} | {"no_vest"}
stats = {"total": len(events), "critical": sum(e["sev"] == "critical" for e in events), "resolved": len(events),
         "types": len(types), "avgAck": mmss(ack).replace(" мин ", ":").replace(" с", "").replace(" мин", ":00"), "avgRes": mmss(res)}
shift = {"fontsDir": str(repo / "assets" / "fonts"), "framesDir": str(FRAMES), "images": images, "events": events, "stats": stats}
(proj / "src" / "photos.js").write_text("// сгенерировано shift_data.py из настоящего прогона конвейера — не править руками\nFILM.SHIFT = "
                                         + json.dumps(shift, ensure_ascii=False) + ";\n")

# таймлайн: открытие, нарушения, итог, финал
t = 0.0
tl_shots = [{"id": "open", "file": "01-open.js", "start": 0, "end": 6, "mode": "schematic", "title": "Смена 24.10"}]
t = 6.0
cues = [{"t": 0, "kind": "open"}]
for s in shots:
    s["start"], s["end"] = t, t + s.pop("dur")
    cues.append({"t": s["start"], "kind": "cut"})
    ev = next(e for e in events if e["shot"] == s["id"])
    cues.append({"t": round(s["start"] + ev["tOpen"], 3), "kind": "alarm", "sev": ev["sev"]})
    cues.append({"t": round(s["start"] + ev["tRes"], 3), "kind": "resolve"})
    tl_shots.append(s)
    t = s["end"]
tl_shots.append({"id": "summary", "file": "90-summary.js", "start": t, "end": t + 8, "mode": "illustrated", "title": "Итог смены", "transitionIn": {"kind": "fade", "dur": 0.4}})
cues.append({"t": t, "kind": "summary"})
t += 8
tl_shots.append({"id": "close", "file": "99-close.js", "start": t, "end": t + 5, "mode": "schematic", "title": "Финал", "transitionIn": {"kind": "fade", "dur": 0.4}})
cues.append({"t": t, "kind": "close"})
t += 5
for sh in tl_shots:
    sh["post"] = 0.3   # зерно плиты слабее: иначе кодек тонет в шуме
tl = {"title": "LinQ Guard — смена", "bpm": 120, "fps": 24, "width": 1920, "height": 1080, "safeBottom": 1080,
      "duration": t, "shots": tl_shots, "cues": cues}
(proj / "src" / "timeline.js").write_text("// сгенерировано shift_data.py\nFILM.TIMELINE = " + json.dumps(tl, ensure_ascii=False, indent=1) + ";\n")
print("duration", t, "events", len(events), stats)
