#!/usr/bin/env bash
# Окружение LinQ TPU SDK в WSL (Ubuntu 24.04, python3.12). Запуск: wsl -e bash compile/setup_sdk.sh
set -euo pipefail
PACKETS=/mnt/d/dev/mosprom/org_materials/tpu_framework/packets
VENV=~/linq/sdk

mkdir -p ~/linq
[ -d "$VENV" ] || python3.12 -m venv "$VENV"
"$VENV/bin/pip" install -q -U pip
# CPU-сборки torch: SDK пинит torch==2.9.1/torchvision==0.24.1, CUDA-версии нам не нужны
"$VENV/bin/pip" install -q torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cpu
cd "$PACKETS"
"$VENV/bin/pip" install -q -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu
"$VENV/bin/pip" install -q opencv-python-headless pillow
"$VENV/bin/python" -c "import tpu_framework; print('SDK OK', tpu_framework.__file__)"
