"""Локальный демо-сайт: картинки и видео -> YOLOv8 на LinQ TPU (через relay) или на CPU для сравнения.

    .venv\\Scripts\\python -m uvicorn app.server:app --port 8000   (из корня проекта)
"""
import html
import io
import json
import os
import sys
import threading
import time
import uuid
from collections import Counter, OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'app'))
sys.path.insert(0, str(ROOT / 'tools'))
from linq_remote import load_env  # noqa: E402
from tpu_client import RelayClient  # noqa: E402
from yolo import SKELETON, gather_kpts, gather_levels, letterbox, merge, postprocess  # noqa: E402

load_env()
MODEL = os.environ.get('LINQ_MODEL', 'yolov8n_640')
POSE_MODEL = os.environ.get('LINQ_POSE_MODEL', 'yolov8n_pose_640')
META = json.loads((ROOT / 'models' / f'{MODEL}.json').read_text())
METAS = {m: json.loads((ROOT / 'models' / f'{m}.json').read_text())
         for m in (MODEL, POSE_MODEL) if (ROOT / 'models' / f'{m}.json').exists()}
POSE = POSE_MODEL in METAS
UPLOADS = ROOT / 'uploads'
UPLOADS.mkdir(exist_ok=True)
MAX_SIDE = 1280      # картинку ужимаем перед отправкой: по VPN ~4 Мбит/с
MAX_INFLIGHT = 8     # кадров видео одновременно «в полёте» к TPU: 1 → 2.3 FPS, 4 → 9.7, 8 → 15.2

relay = RelayClient(os.environ['LINQ_AMQP_HOST'], int(os.environ['LINQ_AMQP_PORT']), os.environ['LINQ_USER'])
cpu_pool = ThreadPoolExecutor(max_workers=2)
_cpu_sessions = {}
_cpu_lock = threading.Lock()


def cpu_session(model):
    with _cpu_lock:
        if model not in _cpu_sessions:
            import onnxruntime as ort
            _cpu_sessions[model] = ort.InferenceSession(str(ROOT / 'models' / f'{model}_cut.onnx'),
                                                        providers=['CPUExecutionProvider'])
        return _cpu_sessions[model]


def models_for(pose):
    return [MODEL] + ([POSE_MODEL] if pose and POSE else [])


def detect_cpu(jpeg, conf, models):
    t0 = time.perf_counter()
    image = Image.open(io.BytesIO(jpeg))
    tensor, lb = letterbox(image, META['imgsz'])
    t1 = time.perf_counter()
    raw = {}
    for m in models:
        outs = cpu_session(m).run(METAS[m]['outputs'], {METAS[m]['input']: tensor})
        raw[m] = dict(zip(METAS[m]['outputs'], outs))
    t2 = time.perf_counter()
    dets = merge([postprocess(gather_levels(METAS[m], raw[m]), METAS[m]['strides'], lb, image.size, conf=conf,
                              kpts=gather_kpts(METAS[m], raw[m])) for m in models])
    t3 = time.perf_counter()
    return {'detections': dets, 'size': image.size,
            'timing': {'pre_ms': round((t1 - t0) * 1000, 1), 'cpu_ms': round((t2 - t1) * 1000, 1),
                       'post_ms': round((t3 - t2) * 1000, 1), 'roundtrip_ms': round((t3 - t0) * 1000, 1)}}


# квантование под TPU роняет уверенность мелких классов: телефон у уха на CPU 0.23, на TPU 0.11.
# Для них свой порог, но засчитываем только внутри рамки человека (телефон в руке / у уха)
CLASS_CONF = {'cell phone': 0.08}


def submit(jpeg, mode, conf, pose=True):
    models = models_for(pose)
    floor = min([conf, *CLASS_CONF.values()])  # от модели берём всё выше минимального порога, фильтруем здесь
    if mode == 'cpu':
        return cpu_pool.submit(detect_cpu, jpeg, floor, models)
    # pika не кодирует float в AMQP-заголовках — передаём строкой, relay делает float()
    return relay.submit(jpeg, model=','.join(models), conf=str(floor))


