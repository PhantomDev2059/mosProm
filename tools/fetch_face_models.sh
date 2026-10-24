#!/bin/bash
# Модели лиц из opencv_zoo (детектор YuNet, распознаватель SFace; лицензии MIT / Apache-2.0).
# Кладёт в ${LINQ_MODELS}/faces/, уже скачанные пропускает.
set -euo pipefail
DIR="${LINQ_MODELS:-/workspace/linq/models}/faces"
ZOO=https://github.com/opencv/opencv_zoo/raw/main/models
mkdir -p "$DIR"
for path in face_detection_yunet/face_detection_yunet_2023mar.onnx \
            face_recognition_sface/face_recognition_sface_2021dec.onnx; do
    f="$DIR/$(basename "$path")"
    if [ -s "$f" ]; then
        echo "есть: $f"
        continue
    fi
    curl -L -f --retry 3 -o "$f.part" "$ZOO/$path"
    mv "$f.part" "$f"
    echo "скачано: $f ($(du -h "$f" | cut -f1))"
done
