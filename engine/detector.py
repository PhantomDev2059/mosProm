import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from .backends import InferenceBackend
from .postprocess import decode, letterbox
from .types import Detection


@dataclass
class ModelSpec:
    name: str
    kind: str                       # detect | pose
    path: str                       # .onnx (или .tpu вместе с хвостом на стенде LinQ)
    class_map: dict[int, str]       # индекс класса модели → канонический класс
    conf: float = 0.35
    imgsz: int = 640


class Detector:
    """Модель + замеры: время препроцессинга, инференса и постпроцессинга по каждому вызову."""

    def __init__(self, spec: ModelSpec, backend: InferenceBackend):
        self.spec = spec
        self.backend = backend
        self.timings = {k: deque(maxlen=300) for k in ("pre", "infer", "post")}

    def __call__(self, frame: np.ndarray) -> list[Detection]:
        t0 = time.perf_counter()
        tensor, ratio, pad = letterbox(frame, self.spec.imgsz)
        t1 = time.perf_counter()
        out = self.backend.run(tensor)
        t2 = time.perf_counter()
        dets = decode(out, self.spec.kind, self.spec.class_map, ratio, pad, frame.shape[:2], conf=self.spec.conf)
        t3 = time.perf_counter()
        self.timings["pre"].append((t1 - t0) * 1e3)
        self.timings["infer"].append((t2 - t1) * 1e3)
        self.timings["post"].append((t3 - t2) * 1e3)
        return dets

    def stats(self) -> dict:
        res = {"model": self.spec.name, "backend": self.backend.name}
        for k, v in self.timings.items():
            if v:
                arr = np.asarray(v)
                res[k] = {"p50": round(float(np.percentile(arr, 50)), 2), "p95": round(float(np.percentile(arr, 95)), 2)}
        return res
