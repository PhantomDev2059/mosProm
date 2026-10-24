"""На стенде: хвосты моделей из уже скачанных там ONNX — по срезу из отчёта компиляции (<имя>_report.json).
Хвост из get_sub_model тянет все веса модели (десятки МБ), extract_model — только нужные (килобайты)."""
import json
from pathlib import Path

import onnx

M = Path.home() / "work/linq/models"
for rep in sorted((M / "tpu").glob("*_report.json")):
    r = json.loads(rep.read_text())
    name = r["name"]
    src = M / "onnx" / f"{name}.onnx"
    out = onnx.load(src).graph.output[0].name
    onnx.utils.extract_model(str(src), str(M / "tpu" / f"{name}_tail.onnx"), r["cut"], [out])
    print(name, "хвост:", (M / "tpu" / f"{name}_tail.onnx").stat().st_size, "байт")