def _center_in(box, outer):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def apply_thresholds(dets, conf):
    people = [d['box'] for d in dets if d['name'] == 'person' and d['conf'] >= conf]
    kept = [d for d in dets if d['conf'] >= conf]
    for d in sorted(dets, key=lambda d: -d['conf']):
        if conf > d['conf'] >= CLASS_CONF.get(d['name'], 1.0) and any(_center_in(d['box'], p) for p in people) \
                and not any(k['name'] == d['name'] and (_center_in(d['box'], k['box']) or _center_in(k['box'], d['box']))
                            for k in kept):
            kept.append(d)
    return kept


VEHICLES = {'car', 'truck', 'bus', 'motorcycle', 'train', 'bicycle'}


VISIBLE = 0.5
# сколько секунд нарушение должно длиться, чтобы стать событием (случайный кадр тревогу не вызывает)
HOLD_S = {'02': 1.0, '03': 2.0, '04': 2.0, '07': 2.0}


def _pt(kpts, i):
    x, y, v = kpts[i]
    return np.array([x, y]) if v > VISIBLE else None


def pose_rules(person):
    """Правила по скелету одного человека (COCO-точки: 0 нос, 5-6 плечи, 9-10 кисти, 11-12 бёдра)."""
    k = person['kpts']
    events = []
    nose, ls, rs = _pt(k, 0), _pt(k, 5), _pt(k, 6)
    if ls is not None and rs is not None:
        shoulders = np.linalg.norm(ls - rs)
    else:
        shoulders = 0.25 * (person['box'][3] - person['box'][1])
    if nose is not None and shoulders > 0:
        for wrist in (_pt(k, 9), _pt(k, 10)):
            if wrist is not None and np.linalg.norm(wrist - nose) < 0.45 * shoulders:
                events.append({'code': '04', 'title': 'Курение? (кисть у рта)', 'level': 'warn', 'box': person['box']})
                break
    lh, rh = _pt(k, 11), _pt(k, 12)
    if ls is not None and rs is not None and lh is not None and rh is not None:
        torso = (lh + rh) / 2 - (ls + rs) / 2
        if abs(torso[1]) < 0.6 * abs(torso[0]):  # корпус ближе к горизонтали, чем к вертикали
            events.append({'code': '07', 'title': 'Человек лежит / падение', 'level': 'critical',
                           'box': person['box']})
    return events


def rules(dets):
    """Кандидаты в нарушения на одном кадре; подтверждение по времени (HOLD_S) — в потоке видео."""
    people = [d for d in dets if d['name'] == 'person']
    events = []
    phones = [d for d in dets if d['name'] == 'cell phone']
    if people and phones:
        events.append({'code': '03', 'title': 'Телефон в руках', 'level': 'warn', 'box': phones[0]['box']})
    for p in people:
        if 'kpts' in p:
            events += [e for e in pose_rules(p) if e['code'] not in {x['code'] for x in events}]
    for p in people:
        px, py, ph = (p['box'][0] + p['box'][2]) / 2, p['box'][3], p['box'][3] - p['box'][1]
        for v in dets:
            if v['name'] in VEHICLES:
                vx = min(max(px, v['box'][0]), v['box'][2])
                vy = min(max(py, v['box'][1]), v['box'][3])
                if np.hypot(px - vx, py - vy) < 0.5 * ph:
                    events.append({'code': '02', 'title': 'Опасное сближение с транспортом', 'level': 'critical',
                                   'box': p['box']})
                    break
        else:
            continue
        break
    return events


app = FastAPI(title='ICEQ · LinQ TPU demo')
app.mount('/static', StaticFiles(directory=ROOT / 'app' / 'static'), name='static')


@app.get('/')
def index():
    return FileResponse(ROOT / 'app' / 'static' / 'index.html')


