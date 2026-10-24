"""Замеры для правил «бег» и «опасное сближение»: по кадрам анализа (5 к/с) для каждого человека —
скорость ног в ростах/с, скорость приближения к камере по росту рамки, подъём колен; для пар человек–техника —
зазор в ростах, разница глубины по нижней кромке, скорость техники, водитель ли это.
python probe_rules.py <repo> <registry.json> <out.json> clip.mp4 [...]
"""
import json
import math
import sys
from pathlib import Path

repo, registry, out = sys.argv[1:4]
clips = sys.argv[4:]
sys.path.insert(0, repo)
import cv2  # noqa: E402
import numpy as np  # noqa: E402

from api.workers import load_detectors  # noqa: E402
from engine.config import parse_camera  # noqa: E402
from engine.events import EventManager  # noqa: E402
from engine.geometry import ioa  # noqa: E402
from engine.pipeline import CameraPipeline  # noqa: E402
from engine.scene import Scene  # noqa: E402
from engine.types import L_ANKLE, L_HIP, L_KNEE, R_ANKLE, R_HIP, R_KNEE  # noqa: E402

VEH = {"forklift", "vehicle", "machinery"}
dets = load_detectors(Path(registry))
res_all = {}
for clip in clips:
    name = Path(clip).stem
    cap = cv2.VideoCapture(clip)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    H = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    cfg = parse_camera({"camera": name, "source": clip, "analysis_fps": 5,
                        "rules": [{"type": "running"}, {"type": "vehicle_proximity"}]})
    opened = []
    state = {"now": 0.0}
    em = EventManager(cfg.id, on_open=lambda ev, d, o=opened, s=state: o.append((ev.type, round(s["now"], 1), ev.track_id)))
    pipe = CameraPipeline(cfg, [dets[m] for m in ("pose", "forklift", "coco")], em)
    rows, idx = [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        idx += 1
        if idx % 5:
            continue
        now = idx / fps
        state["now"] = now
        r = pipe.process(frame, now)
        people = [t for t in r.tracks if t.cls == "person"]
        vehs = [t for t in r.tracks if t.cls in VEH and t.last_seen == now]
        for p in people:
            if p.age < 0.6:
                continue
            vh = Scene.speed_in_heights(p, now)
            pts = p.window(now, 1.0)
            approach = None
            if len(pts) >= 2 and pts[-1][0] - pts[0][0] >= 0.5:
                (t0, d0), (t1, d1) = pts[0], pts[-1]
                if d0.height > 0 and d1.height > 0:
                    # скорость по глубине в ростах/с: d(ln h)/dt × (f/h), f ≈ высота кадра
                    approach = abs(math.log(d1.height / d0.height)) / (t1 - t0) * (H / ((d0.height + d1.height) / 2))
            # походка: разнос колен и стоп по вертикали в ростах (у бега одна нога высоко поднята)
            g = {}
            d = p.det
            h = max(d.height, 1.0)
            for nm, a, b in (("knee", L_KNEE, R_KNEE), ("ankle", L_ANKLE, R_ANKLE)):
                ka, kb = d.keypoint(a), d.keypoint(b)
                if ka is not None and kb is not None:
                    g[nm] = round(float(abs(ka[1] - kb[1]) / h), 3)
            la, ra, lh, rh = d.keypoint(L_ANKLE), d.keypoint(R_ANKLE), d.keypoint(L_HIP), d.keypoint(R_HIP)
            if la is not None and ra is not None and lh is not None and rh is not None:
                g["stride"] = round(float(abs(la[0] - ra[0]) / h), 3)
            row = {"t": round(now, 2), "id": p.id, "h": round(float(p.det.height)), "vh": vh and round(float(vh), 2), **g,
                   "app": approach and round(float(approach), 2), "bottom": round(float(p.det.box[3]))}
            pairs = []
            for v in vehs:
                drv = ioa(p.det.box, v.det.box) > 0.6
                h = max(p.det.height, 1.0)
                gap = float(max(0.0, max(p.det.box[0], v.det.box[0]) - min(p.det.box[2], v.det.box[2])) / h)
                pairs.append({"cls": v.cls, "gap": round(gap, 2), "depth": round(abs(p.det.box[3] - v.det.box[3]) / h, 2),
                              "vspeed": round(Scene.speed_in_heights(v, now) or 0.0, 2), "driver": drv})
            if pairs:
                row["pairs"] = pairs
            rows.append(row)
    em.tick(state["now"] + 3600, lambda tid: 1e9)
    res_all[name] = {"events": opened, "rows": rows}
    vmax = max((r["vh"] or 0 for r in rows), default=0)
    amax = max((r["app"] or 0 for r in rows), default=0)
    gmin = min((pp["gap"] for r in rows for pp in r.get("pairs", []) if not pp["driver"] and pp["depth"] <= 0.25), default=None)
    kmax = max((r.get("knee", 0) for r in rows), default=0)
    amx = max((r.get("ankle", 0) for r in rows), default=0)
    print(f"{name}: events {opened}  max vh {vmax}  max approach {amax}  min gap(same depth) {gmin}  max knee {kmax}  max ankle {amx}", flush=True)
Path(out).write_text(json.dumps(res_all, default=float))
