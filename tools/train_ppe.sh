#!/bin/bash
# Дообучение детектора СИЗ (каска / голова без каски / жилет) на jhboyo/ppe-dataset (MIT, 15,5 тыс. изображений).
# YOLOv8s — архитектура из семейства, которое поддерживает SDK LinQ; вход 640×640 как у остальных моделей.
set -e
ROOT=/workspace/linq/train
DATA=$ROOT/ppe-dataset
while pgrep -f snapshot_download >/dev/null; do sleep 30; done      # ждём окончания загрузки
cat > $ROOT/ppe.yaml <<EOF
path: $DATA
train: train/images
val: val/images
test: test/images
names: {0: helmet, 1: head, 2: vest}
EOF
. /workspace/linq/.venv/bin/activate
cd $ROOT
yolo detect train model=yolov8s.pt data=$ROOT/ppe.yaml epochs=40 imgsz=640 batch=16 workers=6 device=0 \
  project=$ROOT name=ppe_v8s exist_ok=True cos_lr=True patience=15 plots=True
yolo detect val model=$ROOT/ppe_v8s/weights/best.pt data=$ROOT/ppe.yaml split=test imgsz=640 batch=16 \
  project=$ROOT name=ppe_v8s_test exist_ok=True
echo TRAIN_DONE
