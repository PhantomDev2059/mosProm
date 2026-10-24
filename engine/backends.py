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
        # у каждой сессии (камеры × модели) свой пул потоков, по умолчанию на все видимые ядра и с активным
        # ожиданием. В контейнере с квотой CPU меньше nproc (vast: 64 ядра видно, квота ~15; стенд LinQ — 1,5 ядра)
        # семь моделей — это сотни крутящихся потоков, и квота душит всё: кадр идёт в 20 раз медленнее
        threads = os.environ.get("LINQ_ORT_THREADS") or os.environ.get("LINQ_CPU_THREADS") or "2"
        opts.intra_op_num_threads = 2 if providers[0].startswith("CUDA") else int(threads)
        opts.inter_op_num_threads = 1
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self.session = ort.InferenceSession(path, sess_options=opts, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.name = "onnx-" + ("cuda" if self.session.get_providers()[0].startswith("CUDA") else "cpu")

    def run(self, tensor: np.ndarray) -> np.ndarray:
        return self.session.run(None, {self.input_name: tensor})[0]


_GATEWAY = None


def _gateway():
    """Пул процессов-шлюзов к TPU (engine/tpu_gateway.py), один на сервис."""
    global _GATEWAY
    if _GATEWAY is None:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor
        _GATEWAY = ProcessPoolExecutor(max_workers=int(os.environ.get("LINQ_TPU_PROCS", "12")),
                                       mp_context=multiprocessing.get_context("spawn"))
    return _GATEWAY


class LinqCloudBackend:
    """Стенд хакатона: TPU не виден как /dev/tpu, программы выполняет облачный воркер через RabbitMQ.
    Программа .tpu должна лежать в /auto/cache/<логин>/<папка>/; хвост модели — ONNX на CPU."""

    name = "linq-tpu"

    def __init__(self, program_path: str, tail_onnx: str, input_name: str = "images"):
        import getpass
        import onnxruntime as ort
        # в кэше программы лежат по папкам (tpu — 640×640, tpu_v2 — 416×416), чтобы одноимённые версии не путались
        sub = os.path.basename(os.path.dirname(program_path))
        self.uri = f"file:///auto/cache/{getpass.getuser()}/{sub}/{os.path.basename(program_path)}?raw=False"
        opts = ort.SessionOptions()
        # хвост крошечный; пул потоков «на все ядра» в каждой из десятков сессий упирает контейнер в лимит процессов
        opts.intra_op_num_threads, opts.inter_op_num_threads = 1, 1
        self._tail = ort.InferenceSession(tail_onnx, sess_options=opts, providers=["CPUExecutionProvider"])
        self._tail_inputs = [i.name for i in self._tail.get_inputs()]
        self.input_name = input_name
        self.last_tpu_ms = 0.0

    def run(self, tensor: np.ndarray) -> np.ndarray:
        from . import tpu_gateway
        t0 = time.perf_counter()
        feats = _gateway().submit(tpu_gateway.run, self.uri, self.input_name,
                                  np.ascontiguousarray(tensor, np.float32)).result(60)
        self.last_tpu_ms = (time.perf_counter() - t0) * 1e3
        return self._tail.run(None, {k: feats[k] for k in self._tail_inputs})[0]


class LinqFusedBackend:
    """Модель внутри склеенной программы TPU (linq/fuse_models.py): все модели камеры — один запрос на кадр.

    Первая модель кадра отправляет тензор, остальные ждут тот же ответ (ключ — сам объект тензора: конвейер
    готовит letterbox кадра один раз и отдаёт его всем моделям). Выходы модели в ответе — с префиксом «<имя>__»,
    дальше её обычный хвост на CPU."""

    name = "linq-tpu-fused"
    _pending: dict = {}
    _lock = threading.Lock()

    def __init__(self, program_path: str, member: str, n_members: int, tail_onnx: str, input_name: str = "images"):
        import getpass
        import onnxruntime as ort
        sub = os.path.basename(os.path.dirname(program_path))
        self.uri = f"file:///auto/cache/{getpass.getuser()}/{sub}/{os.path.basename(program_path)}?raw=False"
        self.prefix, self.n = f"{member}__", n_members
        opts = ort.SessionOptions()
        opts.intra_op_num_threads, opts.inter_op_num_threads = 1, 1
        self._tail = ort.InferenceSession(tail_onnx, sess_options=opts, providers=["CPUExecutionProvider"])
        self._tail_inputs = [i.name for i in self._tail.get_inputs()]
        self.input_name = input_name
        self.last_tpu_ms = 0.0

    def run(self, tensor: np.ndarray) -> np.ndarray:
        from . import tpu_gateway
        key = (self.uri, id(tensor))
        t0 = time.perf_counter()
        with self._lock:
            entry = self._pending.get(key)
            if entry is None:
                fut = _gateway().submit(tpu_gateway.run, self.uri, self.input_name, np.ascontiguousarray(tensor, np.float32))
                entry = self._pending[key] = [fut, self.n, tensor]     # тензор держим, чтобы id не переиспользовался
            entry[1] -= 1
            if entry[1] <= 0:
                del self._pending[key]
        feats = entry[0].result(60)
        self.last_tpu_ms = (time.perf_counter() - t0) * 1e3
        p = self.prefix
        return self._tail.run(None, {k: feats[p + k] for k in self._tail_inputs})[0]


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