@app.get('/api/status')
def status():
    try:
        pong = relay.ping(timeout=5)
        tpu = {'ok': True, **pong}
    except Exception as e:
        tpu = {'ok': False, 'error': relay.error or f'relay не отвечает ({type(e).__name__})'}
    return {'tpu': tpu, 'model': MODEL, 'meta': META, 'pose': POSE_MODEL if POSE else None,
            'cpu_available': (ROOT / 'models' / f'{MODEL}_cut.onnx').exists()}


def to_jpeg(data, max_side=MAX_SIDE):
    image = Image.open(io.BytesIO(data)).convert('RGB')
    scale = min(1.0, max_side / max(image.size))
    if scale < 1.0:
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.BILINEAR)
    buf = io.BytesIO()
    image.save(buf, 'JPEG', quality=85)
    return buf.getvalue(), scale


@app.post('/api/detect')
async def detect(file: UploadFile = File(...), mode: str = 'tpu', conf: float = 0.25, pose: bool = True):
    jpeg, scale = to_jpeg(await file.read())
    try:
        res = submit(jpeg, mode, conf, pose).result(timeout=60)
    except Exception as e:
        raise HTTPException(502, f'{type(e).__name__}: {e}')
    if 'error' in res:
        raise HTTPException(502, res['error'])
    res['detections'] = apply_thresholds(res['detections'], conf)
    for d in res['detections']:
        d['box'] = [round(v / scale, 1) for v in d['box']]
    res['events'] = rules(res['detections'])
    res['upload_kb'] = round(len(jpeg) / 1024)
    return res


ORG_DIRS = {'data': ROOT / 'data', 'org': ROOT / 'org_materials'}
ORG_EXT = {'.pdf', '.ipynb', '.py', '.txt', '.cpp', '.jpeg', '.jpg'}   # .ovpn (ключ VPN) сюда не входит
ORG_GROUPS = [('data', 'Кейс и инструкции (рассылка оргов)'), ('org/tpu_framework', 'SDK tpu_framework'),
              ('org/jupyter', 'Сервер Jupyter (examples, packages)'), ('org/api', 'Примеры API (Python / C / C++)')]


def org_path(rel):
    root_key, _, rest = rel.partition('/')
    base = ORG_DIRS.get(root_key)
    path = (base / rest).resolve() if base else None
    if not path or base.resolve() not in path.parents or path.suffix.lower() not in ORG_EXT or not path.is_file():
        raise HTTPException(404)
    return path


@app.get('/api/org')
def org_list():
    groups = []
    for prefix, title in ORG_GROUPS:
        root_key, _, sub = prefix.partition('/')
        base = ORG_DIRS[root_key] / sub
        files = sorted(p for p in base.rglob('*') if p.is_file() and p.suffix.lower() in ORG_EXT and
                       '.ipynb_checkpoints' not in p.parts and p.name != 'tpu_framework.txt')
        groups.append({'title': title, 'files': [
            {'name': p.name, 'path': f'{root_key}/{p.relative_to(ORG_DIRS[root_key]).as_posix()}',
             'kind': {'.pdf': 'pdf', '.ipynb': 'notebook', '.jpeg': 'image', '.jpg': 'image'}.get(p.suffix.lower(), 'code'),
             'kb': round(p.stat().st_size / 1024)} for p in files]})
    return groups


@app.get('/org/{rel:path}')
def org_file(rel: str):
    path = org_path(rel)
    if path.suffix.lower() == '.ipynb':
        return HTMLResponse(render_notebook(path))
    media = {'.pdf': 'application/pdf', '.jpeg': 'image/jpeg', '.jpg': 'image/jpeg'}.get(path.suffix.lower(),
                                                                                       'text/plain; charset=utf-8')
    return FileResponse(path, media_type=media, content_disposition_type='inline')


