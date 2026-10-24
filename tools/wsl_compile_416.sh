#!/bin/bash
# Модели на вход 416×416 (в 2,4 раза меньше данных через очередь стенда) + хвосты. Запуск в WSL.
set -e
G=/mnt/d/dev/mosprom-guard; SRC=/mnt/d/dev/mosprom/models/guard; M=/mnt/d/dev/mosprom/models/guard416
mkdir -p $M/onnx $M/tpu
cp -n /mnt/d/dowload/kaski_yolov8s_best.pt $SRC/kaski.pt
cd $M/onnx
~/linq/export/bin/python - <<PY
from ultralytics import YOLO
import shutil
src = {"pose": "$SRC/yolov8n-pose.pt", "coco": "$SRC/yolov8s.pt", "ppe": "$SRC/hf/Hansung-Cho__yolov8-ppe-detection/best.pt",
       "forklift": "$SRC/hf/keremberke__yolov8n-forklift-detection/best.pt", "fire": "$SRC/hf/rabahdev__fire-smoke-yolov8n/best.pt",
       "ppe2": "$SRC/kaski.pt"}
for name, p in src.items():
    out = YOLO(p).export(format="onnx", imgsz=416, opset=12, simplify=True, dynamic=False)
    shutil.move(out, f"$M/onnx/{name}.onnx"); print(name, "ok")
PY
cd $G
run() { TF_CPP_MIN_LOG_LEVEL=2 OMP_NUM_THREADS=6 ~/linq/sdk/bin/python linq/compile_tpu.py --onnx $M/onnx/$1.onnx --name $1 \
          --videos /mnt/d/dev/mosprom/app/static/samples --out $M/tpu > $M/compile_$1.log 2>&1 && echo "$1 скомпилирована"; }
run pose & run ppe2 & run fire & wait
run coco & run ppe & run forklift & wait
~/linq/export/bin/python - <<PY
import json, onnx
from pathlib import Path
M = Path("$M")
for rep in sorted((M / "tpu").glob("*_report.json")):
    r = json.loads(rep.read_text()); n = r["name"]; src = M / "onnx" / f"{n}.onnx"
    onnx.utils.extract_model(str(src), str(M / "tpu" / f"{n}_tail.onnx"), r["cut"], [onnx.load(src).graph.output[0].name])
    print(n, "хвост", (M / "tpu" / f"{n}_tail.onnx").stat().st_size)
PY
ls -la $M/tpu/*.tpu
echo ALL416_DONE
