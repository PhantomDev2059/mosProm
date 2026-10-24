"""Лист проверки срабатываний: для каждого события — кадры при начале, открытии и через 1,5 с,
с рамкой трека события (люди — рамка человека, огонь/дым — рамка события).
python review.py dets.json clips_dir out.jpg [clip ...]"""
import json
import sys
from pathlib import Path

import cv2
from PIL import Image, ImageDraw

dets = json.loads(Path(sys.argv[1]).read_text())
clips_dir, out = Path(sys.argv[2]), sys.argv[3]
only = set(sys.argv[4:])
cells = []


def frame_at(clip, t):
    cap = cv2.VideoCapture(str(clips_dir / f"{clip}.mp4"))
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, round(t * 25) - 1))
    ok, fr = cap.read()
    return Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)) if ok else None


def boxes_at(d, t, ev):
    fr = min(d["frames"], key=lambda f: abs(f["t"] - t))
    out = []
    if ev["track"]:
        out += [("p", p["box"]) for p in fr["people"] if p["id"] == ev["track"]]
    out += [("a", a["box"]) for a in fr["active"] if a["type"] == ev["type"] and a["track"] == ev["track"]]
    out += [("i", i["box"]) for i in fr["items"] if i["cls"] in ("phone", "cigarette", "fire", "smoke", "forklift", "vehicle", "head")]
    return out


for clip, d in dets.items():
    if only and clip not in only:
        continue
    for ev in d["events"]:
        for k, t in enumerate((ev["start"], ev["opened"], ev["opened"] + 1.5)):
            im = frame_at(clip, t)
            if im is None:
                continue
            dr = ImageDraw.Draw(im)
            for kind, b in boxes_at(d, t, ev):
                col = {"p": (255, 60, 60), "a": (255, 200, 0), "i": (0, 200, 255)}[kind]
                dr.rectangle(b, outline=col, width=4 if kind != "i" else 2)
            dr.rectangle([0, 0, 1280, 40], fill=(0, 0, 0))
            dr.text((10, 8), f"{clip} {ev['type']} tr{ev['track']} {['start', 'open', '+1.5'][k]} t={t:.1f}", fill=(255, 255, 255))
            cells.append(im.resize((426, 240)))
cols = 3
rows = (len(cells) + cols - 1) // cols
sheet = Image.new("RGB", (cols * 430, rows * 244), (30, 30, 30))
for i, c in enumerate(cells):
    sheet.paste(c, ((i % cols) * 430, (i // cols) * 244))
sheet.save(out, quality=85)
print(out, len(cells))
