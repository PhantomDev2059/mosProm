#!/bin/bash
# Ночная цепочка на GPU, переживает перезагрузки инстанса: каждый этап оставляет отметку в $STATE,
# обучение продолжается с последней эпохи. Запускается supervisor-ом (программа night_gpu).
set -e
ROOT=/workspace/linq/train
STATE=/workspace/linq/state
APP=/workspace/linq/app
mkdir -p $STATE
. /workspace/linq/.venv/bin/activate
cd $ROOT
stage() { [ -f "$STATE/$1.done" ]; }
mark() { touch "$STATE/$1.done"; echo "== этап $1 готов $(date +%T)"; }

if ! stage fetch; then
  python $APP/tools/fetch_ppe.py 8 | tee $ROOT/fetch.log
  grep -q "ошибок 0" $ROOT/fetch.log && mark fetch
fi
cat > $ROOT/ppe.yaml <<YAML
path: $ROOT/ppe
train: train/images
val: val/images
test: test/images
names: {0: helmet, 1: head, 2: vest}
YAML

if ! stage train; then
  if [ -f $ROOT/ppe_v8s/weights/last.pt ]; then
    yolo detect train resume model=$ROOT/ppe_v8s/weights/last.pt
  else
    yolo detect train model=yolov8s.pt data=$ROOT/ppe.yaml epochs=40 imgsz=640 batch=16 workers=6 device=0 \
      project=$ROOT name=ppe_v8s exist_ok=True cos_lr=True patience=15 plots=True
  fi
  mark train
fi

if ! stage eval; then
  python $APP/tools/eval_ppe.py && mark eval
fi

if ! stage export; then
  python - <<PY
from ultralytics import YOLO
import shutil
p = YOLO("$ROOT/ppe_v8s/weights/best.pt").export(format="onnx", imgsz=640, opset=12, simplify=True, dynamic=False)
shutil.copy(p, "/workspace/linq/models/onnx/ppe2.onnx")
PY
  python - <<PY
import json
p = "/workspace/linq/models/registry.json"; r = json.load(open(p))
r["ppe2"] = {"kind": "detect", "path": "/workspace/linq/models/onnx/ppe2.onnx", "source": "ppe_v8s (jhboyo/ppe-dataset)",
             "names": {"0": "helmet", "1": "head", "2": "vest"}, "class_map": {"0": "helmet", "1": "head", "2": "vest"}, "conf": 0.45}
json.dump(r, open(p, "w"), ensure_ascii=False, indent=1)
PY
  mark export
fi

if ! stage compile_ppe2; then
  PY=/workspace/linq/sdk/.venv/bin/python
  cd $APP
  $PY linq/compile_tpu.py --onnx /workspace/linq/models/onnx/ppe2.onnx --name ppe2 --calib 120 --smart
  $PY linq/verify_quant.py --name ppe2 --frames 140
  mark compile_ppe2
fi
echo NIGHT_GPU_DONE
