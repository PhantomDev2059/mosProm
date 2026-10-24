#!/bin/bash
# Запуск на стенде: nohup setsid bash tools/stand_run.sh > ~/work/linq/server.log 2>&1 &
# Открывать с ноутбука (VPN + вход в Jupyter): http://10.0.114.30/user/<логин>/proxy/8765/
cd "$(dirname "$0")/.."
H=${LINQ_HOME:-$HOME/work/linq}
export TZ=Europe/Moscow LINQ_DATA=$H/data LINQ_MODELS=$H/models LINQ_BACKEND=${LINQ_BACKEND:-tpu}
export LINQ_BASE=/user/$(id -un)/proxy/8765 PATH=$H/bin:$PATH
export LINQ_INFER_WORKERS=24 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
# пресет ppe2 (дообученные каски) живёт на vast; пока его нет — камеры берут открытую модель СИЗ
ls $LINQ_MODELS/tpu/ppe2_* >/dev/null 2>&1 || sed -i 's/ppe2, ppe\]/ppe]/; s/\bppe2\b/ppe/g' configs/cameras/*.yaml
# UR Fall со стенда не скачивается — коридору пока ролик зала
V=$LINQ_DATA/videos; [ -s $V/cam_corridor.mp4 ] || cp -f $V/cam_hall.mp4 $V/cam_corridor.mp4 2>/dev/null
exec $H/.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8765 --log-level warning
