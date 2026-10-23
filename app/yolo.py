"""Пре- и постпроцессинг YOLOv8 только на numpy + PIL.

Используется в трёх местах: калибровка при компиляции (WSL), relay на сервере Jupyter
(там нет OpenCV) и локальный CPU-режим сайта. Модель отрезана на последних свёртках головы:
на каждом масштабе box [1, 64, H, W] (4x16 бинов DFL) и cls [1, nc, H, W] (логиты классов).
"""
import numpy as np
from PIL import Image

COCO_NAMES = [
    'person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus', 'train', 'truck', 'boat',
    'traffic light', 'fire hydrant', 'stop sign', 'parking meter', 'bench', 'bird', 'cat', 'dog',
    'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe', 'backpack', 'umbrella',
    'handbag', 'tie', 'suitcase', 'frisbee', 'skis', 'snowboard', 'sports ball', 'kite',
    'baseball bat', 'baseball glove', 'skateboard', 'surfboard', 'tennis racket', 'bottle',
    'wine glass', 'cup', 'fork', 'knife', 'spoon', 'bowl', 'banana', 'apple', 'sandwich', 'orange',
    'broccoli', 'carrot', 'hot dog', 'pizza', 'donut', 'cake', 'chair', 'couch', 'potted plant',
    'bed', 'dining table', 'toilet', 'tv', 'laptop', 'mouse', 'remote', 'keyboard', 'cell phone',
    'microwave', 'oven', 'toaster', 'sink', 'refrigerator', 'book', 'clock', 'vase', 'scissors',
    'teddy bear', 'hair drier', 'toothbrush',
]


def letterbox(image, size=640):
    """PIL-картинка -> тензор [1, 3, size, size] float32 в [0, 1] и (scale, pad_x, pad_y)."""
    image = image.convert('RGB')
    w, h = image.size
    scale = min(size / w, size / h)
    nw, nh = round(w * scale), round(h * scale)
    pad_x, pad_y = (size - nw) // 2, (size - nh) // 2
    canvas = Image.new('RGB', (size, size), (114, 114, 114))
    canvas.paste(image.resize((nw, nh), Image.BILINEAR), (pad_x, pad_y))
    tensor = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    return np.ascontiguousarray(tensor), (scale, pad_x, pad_y)


def gather_levels(meta, tensors):
    """{имя выхода: массив} -> список [1, 64 + nc, H, W] по уровням, как ждёт postprocess."""
    return [np.concatenate([tensors[box], tensors[cls]], axis=1) for box, cls in meta['levels']]


def gather_kpts(meta, tensors):
    """Для pose-модели: список [1, 17*3, H, W] по уровням, иначе None."""
    return [tensors[name] for name in meta['kpt_levels']] if 'kpt_levels' in meta else None


# пары точек COCO-скелета: 0 нос, 1-2 глаза, 3-4 уши, 5-6 плечи, 7-8 локти, 9-10 кисти,
# 11-12 бёдра, 13-14 колени, 15-16 стопы
SKELETON = [(15, 13), (13, 11), (16, 14), (14, 12), (11, 12), (5, 11), (6, 12), (5, 6), (5, 7),
            (6, 8), (7, 9), (8, 10), (1, 2), (0, 1), (0, 2), (1, 3), (2, 4), (3, 5), (4, 6)]


def merge(results):
    """Детекции нескольких моделей в один список: людей берём из pose-модели (у них есть скелет),
    остальные классы — из детектора, чтобы не было двойных рамок на человеке."""
    pose = [d for dets in results for d in dets if 'kpts' in d]
    if not pose:
        return [d for dets in results for d in dets]
    return pose + [d for dets in results for d in dets if 'kpts' not in d and d['name'] != 'person']


def _nms(boxes, scores, iou):
    order = scores.argsort()[::-1]
    keep = []
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while order.size:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        overlap = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][overlap <= iou]
    return keep


def postprocess(outputs, strides, letterbox_info, orig_size, conf=0.25, iou=0.45,
                names=COCO_NAMES, max_det=300, kpts=None):
    """outputs: список [1, 64 + nc, H, W] по возрастанию stride -> список детекций в координатах оригинала.

    kpts (pose): список [1, K*3, H, W]; точка = ((raw_xy * 2 + номер ячейки) * stride, sigmoid(raw_v))."""
    scale, pad_x, pad_y = letterbox_info
    all_boxes, all_scores, all_cls, all_kpts = [], [], [], []
    for level, (out, stride) in enumerate(zip(outputs, strides)):
        _, c, h, w = out.shape
        out = out.reshape(c, h * w)
        logits = out[64:]
        best = logits.argmax(0)
        best_logit = logits[best, np.arange(h * w)]
        score = 1.0 / (1.0 + np.exp(-best_logit))
        idx = np.nonzero(score > conf)[0]
        if not idx.size:
            continue
        # DFL: softmax по 16 бинам на каждую сторону, матожидание = расстояние в шагах сетки
        dfl = out[:64, idx].reshape(4, 16, -1)
        dfl = np.exp(dfl - dfl.max(1, keepdims=True))
        dist = (dfl / dfl.sum(1, keepdims=True) * np.arange(16)[None, :, None]).sum(1)
        cx = (idx % w + 0.5)
        cy = (idx // w + 0.5)
        boxes = np.stack([cx - dist[0], cy - dist[1], cx + dist[2], cy + dist[3]], 1) * stride
        all_boxes.append(boxes)
        all_scores.append(score[idx])
        all_cls.append(best[idx])
        if kpts is not None:
            k = kpts[level].reshape(-1, 3, h * w)[:, :, idx]          # [K, 3, n]
            kx = (k[:, 0] * 2.0 + (idx % w)) * stride
            ky = (k[:, 1] * 2.0 + (idx // w)) * stride
            kv = 1.0 / (1.0 + np.exp(-k[:, 2]))
            all_kpts.append(np.stack([kx, ky, kv], -1).transpose(1, 0, 2))  # [n, K, 3]
    if not all_boxes:
        return []
    boxes, scores, cls = np.concatenate(all_boxes), np.concatenate(all_scores), np.concatenate(all_cls)
    # NMS по классам: сдвигаем боксы разных классов, чтобы они не пересекались
    keep = _nms(boxes + cls[:, None] * 4096.0, scores, iou)[:max_det]
    ow, oh = orig_size
    boxes = boxes[keep]
    boxes[:, [0, 2]] = np.clip((boxes[:, [0, 2]] - pad_x) / scale, 0, ow)
    boxes[:, [1, 3]] = np.clip((boxes[:, [1, 3]] - pad_y) / scale, 0, oh)
    dets = [{'cls': int(c), 'name': names[c] if c < len(names) else str(c), 'conf': round(float(s), 3),
             'box': [round(float(v), 1) for v in b]}
            for b, s, c in zip(boxes, scores[keep], cls[keep])]
    if kpts is not None:
        pts = np.concatenate(all_kpts)[keep]
        pts[:, :, 0] = (pts[:, :, 0] - pad_x) / scale
        pts[:, :, 1] = (pts[:, :, 1] - pad_y) / scale
        for d, p in zip(dets, pts):
            d['kpts'] = [[round(float(x), 1), round(float(y), 1), round(float(v), 2)] for x, y, v in p]
    return dets
