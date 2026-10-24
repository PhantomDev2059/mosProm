"""Детекции кадра ↔ JSON: заранее посчитанные на TPU LinQ результаты для плавного воспроизведения демо-камер."""
import json
from pathlib import Path

import numpy as np

from .types import Detection


def _plain(v):
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def to_json(d: Detection) -> dict:
    out = {"b": [round(float(x), 1) for x in d.box], "s": round(float(d.score), 4), "c": d.cls}
    if d.keypoints is not None:
        out["k"] = np.round(np.asarray(d.keypoints, np.float32), 2).tolist()
    if d.attrs:
        out["a"] = _plain(d.attrs)
    return out


def from_json(o: dict) -> Detection:
    kp = o.get("k")
    return Detection(np.asarray(o["b"], np.float32), float(o["s"]), o["c"],
                     None if kp is None else np.asarray(kp, np.float32), dict(o.get("a", {})))


def replay_path(data_dir: Path, camera_id: str) -> Path:
    return Path(data_dir) / "dets" / f"{camera_id}.jsonl"


def load_replay(path: Path, source: str, models: list[str]) -> dict[int, list[dict]] | None:
    """{номер кадра: детекции в JSON}, если файл посчитан для того же ролика и того же набора моделей.
    Detection создаются заново на каждом проходе ролика (from_json): трекер хранит их в истории."""
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        head = json.loads(f.readline())
        if head.get("source") != source or head.get("models") != models:
            return None
        return {int(row["i"]): row["d"] for row in map(json.loads, f)}
