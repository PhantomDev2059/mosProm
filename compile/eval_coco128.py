"""Полнота/точность детектора на coco128 (есть разметка): TPU (через relay) против CPU (float ONNX).

    .venv\\Scripts\\python compile\\eval_coco128.py yolov8n_640 yolov8s_640

Совпадение — тот же класс и IoU >= 0.5. Порог уверенности 0.25 (как на сайте), для телефона 0.08.
"""
import glob
import io
import json
import os
import sys
from collections import Counter, deque
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'app'))
sys.path.insert(0, str(ROOT / 'tools'))
from linq_remote import load_env  # noqa: E402
from tpu_client import RelayClient  # noqa: E402
from yolo import COCO_NAMES, gather_levels, letterbox, postprocess  # noqa: E402

CONF, CLASS_CONF = 0.25, {'cell phone': 0.08}
FOCUS = ['person', 'cell phone', 'car', 'truck', 'bottle', 'cup', 'book']


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter + 1e-9)


def ground_truth(img_path, size):
    lbl = Path(str(img_path).replace('images', 'labels')).with_suffix('.txt')
    w, h = size
    out = []
    for line in lbl.read_text().splitlines() if lbl.exists() else []:
        c, cx, cy, bw, bh = map(float, line.split())
        out.append((COCO_NAMES[int(c)], [(cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h]))
    return out


def score(per_image):
    tp, fn, fp = Counter(), Counter(), Counter()
    for gt, preds in per_image:
        preds = [p for p in preds if p['conf'] >= CLASS_CONF.get(p['name'], CONF)]
        used = set()
        for name, box in gt:
            best, bi = 0.0, None
            for i, p in enumerate(preds):
                if i not in used and p['name'] == name and iou(box, p['box']) > best:
                    best, bi = iou(box, p['box']), i
            if best >= 0.5:
                tp[name] += 1
                used.add(bi)
            else:
                fn[name] += 1
        for i, p in enumerate(preds):
            if i not in used:
                fp[p['name']] += 1
    total_tp, total_fn, total_fp = sum(tp.values()), sum(fn.values()), sum(fp.values())
    row = {'recall': total_tp / max(1, total_tp + total_fn), 'precision': total_tp / max(1, total_tp + total_fp)}
    for name in FOCUS:
        row[name] = (tp[name], tp[name] + fn[name])
    return row


def main():
    load_env()
    models = sys.argv[1:] or ['yolov8n_640']
    images = sorted(glob.glob(str(ROOT / 'models/calib/**/images/**/*.jpg'), recursive=True))
    relay = RelayClient(os.environ['LINQ_AMQP_HOST'], int(os.environ['LINQ_AMQP_PORT']), os.environ['LINQ_USER'])
    relay.ping(timeout=10)
    data = []
    for path in images:
        img = Image.open(path).convert('RGB')
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=92)
        data.append((path, img, buf.getvalue()))
    results = {}
    for model in models:
        meta = json.loads((ROOT / 'models' / f'{model}.json').read_text())
        # TPU: до 8 запросов в полёте
        tpu, q = [], deque()
        for i, (path, img, jpeg) in enumerate(data):
            q.append((path, img, relay.submit(jpeg, model=model, conf='0.05')))
            while len(q) >= 8 or (i == len(data) - 1 and q):
                p, im, fut = q.popleft()
                tpu.append((ground_truth(p, im.size), fut.result(60)['detections']))
        results[f'{model} TPU'] = score(tpu)
        # CPU float — для оценки потерь от квантования
        import onnxruntime as ort
        sess = ort.InferenceSession(str(ROOT / 'models' / f'{model}_cut.onnx'), providers=['CPUExecutionProvider'])
        cpu = []
        for path, img, jpeg in data:
            im = Image.open(io.BytesIO(jpeg))
            t, lb = letterbox(im, meta['imgsz'])
            outs = dict(zip(meta['outputs'], sess.run(meta['outputs'], {meta['input']: t})))
            cpu.append((ground_truth(path, im.size),
                        postprocess(gather_levels(meta, outs), meta['strides'], lb, im.size, conf=0.05)))
        results[f'{model} CPU'] = score(cpu)
    print(f'{len(images)} картинок coco128; найдено/всего по классам (IoU>=0.5, порог {CONF}, телефон 0.08)\n')
    print(f"{'модель':22s} {'полнота':>8s} {'точность':>9s}  " + '  '.join(f'{n:>10s}' for n in FOCUS))
    for name, r in results.items():
        print(f"{name:22s} {r['recall']:8.2f} {r['precision']:9.2f}  " +
              '  '.join(f'{r[n][0]:>4d}/{r[n][1]:<5d}' for n in FOCUS))


if __name__ == '__main__':
    main()
