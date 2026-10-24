#!/bin/bash
# Запуск на стенде: nohup setsid bash tools/stand_run.sh > ~/work/linq/server.log 2>&1 &
# Открывать с ноутбука (VPN + вход в Jupyter): http://10.0.114.30/user/<логин>/proxy/8765/
cd "$(dirname "$0")/.."
H=${LINQ_HOME:-$HOME/work/linq}
M=${LINQ_MODELS_DIR:-$H/models_misha}   # модели, пороги и программы TPU Миши (релиз v1.0.0)
export TZ=Europe/Moscow LINQ_DATA=$H/data LINQ_MODELS=$M LINQ_BACKEND=${LINQ_BACKEND:-tpu}
export LINQ_BASE=/user/$(id -un)/proxy/8765 PATH=$H/bin:$PATH
export LINQ_CPU_MODELS=${LINQ_CPU_MODELS:-ppe2} LINQ_REPLAY=${LINQ_REPLAY:-1} LINQ_INFER_WORKERS=48 LINQ_FRAMES_INFLIGHT=3 LINQ_TPU_PROCS=12 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
# UR Fall со стенда не скачивается — коридору пока ролик зала
V=$LINQ_DATA/videos; [ -s $V/cam_corridor.mp4 ] || cp -f $V/cam_hall.mp4 $V/cam_corridor.mp4 2>/dev/null
exec $H/.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8765 --log-level warning
