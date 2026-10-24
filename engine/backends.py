"""Бэкенды инференса. Все возвращают output0 модели YOLO, дальше общий постпроцессинг."""
import contextlib
import os
import threading
import time
from typing import Protocol

import numpy as np

# Пока не проверено на стенде, можно ли звать несколько программ одного TPU из разных потоков, вызовы
# сериализуются. LINQ_TPU_PARALLEL=1 снимает блокировку — так меряем выигрыш от параллельных программ.
_TPU_LOCK = None if os.environ.get("LINQ_TPU_PARALLEL") == "1" else threading.Lock()


class InferenceBackend(Protocol):
    name: str

    def run(self, tensor: np.ndarray) -> np.ndarray: ...


class OnnxBackend:
    """ONNX Runtime: CUDA на vast-сервере, CPU на ноутбуке. Для разработки и сравнения с LinQ."""

    def __init__(self, path: str, prefer_gpu: bool = True):
        import onnxruntime as ort

        providers = ["CPUExecutionProvider"]
        if prefer_gpu and "CUDAExecutionProvider" in ort.get_available_providers():
            if hasattr(ort, "preload_dlls"):
                ort.preload_dlls()           # CUDA и cuDNN из pip-пакетов nvidia-*, которые ставит torch
            providers.insert(0, "CUDAExecutionProvider")
        opts = ort.SessionOptions()
        if providers[0].startswith("CUDA"):
            # на GPU потоки CPU почти не нужны, а у каждой из десятков сессий (камеры × модели) свой пул,
            # по умолчанию на все ядра и с активным ожиданием — они отнимают процессор у декодирования видео
            opts.intra_op_num_threads = 2
            opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        else:
            # на CPU тоже не «на все ядра»: на стенде 64 ядра и десятки сессий — упираемся в лимит потоков
            opts.intra_op_num_threads = int(os.environ.get("LINQ_CPU_THREADS", "4"))
            opts.inter_op_num_threads = 1
            opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self.session = ort.InferenceSession(path, sess_options=opts, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.name = "onnx-" + ("cuda" if self.session.get_providers()[0].startswith("CUDA") else "cpu")

    def run(self, tensor: np.ndarray) -> np.ndarray:
        return self.session.run(None, {self.input_name: tensor})[0]


class _TpuCloud:
    """Облачный TPU стенда: задачи в RabbitMQ (обмен tpu.direct, очередь input), ответы в свою очередь.
    pika не потокобезопасна: соединением владеет один поток, run() из любых потоков ждут Future."""

    _inst = None

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def __init__(self):
        import queue
        import pika
        self._pika, self._out, self._fut = pika, queue.Queue(), {}
        self.host = os.environ.get("LINQ_AMQP_HOST", "10.0.114.30")
        threading.Thread(target=self._loop, daemon=True, name="tpu-cloud").start()

    def submit(self, uri: str, tensors: dict):
        from concurrent.futures import Future
        fut = Future()
        self._out.put((uri, tensors, fut))
        return fut

    def _loop(self):
        import json, uuid
        from tpu_pycloud_format import (MESSAGE_ID_ATTR_NAME, RESPONSE_ROUTING_KEYS_ATTR_NAME,
                                        TARGET_URI_ATTR_NAME, PyTask, deserialize, serialize)
        pika = self._pika
        while True:
            try:
                conn = pika.BlockingConnection(pika.ConnectionParameters(
                    self.host, 5672, credentials=pika.PlainCredentials("guest", "guest"), heartbeat=60))
                ch = conn.channel()
                reply = f"guard_{uuid.uuid4().hex[:8]}"
                ch.queue_declare(queue=reply, exclusive=True)
                ch.queue_bind(exchange="tpu.direct", queue=reply, routing_key=reply)

                def on_reply(_ch, _m, props, body):
                    fut = self._fut.pop((props.headers or {}).get(MESSAGE_ID_ATTR_NAME), None)
                    if fut is None:
                        return
                    data, _, worker = deserialize(body)
                    if data:
                        fut.set_result(data)
                    else:
                        fut.set_exception(RuntimeError(f"TPU: {worker['error_message']}"))

                ch.basic_consume(reply, on_reply, auto_ack=True)
                while True:
                    while not self._out.empty():
                        uri, tensors, fut = self._out.get_nowait()
                        mid = uuid.uuid4().hex
                        self._fut[mid] = fut
                        ch.basic_publish("tpu.direct", "input", serialize(PyTask(tensors)), pika.BasicProperties(
                            headers={TARGET_URI_ATTR_NAME: uri, RESPONSE_ROUTING_KEYS_ATTR_NAME: json.dumps([reply]),
                                     MESSAGE_ID_ATTR_NAME: mid}))
                    conn.process_data_events(time_limit=0.005)
            except Exception as e:  # обрыв брокера: всем ждущим ошибка, переподключаемся
                for f in self._fut.values():
                    if not f.done():
                        f.set_exception(ConnectionError(str(e)))
                self._fut.clear()
                time.sleep(2)


class LinqCloudBackend:
    """Стенд хакатона: TPU не виден как /dev/tpu, программы выполняет облачный воркер через RabbitMQ.
    Программа .tpu должна лежать в /auto/cache/<логин>/; хвост модели — ONNX на CPU, как в LinqTpuBackend."""

    name = "linq-tpu"

    def __init__(self, program_path: str, tail_onnx: str, input_name: str = "images"):
        import getpass
        import onnxruntime as ort
        # в кэше программы лежат по папкам (tpu — 640×640, tpu_v2 — 416×416), чтобы одноимённые версии не путались
        sub = os.path.basename(os.path.dirname(program_path))
        self.uri = f"file:///auto/cache/{getpass.getuser()}/{sub}/{os.path.basename(program_path)}?raw=False"
        self._cloud = _TpuCloud.get()
        opts = ort.SessionOptions()
        # хвост крошечный; пул потоков «на все ядра» в каждой из десятков сессий упирает контейнер в лимит процессов
        opts.intra_op_num_threads, opts.inter_op_num_threads = 1, 1
        self._tail = ort.InferenceSession(tail_onnx, sess_options=opts, providers=["CPUExecutionProvider"])
        self._tail_inputs = [i.name for i in self._tail.get_inputs()]
        self.input_name = input_name
        self.last_tpu_ms = 0.0

    def run(self, tensor: np.ndarray) -> np.ndarray:
        t0 = time.perf_counter()
        feats = self._cloud.submit(self.uri, {self.input_name: np.ascontiguousarray(tensor, np.float32)}).result(30)
        self.last_tpu_ms = (time.perf_counter() - t0) * 1e3
        return self._tail.run(None, {k: feats[k] for k in self._tail_inputs})[0]


class LinqTpuBackend:
    """Программа .tpu на ускорителе LinQ через pytpu; хвост модели (decode головы YOLO) — ONNX на CPU.

    Доступен только на стенде LinQ, где есть /dev/tpu*.
    """

    name = "linq-tpu"

    def __init__(self, program_path: str, tail_onnx: str, input_name: str = "images"):
        import onnxruntime as ort
        import pytpu as tpu

        devices = tpu.Device.list_devices()
        if not devices:
            raise RuntimeError("TPU не найден, проверьте ls /dev/tpu*")
        self._stack = contextlib.ExitStack()
        device = self._stack.enter_context(tpu.Device.open(devices[0]))
        program = self._stack.enter_context(device.load(program_path))
        self._inference = self._stack.enter_context(program.inference())
        self._tail = ort.InferenceSession(tail_onnx, providers=["CPUExecutionProvider"])
        self._tail_inputs = [i.name for i in self._tail.get_inputs()]
        self.input_name = input_name
        self.last_tpu_ms = 0.0

    def run(self, tensor: np.ndarray) -> np.ndarray:
        t0 = time.perf_counter()
        if _TPU_LOCK is not None:
            with _TPU_LOCK:
                feats = self._inference.sync({self.input_name: tensor})
        else:
            feats = self._inference.sync({self.input_name: tensor})
        self.last_tpu_ms = (time.perf_counter() - t0) * 1e3
        return self._tail.run(None, {k: feats[k] for k in self._tail_inputs})[0]

    def close(self) -> None:
        self._stack.close()
