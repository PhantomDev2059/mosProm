#!/usr/bin/env bash
# Экспорт YOLOv8n (COCO) в ONNX с фиксированным входом. Запуск: wsl -e bash compile/export_yolo.sh [imgsz]
set -euo pipefail
IMGSZ=${1:-640}
VENV=~/linq/export
OUT=/mnt/d/dev/mosprom/models

mkdir -p ~/linq "$OUT"
[ -d "$VENV" ] || python3.12 -m venv "$VENV"
"$VENV/bin/pip" install -q -U pip
"$VENV/bin/pip" install -q torch torchvision --index-url https://download.pytorch.org/whl/cpu
"$VENV/bin/pip" install -q ultralytics "onnx>=1.16,<1.18" onnxslim
cd "$OUT"
"$VENV/bin/yolo" export model=yolov8n.pt format=onnx imgsz="$IMGSZ" opset=13 dynamic=False simplify=True batch=1
mv -f yolov8n.onnx "yolov8n_${IMGSZ}.onnx"
ls -la "$OUT"
