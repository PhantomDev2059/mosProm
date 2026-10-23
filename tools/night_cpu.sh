#!/bin/bash
# Ночная цепочка на CPU: классификатор падения и улучшенное квантование детекторов под LinQ.
# Переживает перезагрузки: этапы с отметками в $STATE. Программа supervisor night_cpu.
set -e
STATE=/workspace/linq/state
APP=/workspace/linq/app
mkdir -p $STATE
cd $APP
stage() { [ -f "$STATE/$1.done" ]; }
mark() { touch "$STATE/$1.done"; echo "== этап $1 готов $(date +%T)"; }

if ! stage fall_ml; then
  /workspace/linq/.venv/bin/python -c "import sklearn" 2>/dev/null || uv pip install --python /workspace/linq/.venv/bin/python scikit-learn
  /workspace/linq/.venv/bin/python eval/fall_ml.py && mark fall_ml
fi

PY=/workspace/linq/sdk/.venv/bin/python
for m in ppe forklift fire cigarette; do
  if ! stage quant2_$m; then
    $PY linq/compile_tpu.py --onnx /workspace/linq/models/onnx/$m.onnx --name $m --calib 120 --smart --finetune 2 \
      --out /workspace/linq/models/tpu_v2
    $PY linq/verify_quant.py --name $m --frames 140 --tpu-dir /workspace/linq/models/tpu_v2
    $PY linq/verify_quant.py --name $m --frames 140
    mark quant2_$m
  fi
done
echo NIGHT_CPU_DONE
