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
        self.session = ort.InferenceSession(path, sess_options=opts, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.name = "onnx-" + ("cuda" if self.session.get_providers()[0].startswith("CUDA") else "cpu")

    def run(self, tensor: np.ndarray) -> np.ndarray:
        return self.session.run(None, {self.input_name: tensor})[0]


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
