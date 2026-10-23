"""Проверка инференса на LinQ TPU с локальной машины через RabbitMQ (по образцу examples/example.ipynb).

    python tools/tpu_smoke.py            # эталонный ResNet50 (сначала: linq_remote.py deploy ...resnet50...tpu)
    python tools/tpu_smoke.py -n 50      # 50 запросов, статистика задержки
"""
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import numpy as np
import pika
from PIL import Image
from tpu_pycloud_format import (MESSAGE_ID_ATTR_NAME, RESPONSE_ROUTING_KEYS_ATTR_NAME,
                                TARGET_URI_ATTR_NAME, PyTask, deserialize, serialize)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from linq_remote import ROOT, load_env  # noqa: E402

EXCHANGE, INPUT_QUEUE = 'tpu.direct', 'input'
PROGRAM = 'torch_resnet50_b1_o5_h1m.tpu'
IMG = ROOT / 'org_materials/jupyter/examples/ILSVRC2012_val_00000045.JPEG'
INPUT_TENSOR_NAME, OUTPUT_TENSOR_NAME = 'input.1', '495'
EXPECTED_CLASS = 257  # Newfoundland dog


def preprocess(image, hw=(224, 224)):
    width, height = image.size
    side = int(min(width, height) * 0.85)
    top, left = (height - side) // 2, (width - side) // 2
    image = image.convert('RGB').crop((left, top, left + side, top + side)).resize(hw)
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    arr = (np.asarray(image) / 255.0 - mean) / std
    return np.transpose(arr, (2, 0, 1))[None].astype(np.float32)


def main():
    load_env()
    p = argparse.ArgumentParser()
    p.add_argument('-n', type=int, default=10, help='число запросов')
    p.add_argument('--timeout', type=float, default=60)
    args = p.parse_args()

    uri = f"file:///auto/cache/{os.environ['LINQ_USER']}/{PROGRAM}?raw=False"
    connection = pika.BlockingConnection(pika.ConnectionParameters(
        os.environ['LINQ_AMQP_HOST'], int(os.environ['LINQ_AMQP_PORT']),
        credentials=pika.PlainCredentials('guest', 'guest')))
    channel = connection.channel()
    reply_queue = f'local_{uuid.uuid4().hex[:8]}'
    # exclusive, а не auto_delete: auto_delete-очередь удаляется после первого channel.cancel()
    channel.queue_declare(queue=reply_queue, exclusive=True)
    channel.queue_bind(exchange=EXCHANGE, queue=reply_queue, routing_key=reply_queue)

    body = serialize(PyTask({INPUT_TENSOR_NAME: preprocess(Image.open(IMG))}))
    round_trips, tpu_times = [], []
    for i in range(args.n):
        task_id = uuid.uuid4().hex
        started = time.perf_counter()
        channel.basic_publish(exchange=EXCHANGE, routing_key=INPUT_QUEUE, body=body,
                              properties=pika.BasicProperties(headers={
                                  TARGET_URI_ATTR_NAME: uri,
                                  RESPONSE_ROUTING_KEYS_ATTR_NAME: json.dumps([reply_queue]),
                                  MESSAGE_ID_ATTR_NAME: task_id}))
        for method, properties, reply in channel.consume(reply_queue, auto_ack=True, inactivity_timeout=0.05):
            if method is not None:
                break
            if time.perf_counter() - started > args.timeout:
                sys.exit(f'нет ответа за {args.timeout} с')
        channel.cancel()
        round_trips.append(time.perf_counter() - started)

        assert properties.headers[MESSAGE_ID_ATTR_NAME] == task_id, 'чужой ответ'
        data, _, worker = deserialize(reply)
        if not data:
            sys.exit(f"ошибка воркера (status={worker['status_code']}): {worker['error_message']}")
        if i == 0:
            print('ответ воркера:', {k: v for k, v in worker.items() if k != 'error_message'})
        tpu_times.append(worker.get('inference_time') or 0.0)
        cls = int(np.argmax(data[OUTPUT_TENSOR_NAME])) + 1
        print(f'#{i + 1}: класс {cls} {"OK" if cls == EXPECTED_CLASS else "MISMATCH"}, '
              f'round-trip {round_trips[-1] * 1000:.0f} мс, inference_time={tpu_times[-1]}')
    connection.close()

    rt = np.array(round_trips[1:] or round_trips) * 1000  # первый запрос прогревает загрузку программы
    print(f'\nround-trip без прогрева: median {np.median(rt):.0f} мс, p90 {np.percentile(rt, 90):.0f} мс, '
          f'min {rt.min():.0f} мс  (~{1000 / np.median(rt):.1f} FPS последовательно)')


if __name__ == '__main__':
    main()
