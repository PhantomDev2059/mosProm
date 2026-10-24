"""Прогон настоящего конвейера LinQ Guard по роликам для фильма «Смена»: как в лайве — 5 кадров/с анализа,
свежий конвейер на каждый ролик. Пишет события и покадрово (по кадрам анализа) рамки активных нарушений
и предметов рядом с нарушителем — фильм рисует только их.
python detect.py <repo> <registry.json> <out.json> clip1.mp4 [clip2.mp4 ...]
"""
import json
import sys
from pathlib import Path

repo, registry, out = sys.argv[1:4]
clips = sys.argv[4:]
sys.path.insert(0, repo)
import cv2  # noqa: E402

from api.workers import load_detectors  # noqa: E402
from engine.config import parse_camera  # noqa: E402
from engine.events import EventManager  # noqa: E402
from engine.pipeline import CameraPipeline  # noqa: E402

RULES = ("fall", "immobile", "no_helmet", "no_vest", "vehicle_proximity", "running", "phone", "smoking", "open_fire", "smoke")
MODELS = ["pose", "ppe2", "ppe", "forklift", "fire", "cigarette", "coco"]
ITEMS = {"phone", "cigarette", "head", "no_helmet", "no_vest", "helmet", "vest", "fire", "smoke", "forklift", "vehicle", "machinery"}
STEP = 5

dets = load_detectors(Path(registry))
CAMS = json.loads((Path(__file__).parent / "cams.json").read_text()) if (Path(__file__).parent / "cams.json").exists() else {}
result = json.loads(Path(out).read_text()) if Path(out).exists() else {}


def box(b):
    return [round(float(v), 1) for v in b]


for clip in clips:
    name = Path(clip).stem
    cap = cv2.VideoCapture(clip)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    params = CAMS.get(name, {}).get("rules", {})
    cam = {"camera": name, "source": clip, "analysis_fps": 5, "rules": [{"type": r, **params.get(r, {})} for r in RULES]}
    if name in CAMS:                       # калибровка пола камеры (cams.json), как при установке на объекте
        cam["floor"] = CAMS[name]["floor"]
    cfg = parse_camera(cam)
    state = {"now": 0.0}
    opened = []
    em = EventManager(cfg.id, on_open=lambda ev, d, o=opened, s=state: o.append((ev, s["now"])))
    pipe = CameraPipeline(cfg, [dets[m] for m in MODELS], em)
    frames, idx = [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        idx += 1
        if idx % STEP:
            continue
        state["now"] = idx / fps
        res = pipe.process(frame, state["now"])
        tracks = {t.id: t for t in res.tracks}
        act = []
        for e in res.active:
            tr = tracks.get(e.track_id)
            act.append({"type": e.type, "sev": e.severity, "track": e.track_id,
                        "box": box(tr.det.box if tr is not None and tr.cls == "person" else e.box)})
        items = [{"cls": d.cls, "box": box(d.box), "score": round(float(d.score), 2)}
                 for d in res.detections if d.cls in ITEMS]
        people = [{"id": t.id, "box": box(t.det.box),
                   "kp": [[round(float(v), 1) for v in k] for k in t.det.keypoints] if t.det.keypoints is not None else None}
                  for t in res.tracks if t.cls == "person" and t.last_seen == state["now"]]
        frames.append({"f": idx, "t": round(state["now"], 2), "active": act, "items": items, "people": people})
    em.tick(state["now"] + 3600, lambda tid: 1e9)
    events = []
    for ev, op in opened:
        end = ev.closed_at if ev.closed_at is not None else ev.last_signal
        events.append({"type": ev.type, "title": ev.title, "severity": ev.severity, "track": ev.track_id,
                       "start": round(ev.started_at, 2), "opened": round(op, 2), "end": round(end, 2),
                       "box": box(ev.box), "score": round(ev.score, 2)})
    result[name] = {"fps": fps, "w": w, "h": h, "frames_total": idx, "events": events, "frames": frames}
    print(name, [(e["type"], e["opened"], e["track"]) for e in events], flush=True)
    Path(out).write_text(json.dumps(result, ensure_ascii=False))
