"""Пре- и постпроцессинг YOLO на numpy.

Один и тот же код работает с выходом ONNX на GPU/CPU и с выходом хвоста модели
после TPU LinQ: в обоих случаях на вход приходит output0 формы (1, C, N).
"""
import numpy as np

from .types import PERSON, Detection

PAD_VALUE = 114


def letterbox(img: np.ndarray, size: int = 640) -> tuple[np.ndarray, float, tuple[float, float]]:
    """BGR-кадр → тензор (1, 3, size, size) float32 в [0, 1], масштаб и отступы."""
    import cv2

    h, w = img.shape[:2]
    r = min(size / h, size / w)
    nw, nh = round(w * r), round(h * r)
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    pad_x, pad_y = (size - nw) / 2, (size - nh) / 2
    top, left = round(pad_y - 0.1), round(pad_x - 0.1)
    canvas = np.full((size, size, 3), PAD_VALUE, np.uint8)
    canvas[top:top + nh, left:left + nw] = resized
    # BGR→RGB, HWC→NCHW и /255 одним вызовом на C++
    tensor = cv2.dnn.blobFromImage(canvas, scalefactor=1 / 255.0, swapRB=True)
    return tensor, r, (left, top)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_thr: float) -> list[int]:
    order = scores.argsort()[::-1]
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    keep = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_thr]
    return keep


def decode(
    output0: np.ndarray,
    kind: str,
    names: dict[int, str],
    ratio: float,
    pad: tuple[float, float],
    frame_shape: tuple[int, int],
    conf: float = 0.25,
    iou: float = 0.45,
    max_det: int = 100,
) -> list[Detection]:
    """output0 YOLOv8/YOLO11 (detect или pose) → детекции в координатах исходного кадра.

    names — отображение индекса класса модели в канонический класс; классы без отображения отбрасываются.
    """
    out = output0[0].T                       # формат Ultralytics (C, N) → (N, C)
    if kind == "pose":
        scores = out[:, 4]
        cls = np.zeros(len(out), np.int64)
        kpts = out[:, 5:].reshape(len(out), -1, 3)
    else:
        cls_scores = out[:, 4:]
        cls = cls_scores.argmax(1)
        scores = cls_scores[np.arange(len(out)), cls]
        kpts = None

    mask = scores >= conf
    if not mask.any():
        return []
    out, scores, cls = out[mask], scores[mask], cls[mask]
    if kpts is not None:
        kpts = kpts[mask]

    xy, wh = out[:, :2], out[:, 2:4]
    boxes = np.concatenate([xy - wh / 2, xy + wh / 2], 1)
    # NMS по классам: сдвигаем рамки разных классов далеко друг от друга
    keep = nms(boxes + cls[:, None] * 4096.0, scores, iou)[:max_det]

    h, w = frame_shape
    dets = []
    for i in keep:
        label = names.get(int(cls[i])) if kind != "pose" else PERSON
        if label is None:
            continue
        b = boxes[i].copy()
        b[[0, 2]] = (b[[0, 2]] - pad[0]) / ratio
        b[[1, 3]] = (b[[1, 3]] - pad[1]) / ratio
        b[[0, 2]] = b[[0, 2]].clip(0, w)
        b[[1, 3]] = b[[1, 3]].clip(0, h)
        k = None
        if kpts is not None:
            k = kpts[i].copy()
            k[:, 0] = (k[:, 0] - pad[0]) / ratio
            k[:, 1] = (k[:, 1] - pad[1]) / ratio
        dets.append(Detection(box=b.astype(np.float32), score=float(scores[i]), cls=label, keypoints=k))
    return dets