def render_notebook(path):
    nb = json.loads(path.read_text(encoding='utf-8'))
    parts = []
    for cell in nb['cells']:
        src = html.escape(''.join(cell['source']))
        parts.append(f'<pre class="{cell["cell_type"]}">{src}</pre>')
        for out in cell.get('outputs', []):
            data = out.get('data', {})
            if 'image/png' in data:
                parts.append(f'<img src="data:image/png;base64,{data["image/png"]}">')
            text = ''.join(out.get('text', data.get('text/plain', [])))
            if text:
                parts.append(f'<pre class="out">{html.escape(text)}</pre>')
    style = ('body{font:14px system-ui;background:#0d1418;color:#e6eef2;padding:16px}'
             'pre{background:#141e24;border:1px solid #25343d;border-radius:8px;padding:10px;overflow:auto}'
             'pre.out{background:#0b1114;color:#a8d520}img{max-width:100%;border-radius:8px}')
    return f'<!doctype html><meta charset="utf-8"><style>{style}</style><h3>{html.escape(path.name)}</h3>' + ''.join(parts)


@app.get('/api/samples')
def samples():
    folder = ROOT / 'app' / 'static' / 'samples'
    files = sorted(p.name for p in folder.glob('*') if p.is_file()) if folder.exists() else []
    return {'images': [f for f in files if f.lower().endswith(('.jpg', '.jpeg', '.png'))],
            'videos': [f for f in files if f.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))]}


@app.get('/api/bench')
def bench(mode: str = 'tpu', n: int = 20, inflight: int = 4, sample: str = '', pose: bool = False):
    """Замер: n запросов одной и той же картинки, до inflight одновременно."""
    folder = ROOT / 'app' / 'static' / 'samples'
    path = folder / sample if sample else next(iter(sorted(folder.glob('*.jpg'))), None)
    if not path or not path.exists():
        raise HTTPException(404, 'нет картинки для замера в app/static/samples')
    jpeg, _ = to_jpeg(path.read_bytes())
    n, inflight = max(1, min(n, 200)), max(1, min(inflight, 16))
    lat, inner, errors = [], [], []
    queue_ = deque()
    t0 = time.perf_counter()
    for i in range(n):
        queue_.append(submit(jpeg, mode, 0.25, pose))
        while len(queue_) >= inflight or (i == n - 1 and queue_):
            try:
                res = queue_.popleft().result(timeout=60)
                if 'error' in res:
                    raise RuntimeError(res['error'])
                lat.append(res['timing']['roundtrip_ms'])
                inner.append(res['timing'].get('tpu_ms', res['timing'].get('cpu_ms')))
            except Exception as e:
                errors.append(str(e))
    total = time.perf_counter() - t0
    pct = lambda a, q: round(float(np.percentile(a, q)), 1) if a else None  # noqa: E731
    return {'mode': mode, 'n': n, 'inflight': inflight, 'ok': len(lat), 'errors': errors[:3],
            'fps': round(len(lat) / total, 2), 'total_s': round(total, 2), 'upload_kb': round(len(jpeg) / 1024),
            'latency_ms': {'p50': pct(lat, 50), 'p90': pct(lat, 90), 'min': pct(lat, 0)},
            'inner_ms': {'p50': pct(inner, 50), 'p90': pct(inner, 90), 'min': pct(inner, 0)}}


VIDEOS, STATS = {}, {}
SNAPS = OrderedDict()  # снимки момента нарушения: id -> JPEG


@app.get('/api/snap/{snap}')
def snapshot(snap: str):
    if snap not in SNAPS:
        raise HTTPException(404)
    return Response(SNAPS[snap], media_type='image/jpeg')


@app.post('/api/video')
async def upload_video(file: UploadFile = File(...)):
    vid = uuid.uuid4().hex[:10]
    path = UPLOADS / f'{vid}{Path(file.filename or "v.mp4").suffix or ".mp4"}'
    with open(path, 'wb') as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    return register_video(vid, path, file.filename)


