"""Relay на сервере Jupyter (рядом с RabbitMQ и TPU).

Канал VPN ~4 Мбит/с, а входной тензор YOLO 640 — 4.9 МБ, поэтому с ноутбука шлём только JPEG,
а тяжёлые тензоры гоняются внутри площадки:

    сайт --JPEG--> <login>.relay --> препроцессинг --> tpu.direct/input --> TPU
    сайт <--JSON-- постпроцессинг (DFL+NMS) <-- <login>.relay.tpu <--------'

Запуск (делает tools/deploy_relay.py): nohup python relay.py > relay.log 2>&1 &
"""
import glob
import io
import json
import os
import time
import traceback
import uuid

import numpy as np
import pika
from PIL import Image
from tpu_pycloud_format import (MESSAGE_ID_ATTR_NAME, RESPONSE_ROUTING_KEYS_ATTR_NAME,
                                TARGET_URI_ATTR_NAME, PyTask, deserialize, serialize)

from yolo import gather_levels, letterbox, postprocess

LOGIN = os.environ.get('LINQ_USER') or os.environ['USER']
AMQP_HOST = os.environ.get('AMQP_HOST', '10.0.114.30')
EXCHANGE, TPU_QUEUE = 'tpu.direct', 'input'
REQ_QUEUE, TPU_REPLY_QUEUE = f'{LOGIN}.relay', f'{LOGIN}.relay.tpu'
HERE = os.path.dirname(os.path.abspath(__file__))
STARTED = time.time()
TASK_TIMEOUT = 30.0


def load_models():
    models = {}
    for path in glob.glob(os.path.join(HERE, 'models', '*.json')):
        meta = json.load(open(path))
        meta['uri'] = f"file:///auto/cache/{LOGIN}/{meta['program']}?raw=False"
        models[os.path.splitext(os.path.basename(path))[0]] = meta
    return models


class Relay:
    def __init__(self):
        self.models = load_models()
        self.pending = {}
        self.conn = pika.BlockingConnection(pika.ConnectionParameters(
            AMQP_HOST, 5672, credentials=pika.PlainCredentials('guest', 'guest'), heartbeat=60))
        self.ch = self.conn.channel()
        for q in (REQ_QUEUE, TPU_REPLY_QUEUE):
            self.ch.queue_declare(queue=q, auto_delete=False)
            self.ch.queue_purge(queue=q)
        self.ch.queue_bind(exchange=EXCHANGE, queue=TPU_REPLY_QUEUE, routing_key=TPU_REPLY_QUEUE)
        self.ch.basic_consume(REQ_QUEUE, self.on_request, auto_ack=True)
        self.ch.basic_consume(TPU_REPLY_QUEUE, self.on_tpu_reply, auto_ack=True)
        print(f'relay {LOGIN} pid={os.getpid()} models={list(self.models)}', flush=True)

    def reply(self, reply_to, corr, payload):
        if reply_to:
            self.ch.basic_publish('', reply_to, json.dumps(payload).encode(),
                                  pika.BasicProperties(correlation_id=corr, content_type='application/json'))

    def on_request(self, ch, method, props, body):
        headers = props.headers or {}
        corr, reply_to = props.correlation_id, props.reply_to
        if headers.get('type') == 'ping':
            self.reply(reply_to, corr, {'pong': True, 'pid': os.getpid(), 'uptime': round(time.time() - STARTED),
                                        'models': list(self.models), 'pending': len(self.pending)})
            return
        try:
            t0 = time.perf_counter()
            meta = self.models[headers.get('model') or next(iter(self.models))]
            image = Image.open(io.BytesIO(body))
            tensor, lb = letterbox(image, meta['imgsz'])
            t1 = time.perf_counter()
            task_id = uuid.uuid4().hex
            self.pending[task_id] = dict(reply_to=reply_to, corr=corr, meta=meta, lb=lb, size=image.size,
                                         conf=float(headers.get('conf', 0.25)), iou=float(headers.get('iou', 0.45)),
                                         t0=t0, pre_ms=(t1 - t0) * 1000, sent=time.perf_counter(), wall=time.time())
            self.ch.basic_publish(EXCHANGE, TPU_QUEUE, serialize(PyTask({meta['input']: tensor})),
                                  pika.BasicProperties(headers={
                                      TARGET_URI_ATTR_NAME: meta['uri'],
                                      RESPONSE_ROUTING_KEYS_ATTR_NAME: json.dumps([TPU_REPLY_QUEUE]),
                                      MESSAGE_ID_ATTR_NAME: task_id}))
        except Exception as e:
            traceback.print_exc()
            self.reply(reply_to, corr, {'error': f'{type(e).__name__}: {e}'})

    def on_tpu_reply(self, ch, method, props, body):
        task = self.pending.pop((props.headers or {}).get(MESSAGE_ID_ATTR_NAME), None)
        if task is None:
            return
        tpu_ms = (time.perf_counter() - task['sent']) * 1000
        try:
            data, _, worker = deserialize(body)
            if not data:
                raise RuntimeError(f"TPU worker status={worker['status_code']}: {worker['error_message']}")
            t2 = time.perf_counter()
            meta = task['meta']
            dets = postprocess(gather_levels(meta, data), meta['strides'], task['lb'],
                               task['size'], conf=task['conf'], iou=task['iou'])
            t3 = time.perf_counter()
            self.reply(task['reply_to'], task['corr'], {
                'detections': dets, 'size': task['size'],
                'timing': {'pre_ms': round(task['pre_ms'], 1), 'tpu_ms': round(tpu_ms, 1),
                           'post_ms': round((t3 - t2) * 1000, 1),
                           'relay_ms': round((t3 - task['t0']) * 1000, 1)}})
        except Exception as e:
            traceback.print_exc()
            self.reply(task['reply_to'], task['corr'], {'error': f'{type(e).__name__}: {e}'})

    def expire(self):
        now = time.perf_counter()
        for task_id in [k for k, t in self.pending.items() if now - t['sent'] > TASK_TIMEOUT]:
            task = self.pending.pop(task_id)
            self.reply(task['reply_to'], task['corr'], {'error': f'TPU не ответил за {TASK_TIMEOUT:.0f} с'})

    def run(self):
        while True:
            self.conn.process_data_events(time_limit=1.0)
            self.expire()


if __name__ == '__main__':
    while True:
        try:
            Relay().run()
        except Exception:
            traceback.print_exc()
            time.sleep(3)
