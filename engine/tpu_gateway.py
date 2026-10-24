"""Шлюз к облачному TPU стенда в отдельных процессах.

Внутри сервиса десятки потоков (камеры, отрисовка, numpy) держат GIL, и единственный поток pika, который
гоняет мегабайтные тензоры через RabbitMQ, голодал: задержка TPU росла с 0,3 до 5 с. Здесь каждый процесс
пула держит своё соединение и шлёт задачи по одной; модуль лёгкий (pika, numpy), без кода сервиса.
"""
import json
import os
import uuid

import numpy as np

_conn = None


def _init():
    global _conn
    import pika
    params = pika.ConnectionParameters(os.environ.get("LINQ_AMQP_HOST", "10.0.114.30"), 5672,
                                       credentials=pika.PlainCredentials("guest", "guest"), heartbeat=60)
    conn = pika.BlockingConnection(params)
    ch = conn.channel()
    reply = f"guard_{uuid.uuid4().hex[:8]}"
    ch.queue_declare(queue=reply, exclusive=True)
    ch.queue_bind(exchange="tpu.direct", queue=reply, routing_key=reply)
    _conn = (conn, ch, reply)


def run(uri: str, input_name: str, tensor: np.ndarray, timeout: float = 30.0) -> dict:
    import pika
    from tpu_pycloud_format import (MESSAGE_ID_ATTR_NAME, RESPONSE_ROUTING_KEYS_ATTR_NAME,
                                    TARGET_URI_ATTR_NAME, PyTask, deserialize, serialize)
    for attempt in range(2):
        try:
            if _conn is None:
                _init()
            conn, ch, reply = _conn
            mid = uuid.uuid4().hex
            ch.basic_publish("tpu.direct", "input", serialize(PyTask({input_name: tensor})), pika.BasicProperties(
                headers={TARGET_URI_ATTR_NAME: uri, RESPONSE_ROUTING_KEYS_ATTR_NAME: json.dumps([reply]),
                         MESSAGE_ID_ATTR_NAME: mid}))
            for method, props, body in ch.consume(reply, auto_ack=True, inactivity_timeout=timeout):
                if method is None:
                    raise TimeoutError(f"TPU не ответил за {timeout} с")
                if (props.headers or {}).get(MESSAGE_ID_ATTR_NAME) != mid:
                    continue                                   # опоздавший ответ на прошлую задачу
                ch.cancel()
                data, _, worker = deserialize(body)
                if not data:
                    raise RuntimeError(f"TPU: {worker['error_message']}")
                return data
        except (pika.exceptions.AMQPError, OSError):
            globals()["_conn"] = None                          # обрыв: переподключаемся и пробуем ещё раз
            if attempt:
                raise
