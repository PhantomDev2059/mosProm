"""Сырой режим TPU LinQ (?raw=True): данные в формате ускорителя, а не float32.

Внутри программы вход и выходы — int8 в раскладке TPU. В обычном режиме воркер стенда получает float32
(кадр 640×640 — 4,9 МБ) и сам переводит его в int8; здесь это делаем мы кодеком SDK (tpu_io), и через очередь
идёт в 4 раза меньше данных в обе стороны. Описания тензоров берутся из metadata.json внутри файла .tpu.
"""
import json
import zipfile
from functools import lru_cache

import numpy as np


@lru_cache(maxsize=None)
def program_io(program_path: str):
    import tpu_io  # noqa: F401  — проверяем, что кодек есть
    from tpu_tlm_is.base import TensorDescription
    from tpu_tlm_is.base import number_types as nt

    meta = json.loads(zipfile.ZipFile(program_path).read("metadata.json"))

    def mask(d):
        """Маска формы: True — выравнивание. Раскладка TPU — (C-блоки, H, W, N, B): каналы дополняются до блока
        (кадр 3 → 4, 51 → 64), у одноканальных выходов — ширина (20 → 32). Длины осей с выравниванием берём из
        tpu_shape: C = блоки × B, остальные — как есть."""
        tpu = dict(zip(d["tpu_order"], d["tpu_shape"]))
        padded = {a: (tpu["C"] * tpu.get("B", 1) if a == "C" else tpu.get(a, 1)) for a in d["user_order"]}
        return tuple(tuple([False] * s + [True] * max(0, padded[a] - s))
                     for a, s in zip(d["user_order"], d["user_shape"]))

    def desc(d):
        return TensorDescription(
            user_shape_mask=mask(d), user_order=tuple(d["user_order"]),
            user_dtype=nt.STR_TO_USER_NUMBER_TYPE[d["user_dtype"]], tpu_shape=tuple(d["tpu_shape"]),
            tpu_order=tuple(d["tpu_order"]), tpu_dtype=nt.STR_TO_TPU_NUMBER_TYPE[d["tpu_dtype"]],
            scales=tuple(d["scales"]), scale_shape=tuple(d["scale_shape"]), zero_points=tuple(d["zero_points"]),
            anchor=d["anchor"])

    hw = meta["hardware_parameters"]
    ins = {k: desc(v) for group in meta["inputs"].values() for k, v in group.items()}
    outs = {k: desc(v) for group in meta["outputs"].values() for k, v in group.items()}
    return hw["cache_word_len"], hw["ddr_word_len"], ins, outs


def encode(program_path: str, tensors: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    import tpu_io
    cwl, ddr, ins, _ = program_io(program_path)
    return tpu_io.pack_mapping(tensors, cwl, ddr, ins)


def decode(program_path: str, raw: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    import tpu_io
    cwl, _, _, outs = program_io(program_path)
    return tpu_io.unpack_mapping(raw, cwl, outs)
