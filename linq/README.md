# Перенос на ускоритель LinQ

## 1. Компиляция (Linux x86-64, Python 3.12.11, SDK tpu_framework 2026.9.45)

```bash
uv venv --python 3.12.11 sdk/.venv && . sdk/.venv/bin/activate
cd tpu_framework/packets && uv pip install --no-deps *.whl          # пакеты SDK
uv pip install "numpy==1.26.4" "tensorflow==2.20.0" "keras==3.14.0" tf-keras "torch==2.9.1" "torchvision==0.24.1" \
  --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match \
  scipy dictdiffer click tabulate transformers "onnx~=1.16.0" onnxscript "onnxsim==0.6.5" onnx-graphsurgeon \
  onnxruntime-extensions onnxoptimizer rich networkx ordered-set frozendict humanize ml-dtypes setuptools simpy \
  sympy importlib-resources gmpy2 bitstruct parse "onnxruntime==1.29.0" "attrs==26.1.0" opencv-python-headless
```

Прямая установка `pip install -r requirements.txt` у нас зависала на разрешении версий, поэтому пакеты SDK
ставятся без зависимостей, а зависимости — явным списком из их метаданных.

```bash
python linq/compile_tpu.py --onnx models/onnx/pose.onnx --name pose --calib 70   # срез, калибровка, квантование, O5
python linq/verify_quant.py --name pose --kind pose --frames 70                  # расхождение с FP32
```

Результат в `models/tpu/`: `<имя>_b1_o5_h1m.tpu` (программа TPU), `<имя>_tail.onnx` (хвост для CPU),
`<имя>_report.json` (срез, время, размер, проверка квантования).

## 2. Запуск на стенде

Скопировать `api/`, `engine/`, `configs/`, `models/registry.json`, `models/onnx/` и `models/tpu/` в Jupyter стенда
(программы — в `/auto/cache/<логин>`), затем:

```bash
LINQ_BACKEND=tpu tools/run_server.sh
```

Сервис загрузит для каждой модели `LinqTpuBackend`: `pytpu.Device → load(.tpu) → inference().sync`,
хвост — onnxruntime на CPU, дальше тот же постпроцессинг и логика событий. Экран «LinQ» покажет задержку TPU.