@app.post('/api/video/sample')
def sample_video(name: str):
    path = ROOT / 'app' / 'static' / 'samples' / Path(name).name
    if not path.exists():
        raise HTTPException(404)
    return register_video(uuid.uuid4().hex[:10], path, path.name)


@app.post('/api/video/webcam')
def webcam(index: int = 0):
    vid = uuid.uuid4().hex[:10]
    VIDEOS[vid] = (None, {'id': vid, 'name': 'Камера ноутбука (live)', 'webcam': index,
                          'fps': 30, 'frames': 0, 'width': 1280, 'height': 720})
    return VIDEOS[vid][1]


def register_video(vid, path, name):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise HTTPException(400, 'не удалось открыть видео')
    info = {'id': vid, 'name': name, 'fps': round(cap.get(cv2.CAP_PROP_FPS) or 25, 2),
            'frames': int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
            'width': int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), 'height': int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}
    cap.release()
    VIDEOS[vid] = (path, info)
    return info


def color(cls):
    rng = np.random.default_rng(cls * 7 + 3)
    return tuple(int(c) for c in rng.integers(60, 255, 3))


def draw_skeleton(frame, kpts):
    pts = [(int(x), int(y)) if v > VISIBLE else None for x, y, v in kpts]
    for a, b in SKELETON:
        if pts[a] and pts[b]:
            cv2.line(frame, pts[a], pts[b], (80, 255, 170), 3, cv2.LINE_AA)
    for i, p in enumerate(pts):
        if p:
            # кисти и нос крупнее — по ним считаются правила «курение» и «телефон у уха»
            cv2.circle(frame, p, 7 if i in (0, 9, 10) else 4, (0, 200, 255) if i in (0, 9, 10) else (255, 255, 255),
                       -1, cv2.LINE_AA)


_FONTS = {}


def font(size):
    if size not in _FONTS:
        for name in ('arialbd.ttf', 'arial.ttf', 'DejaVuSans-Bold.ttf'):  # нужен шрифт с кириллицей
            try:
                _FONTS[size] = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        else:
            _FONTS[size] = ImageFont.load_default()
    return _FONTS[size]


