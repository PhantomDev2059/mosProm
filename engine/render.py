"""Отрисовка кадра для оператора: зоны, люди со скелетом, СИЗ, техника, активные нарушения."""
import cv2
import numpy as np

from .pipeline import FrameResult
from .scene import Scene

# BGR
RED, AMBER, GREEN, BLUE, WHITE, GREY = (40, 40, 220), (0, 170, 255), (80, 190, 60), (220, 140, 30), (255, 255, 255), (170, 170, 170)
ITEM_COLORS = {
    "helmet": GREEN, "vest": GREEN, "no_helmet": RED, "head": RED, "no_vest": RED,
    "forklift": BLUE, "vehicle": BLUE, "machinery": BLUE, "phone": AMBER, "cigarette": AMBER,
    "fire": RED, "smoke": GREY,
}
SKELETON = [(5, 7), (7, 9), (6, 8), (8, 10), (5, 6), (5, 11), (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16)]


def _label(img, text, x, y, color):
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    y = max(y, th + 6)
    cv2.rectangle(img, (x, y - th - 6), (x + tw + 6, y), color, -1)
    cv2.putText(img, text, (x + 3, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1, cv2.LINE_AA)


def draw(frame: np.ndarray, scene: Scene, res: FrameResult, highlight_track: int | None = None) -> np.ndarray:
    img = frame.copy()
    overlay = img.copy()
    for z in scene.zones:
        if z.polygon is None:
            continue
        pts = z.polygon.astype(np.int32)
        cv2.fillPoly(overlay, [pts], AMBER)
        cv2.polylines(img, [pts], True, AMBER, 2, cv2.LINE_AA)
    img = cv2.addWeighted(overlay, 0.18, img, 0.82, 0)

    by_track = {}
    for e in res.active:
        by_track.setdefault(e.track_id, []).append(e)

    for tr in res.tracks:
        d = tr.det
        x1, y1, x2, y2 = d.box.astype(int)
        if tr.cls == "person":
            bad = tr.id in res.violators or tr.id == highlight_track
            color = RED if bad else GREEN
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 3 if bad else 2)
            if d.keypoints is not None:
                for a, b in SKELETON:
                    pa, pb = d.keypoint(a), d.keypoint(b)
                    if pa is not None and pb is not None:
                        cv2.line(img, tuple(pa.astype(int)), tuple(pb.astype(int)), color, 2, cv2.LINE_AA)
            text = f"#{tr.id}"
            if tr.id in by_track:
                text += " " + ", ".join(_ascii(e.type) for e in by_track[tr.id])
            _label(img, text, x1, y1 - 2, color)
        else:
            color = ITEM_COLORS.get(tr.cls, GREY)
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            _label(img, f"{tr.cls} {d.score:.2f}", x1, y1 - 2, color)
        for e in by_track.get(tr.id, []) if tr.cls != "person" else []:
            _label(img, _ascii(e.type), x1, y2 + 16, RED)
    return img


def _ascii(rule_type: str) -> str:
    # OpenCV не рисует кириллицу; на кадре короткие латинские метки, по-русски — в интерфейсе
    return rule_type.replace("_", " ").upper()
