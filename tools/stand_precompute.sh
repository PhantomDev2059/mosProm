#!/bin/bash
# Прогон всех демо-камер через TPU стенда (сервис на это время останавливается, чтобы не делить очередь).
cd "$(dirname "$0")/.."
H=${LINQ_HOME:-$HOME/work/linq}
export LINQ_DATA=$H/data LINQ_MODELS=${LINQ_MODELS_DIR:-$H/models_misha} LINQ_BACKEND=tpu LINQ_TPU_PROCS=16 PRECOMPUTE_INFLIGHT=16
export LINQ_CPU_MODELS=${LINQ_CPU_MODELS:-ppe2} LINQ_CPU_THREADS=2 LINQ_INFER_WORKERS=64 OMP_NUM_THREADS=1
pkill -f "[u]vicorn api.main"
$H/.venv/bin/python tools/precompute_tpu.py "$@"
echo PRECOMPUTE_DONE
