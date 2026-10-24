from datetime import datetime

import numpy as np

from engine.config import parse_camera
from engine.events import EventManager
from engine.pipeline import CameraPipeline
from engine.postprocess import decode
from engine.rules import Signal
from engine.scene import Schedule
from engine.types import Detection

FPS = 5


def person(x=100.0, y=100.0, w=60.0, h=160.0, kpts=None):
    return Detection(box=np.array([x, y, x + w, y + h], np.float32), score=0.9, cls="person", keypoints=kpts)


def run_signal(mgr, pattern, track_id=1, confirm_s=2.0, box_x=100.0):
    for i, on in enumerate(pattern):
        now = i / FPS
        mgr.observe(now, "z", "no_helmet", "Нет каски", "high", confirm_s, person(box_x), track_id,
                    Signal(0.8) if on else None)
        mgr.tick(now, lambda tid: 0.0)


def test_continuous_violation_gives_one_event():
    opened = []
    mgr = EventManager("cam", on_open=lambda e, d: opened.append(e))
    run_signal(mgr, [True] * (60 * FPS))
    assert len(opened) == 1
    assert opened[0].confirmed_at - opened[0].started_at >= 2.0


def test_single_frames_do_not_alarm():
    opened = []
    mgr = EventManager("cam", on_open=lambda e, d: opened.append(e))
    run_signal(mgr, ([True] + [False] * 14) * 20)
    assert opened == []


def test_track_id_switch_is_merged():
    opened = []
    mgr = EventManager("cam", on_open=lambda e, d: opened.append(e))
    for i in range(40 * FPS):
        now = i / FPS
        tid = 1 if i < 20 * FPS else 2                       # трекер потерял человека и выдал новый ID
        mgr.observe(now, "z", "no_helmet", "Нет каски", "high", 2.0, person(), tid, Signal(0.8))
        mgr.tick(now, lambda t, tid=tid: 0.0 if t == tid else 10.0)
    assert len(opened) == 1


def test_new_pass_does_not_stretch_old_event():
    """Демо-файл по кругу: событие прошлого прохода закрывается и не приклеивает к себе повтор через минуту."""
    opened, closed = [], []
    mgr = EventManager("cam", on_open=lambda e, d: opened.append(e), on_close=closed.append)
    run_signal(mgr, [True] * (10 * FPS))
    mgr.close_all()
    assert len(closed) == 1 and closed[0].duration_s <= 10
    run_signal(mgr, [True] * (10 * FPS))
    assert len(opened) == 2 and opened[1] is not opened[0]


def test_schedule_overnight():
    s = Schedule.parse("22:00-06:00")
    assert s.active(datetime(2026, 10, 23, 23, 30))
    assert not s.active(datetime(2026, 10, 23, 12, 0))
    assert Schedule.parse("07:55-12:00").active(datetime(2026, 10, 24, 8, 0))


def test_decode_detect_output():
    out = np.zeros((1, 4 + 2, 3), np.float32)               # 2 класса, 3 кандидата, формат (1, C, N)
    out[0, :4, 0] = [320, 320, 100, 200]
    out[0, 4, 0] = 0.9                                       # класс 0
    out[0, :4, 1] = [322, 322, 100, 200]                     # дубль первого — уйдёт в NMS
    out[0, 4, 1] = 0.8
    out[0, :4, 2] = [100, 100, 40, 40]
    out[0, 5, 2] = 0.7                                       # класс 1
    dets = decode(out, "detect", {0: "helmet", 1: "vest"}, ratio=0.5, pad=(0, 80), frame_shape=(960, 1280))
    assert sorted(d.cls for d in dets) == ["helmet", "vest"]
    helmet = next(d for d in dets if d.cls == "helmet")
    assert np.allclose(helmet.box, [540, 280, 740, 680])


def test_zone_rule_with_schedule_end_to_end():
    cfg = parse_camera({
        "camera": "cam-test", "source": "x.mp4",
        "zones": [{"id": "racks", "polygon": [[0, 0], [400, 0], [400, 400], [0, 400]],
                   "rules": [{"type": "zone_intrusion", "confirm_s": 1.0, "schedule": "07:55-12:00"}]}],
    })
    opened = []
    pipe = CameraPipeline(cfg, [], EventManager("cam-test", on_open=lambda e, d: opened.append(e)))
    for i in range(5 * FPS):
        pipe.step([person(100, 100)], i / FPS, wall=datetime(2026, 10, 24, 13, 0))   # правило не действует
    assert opened == []
    for i in range(5 * FPS, 10 * FPS):
        pipe.step([person(100, 100)], i / FPS, wall=datetime(2026, 10, 24, 9, 0))
    assert len(opened) == 1 and opened[0].type == "zone_intrusion"


def test_fire_is_one_event_per_zone():
    cfg = parse_camera({"camera": "cam-fire", "source": "x.mp4", "rules": [{"type": "open_fire", "confirm_s": 1.0}]})
    opened = []
    pipe = CameraPipeline(cfg, [], EventManager("cam-fire", on_open=lambda e, d: opened.append(e)))
    rng = np.random.default_rng(0)
    for i in range(30 * FPS):                     # пламя «прыгает»: каждый кадр новая рамка в другом месте
        x, y = rng.uniform(0, 800, 2)
        fire = Detection(np.array([x, y, x + 60, y + 80], np.float32), 0.8, "fire")
        pipe.step([fire], i / FPS)
    assert len(opened) == 1


def test_vehicle_far_behind_person_is_not_proximity():
    from engine.rules import RuleContext, build_rule
    from engine.scene import Scene, Zone
    from engine.types import Track
    rule = build_rule("vehicle_proximity", {})
    p = person(400, 300, 80, 240)                                               # ноги на y=540
    far = Detection(np.array([380, 250, 560, 380], np.float32), 0.9, "forklift")   # низ на y=380 — далеко в глубине
    near = Detection(np.array([500, 360, 700, 545], np.float32), 0.9, "forklift")  # низ на y=545, рядом сбоку
    tp = Track(1, "person", p, 0, 0)
    zone = Zone("frame", "Весь кадр", None)
    scene = Scene("c", [zone])
    ctx_far = RuleContext(scene, zone, 0, [tp, Track(2, "forklift", far, 0, 0)])
    ctx_near = RuleContext(scene, zone, 0, [tp, Track(3, "forklift", near, 0, 0)])
    assert rule.evaluate(tp, ctx_far) is None
    assert rule.evaluate(tp, ctx_near) is not None
