"""Сверка: обрезанная модель + app/yolo.postprocess против полной ONNX-модели (output0 [1, 84, 8400]).

    .venv\\Scripts\\python compile\\check_postprocess.py models\\yolov8n_640
"""
import glob
import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'app'))
from yolo import _nms, gather_levels, letterbox, postprocess  # noqa: E402

base = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / 'models' / 'yolov8n_640')
meta = json.loads(base.with_suffix('.json').read_text())
full = ort.InferenceSession(str(base.with_suffix('.onnx')), providers=['CPUExecutionProvider'])
cut = ort.InferenceSession(str(base.parent / f'{base.name}_cut.onnx'), providers=['CPUExecutionProvider'])


def reference(tensor, lb, size, conf=0.25, iou=0.45):
    out = full.run(None, {meta['input']: tensor})[0][0]  # [84, 8400]: xywh + 80 вероятностей
    scores, cls = out[4:].max(0), out[4:].argmax(0)
    keep = scores > conf
    xywh, scores, cls = out[:4, keep].T, scores[keep], cls[keep]
    boxes = np.concatenate([xywh[:, :2] - xywh[:, 2:] / 2, xywh[:, :2] + xywh[:, 2:] / 2], 1)
    idx = _nms(boxes + cls[:, None] * 4096.0, scores, iou)
    scale, px, py = lb
    boxes = boxes[idx]
    boxes[:, [0, 2]] = np.clip((boxes[:, [0, 2]] - px) / scale, 0, size[0])
    boxes[:, [1, 3]] = np.clip((boxes[:, [1, 3]] - py) / scale, 0, size[1])
    return sorted(zip(cls[idx].tolist(), scores[idx].round(3).tolist(), boxes.round(1).tolist()), key=lambda d: -d[1])


worst = 0.0
for path in sorted(glob.glob(str(ROOT / 'models/calib/**/*.jpg'), recursive=True))[:20]:
    image = Image.open(path)
    tensor, lb = letterbox(image, meta['imgsz'])
    outs = cut.run(meta['outputs'], {meta['input']: tensor})
    ours = postprocess(gather_levels(meta, dict(zip(meta['outputs'], outs))), meta['strides'], lb, image.size)
    ref = reference(tensor, lb, image.size)
    assert len(ours) == len(ref), f'{path}: {len(ours)} vs {len(ref)} детекций'
    for d, (c, s, b) in zip(sorted(ours, key=lambda d: -d['conf']), ref):
        assert d['cls'] == c, (path, d, c)
        worst = max(worst, abs(d['conf'] - s), float(np.abs(np.array(d['box']) - b).max()))
print(f'OK: 20 картинок, детекции совпадают, макс. расхождение {worst:.3f}')
