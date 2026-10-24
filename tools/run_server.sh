#!/bin/bash
# Запуск веб-сервиса на vast-сервере. Порт 8000, доступ через ssh -L 8000:localhost:8000.
cd "$(dirname "$0")/.."
export TZ=Europe/Moscow
export LINQ_DATA=${LINQ_DATA:-/workspace/linq/data}
export LINQ_MODELS=${LINQ_MODELS:-/workspace/linq/models}
# на vast nproc показывает все ядра машины, а квота контейнера меньше (у нынешнего ~17 из 72):
# пулы потоков «на все ядра» в каждой из десятков сессий только мешают друг другу
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
exec /workspace/linq/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000 --log-level warning