def draw_alerts(frame, alerts, pending):
    """Подтверждённые нарушения — красная рамка кадра, баннер и рамка вокруг нарушителя;
    замеченные, но ещё не подтверждённые — оранжевая полоса с отсчётом."""
    h, w = frame.shape[:2]
    for ev in alerts:
        x1, y1, x2, y2 = map(int, ev['box'])
        cv2.rectangle(frame, (x1 - 4, y1 - 4), (x2 + 4, y2 + 4), (0, 0, 255), 6)
    if alerts:
        cv2.rectangle(frame, (0, 0), (w - 1, h - 1), (0, 0, 255), 14)
        cv2.rectangle(frame, (0, h - 60), (w, h), (0, 0, 200), -1)
    if pending:
        cv2.rectangle(frame, (0, 26), (w, 60), (0, 120, 230), -1)
    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    pen = ImageDraw.Draw(img)
    for ev in alerts:
        x1, y1 = int(ev['box'][0]), int(ev['box'][1])
        pen.rectangle((x1 - 4, y1 - 34, x1 + 170, y1 - 4), fill=(255, 0, 0))
        pen.text((x1 + 2, y1 - 32), 'НАРУШИТЕЛЬ', font=font(24), fill=(255, 255, 255))
    if alerts:
        text = '▲ НАРУШЕНИЕ: ' + ' · '.join(f"п.{e['code']} {e['title']}" for e in alerts)
        size = 30
        while size > 14 and pen.textlength(text, font=font(size)) > w - 32:  # уменьшаем, пока не влезет
            size -= 2
        pen.text((16, h - 30 - size // 2 - 2), text, font=font(size), fill=(255, 255, 255))
    if pending:
        pen.text((12, 31), 'проверка: ' + '   '.join(pending), font=font(22), fill=(255, 255, 255))
    frame[:] = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


def draw(frame, dets, hud, pending=(), alerts=()):
    for d in dets:
        x1, y1, x2, y2 = map(int, d['box'])
        c = (40, 40, 230) if d['name'] == 'person' else color(d['cls'])
        if 'kpts' in d:
            draw_skeleton(frame, d['kpts'])
        cv2.rectangle(frame, (x1, y1), (x2, y2), c, 2)
        label = f"{d['name']} {d['conf']:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 4, y1), c, -1)
        cv2.putText(frame, label, (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.rectangle(frame, (0, 0), (frame.shape[1], 26), (20, 20, 20), -1)
    cv2.putText(frame, hud, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 255, 120), 1, cv2.LINE_AA)
    if pending or alerts:
        draw_alerts(frame, alerts, pending)
    return frame


@app.get('/api/video/{vid}/stream')
def stream(vid: str, mode: str = 'tpu', fps: float = 10, conf: float = 0.25, pose: bool = True):
    if vid not in VIDEOS:
        raise HTTPException(404)
    path, info = VIDEOS[vid]
    run = uuid.uuid4().hex
    stats = STATS[vid] = {'run': run, 'mode': mode, 'done': False, 'processed': 0, 'errors': 0,
                          'fps': 0.0, 'latency_ms': deque(maxlen=50), 'tpu_ms': deque(maxlen=50),
                          'classes': Counter(), 'events': deque(maxlen=30), 'last_error': None}

    live = 'webcam' in info

    def open_capture():
        if not live:
            return cv2.VideoCapture(str(path))
        cap = cv2.VideoCapture(info['webcam'], cv2.CAP_DSHOW)  # DSHOW на Windows открывается за ~1 с, MSMF — до 10 с
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def gen():
        cap = open_capture()
        if live and not cap.isOpened():
            stats['last_error'] = 'камера не открылась (занята другим приложением?)'
            stats['done'] = True
            return
        step = max(1, round((info['fps'] or 25) / max(fps, 0.1)))
        inflight, done_times, idx, last_yield = deque(), deque(maxlen=20), 0, 0.0
        recent_events = {}
        t_start, next_submit = time.perf_counter(), 0.0

        def emit(frame, fut, sec):
            nonlocal last_yield
            try:
                res = fut.result(timeout=60)
                if 'error' in res:
                    raise RuntimeError(res['error'])
                res['detections'] = apply_thresholds(res['detections'], conf)
            except Exception as e:
                stats['errors'] += 1
                stats['last_error'] = str(e)
                res = {'detections': [], 'timing': {}}
            done_times.append(time.perf_counter())
            if len(done_times) > 1:
                stats['fps'] = round((len(done_times) - 1) / (done_times[-1] - done_times[0]), 2)
            t = res['timing']
            if 'roundtrip_ms' in t:
                stats['latency_ms'].append(t['roundtrip_ms'])
            if 'tpu_ms' in t or 'cpu_ms' in t:
                stats['tpu_ms'].append(t.get('tpu_ms', t.get('cpu_ms')))
            stats['processed'] += 1
            stats['classes'].update(d['name'] for d in res['detections'])
            # нарушение становится событием, только если держится HOLD_S секунд подряд,
            # и даёт ровно одно событие, пока не прекратится (пропало дольше 1.5 с)
            pending, new_events = [], []
            for ev in rules(res['detections']):
                s = recent_events.setdefault(ev['code'], {'since': sec, 'fired': False})
                s['seen'], s['ev'] = sec, ev
                held = sec - s['since']
                if not s['fired'] and held >= HOLD_S.get(ev['code'], 1.0):
                    s['fired'], s['fired_at'] = True, sec
                    new_events.append(ev)
                if not s['fired']:
                    pending.append(f"{ev['title']} {held:.1f} / {HOLD_S.get(ev['code'], 1.0):.0f} с")
            # подтверждённое нарушение подсвечиваем, пока оно длится, и ещё минимум 3 с после срабатывания
            alerts = [s['ev'] for s in recent_events.values()
                      if s['fired'] and (sec - s['seen'] < 0.6 or sec - s['fired_at'] < 3)]
            for code in [c for c, s in recent_events.items()
                         if sec - s['seen'] > 1.5 and (not s['fired'] or sec - s['fired_at'] > 3)]:
                del recent_events[code]  # нарушение прекратилось: следующее такое же будет новым событием
            hud = (f"{mode.upper()} | {stats['fps']:.1f} FPS | e2e {t.get('roundtrip_ms', 0):.0f} ms"
                   + (f" | tpu {t['tpu_ms']:.0f} ms" if 'tpu_ms' in t else '')
                   + (f" | cpu {t['cpu_ms']:.0f} ms" if 'cpu_ms' in t else '')
                   + f" | t={sec:.1f}s")
            out = draw(frame, res['detections'], hud, pending, alerts)
            ok, buf = cv2.imencode('.jpg', out, [cv2.IMWRITE_JPEG_QUALITY, 80])
            for ev in new_events:
                # оповещение по кейсу: кадр-доказательство, камера, время, тип нарушения
                snap = uuid.uuid4().hex[:12]
                SNAPS[snap] = buf.tobytes()
                while len(SNAPS) > 300:
                    SNAPS.popitem(last=False)
                stats['events'].appendleft({k: v for k, v in ev.items() if k != 'box'} | {
                    't': round(sec, 1), 'snap': snap, 'camera': info['name'],
                    'time': time.strftime('%H:%M:%S')})
            wait = 1.0 / max(fps, 0.1) - (time.perf_counter() - last_yield)
            if wait > 0:
                time.sleep(wait)
            last_yield = time.perf_counter()
            return b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n'

        try:
            while STATS.get(vid, {}).get('run') == run:
                if not live and idx % step:
                    if not cap.grab():
                        break
                    idx += 1
                    continue
                ok, frame = cap.read()
                if not ok:
                    break
                if live:
                    # с камеры читаем всё подряд (иначе копится буфер и растёт задержка),
                    # а на TPU отправляем не чаще fps кадров в секунду
                    now = time.perf_counter()
                    if now < next_submit:
                        continue
                    next_submit = now + 1.0 / max(fps, 0.1)
                    frame = cv2.flip(frame, 1)  # зеркально, как в обычном видеозвонке
                    sec = now - t_start
                else:
                    sec = idx / (info['fps'] or 25)
                h, w = frame.shape[:2]
                if max(h, w) > 960:
                    s = 960 / max(h, w)
                    frame = cv2.resize(frame, (round(w * s), round(h * s)))
                jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
                inflight.append((frame, submit(jpeg, mode, conf, pose), sec))
                idx += 1
                while len(inflight) >= MAX_INFLIGHT or (inflight and inflight[0][1].done()):
                    yield emit(*inflight.popleft())
            while inflight and STATS.get(vid, {}).get('run') == run:
                yield emit(*inflight.popleft())
        finally:
            stats['done'] = True
            cap.release()

    return StreamingResponse(gen(), media_type='multipart/x-mixed-replace; boundary=frame')


@app.get('/api/video/{vid}/stats')
def video_stats(vid: str):
    s = STATS.get(vid)
    if not s:
        return {'processed': 0}
    lat, tpu = list(s['latency_ms']), list(s['tpu_ms'])
    return {'mode': s['mode'], 'done': s['done'], 'processed': s['processed'], 'errors': s['errors'],
            'fps': s['fps'], 'last_error': s['last_error'],
            'latency_ms': round(float(np.median(lat)), 1) if lat else None,
            'tpu_ms': round(float(np.median(tpu)), 1) if tpu else None,
            'classes': s['classes'].most_common(8), 'events': list(s['events'])}


@app.post('/api/video/{vid}/stop')
def stop(vid: str):
    STATS.pop(vid, None)
    return {'ok': True}
