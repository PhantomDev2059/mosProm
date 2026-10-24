#!/bin/bash
set -e
SRC=/mnt/d/dev/mosprom/models/guard; M=/mnt/d/dev/mosprom/models/guard416
cd $M/onnx && ~/linq/export/bin/python -c "
from ultralytics import YOLO; import shutil
shutil.move(YOLO('$SRC/hf/Beehzod__best_smoke_cigarette-detection/best.pt').export(format='onnx', imgsz=416, opset=12, simplify=True, dynamic=False), '$M/onnx/cigarette.onnx')"
cd /mnt/d/dev/mosprom-guard
TF_CPP_MIN_LOG_LEVEL=2 ~/linq/sdk/bin/python linq/compile_tpu.py --onnx $M/onnx/cigarette.onnx --name cigarette --videos /mnt/d/dev/mosprom/app/static/samples --out $M/tpu > $M/compile_cigarette.log 2>&1 || { tail -20 $M/compile_cigarette.log; exit 1; }
~/linq/export/bin/python -c "
import json, onnx
r = json.load(open('$M/tpu/cigarette_report.json')); src = '$M/onnx/cigarette.onnx'
onnx.utils.extract_model(src, '$M/tpu/cigarette_tail.onnx', r['cut'], [onnx.load(src).graph.output[0].name])"
ls -la $M/tpu/cigarette*; echo CIG_DONE
