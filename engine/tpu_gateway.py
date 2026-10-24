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


def _raw_program(uri: str) -> str | None:
    """Путь к программе, если включён сырой режим (LINQ_TPU_RAW=1) и кодек SDK доступен. По умолчанию выключен:
    на стенде (квота CPU 1,5 ядра) упаковка стоит дороже, чем экономия трафика — 0,8 против 1,1 кадра/с."""
    if os.environ.get("LINQ_TPU_RAW", "0") != "1":
        return None
    try:
        import tpu_io  # noqa: F401
    except ImportError:
        return None
    return uri.split("file://", 1)[1].split("?", 1)[0]


def run(uri: str, input_name: str, tensor: np.ndarray, timeout: float = 30.0) -> dict:
    """Одна задача на TPU. В сыром режиме кадр упаковывается в int8-раскладку TPU кодеком SDK (engine/tpu_raw.py):
    в очередь идёт в 3 раза меньше данных, ответ — в 3,6 раза меньше; результат совпадает с float32-режимом."""
    program = _raw_program(uri)
    if program:
        from . import tpu_raw
        tensors = tpu_raw.encode(program, {input_name: tensor})
        uri = uri.replace("raw=False", "raw=True")
    else:
        tensors = {input_name: tensor}
    out = _send(uri, tensors, timeout)
    return tpu_raw.decode(program, out) if program else out


def _send(uri: str, tensors: dict, timeout: float) -> dict:
    import pika
    from tpu_pycloud_format import (MESSAGE_ID_ATTR_NAME, RESPONSE_ROUTING_KEYS_ATTR_NAME,
                                    TARGET_URI_ATTR_NAME, PyTask, deserialize, serialize)
    for attempt in range(2):
        try:
            if _conn is None:
                _init()
            conn, ch, reply = _conn
            mid = uuid.uuid4().hex
            ch.basic_publish("tpu.direct", "input", serialize(PyTask(tensors)), pika.BasicProperties(
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
