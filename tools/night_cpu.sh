#!/bin/bash
# Ночная цепочка: оценка падений на UR Fall, компиляция всех моделей под LinQ и проверка квантования,
# улучшенное квантование детекторов, замеры скорости. Переживает перезагрузки: этапы с отметками в $STATE.
set -e
W=/workspace/linq
STATE=$W/state
APP=$W/app
mkdir -p $STATE
cd $APP
stage() { [ -f "$STATE/$1.done" ]; }
mark() { touch "$STATE/$1.done"; echo "== этап $1 готов $(date +%T)"; }
wait_sdk() { until $W/sdk/.venv/bin/python -c "import tpu_framework" >/dev/null 2>&1; do sleep 60; done; }
APY=$W/.venv/bin/python
# живые камеры важнее: квота CPU контейнера меньше nproc, фоновые задачи — с низшим приоритетом (nice 19)
SPY=$W/sdk/.venv/bin/python

if ! stage urfall_data; then
  D=$W/data/eval/urfall; mkdir -p $D; B=https://fenix.ur.edu.pl/~mkepski/ds/data
  curl -s -f -o $D/urfall-cam0-falls.csv $B/urfall-cam0-falls.csv
  for i in $(seq -w 1 30); do [ -s $D/fall-$i-cam0.mp4 ] || curl -s -f -o $D/fall-$i-cam0.mp4 $B/fall-$i-cam0.mp4; done
  for i in $(seq -w 1 40); do [ -s $D/adl-$i-cam0.mp4 ] || curl -s -f -o $D/adl-$i-cam0.mp4 $B/adl-$i-cam0.mp4; done
  [ "$(ls $D/*.mp4 | wc -l)" -ge 70 ] && mark urfall_data
fi
if ! stage sweep; then nice -n 19 $APY eval/sweep_fall.py && mark sweep; fi
if ! stage fall_ml; then nice -n 19 $APY eval/fall_ml.py && mark fall_ml; fi

wait_sdk
for m in pose ppe coco forklift fire cigarette; do
  if ! stage compile_$m; then
    nice -n 19 $SPY linq/compile_tpu.py --onnx $W/models/onnx/$m.onnx --name $m --calib 120 $([ $m != pose ] && echo --smart)
    if [ $m = pose ]; then nice -n 19 $SPY linq/verify_quant.py --name pose --kind pose --frames 140; else nice -n 19 $SPY linq/verify_quant.py --name $m --frames 140; fi
    mark compile_$m
  fi
done
for m in ppe forklift fire cigarette; do
  if ! stage quant2_$m; then
    nice -n 19 $SPY linq/compile_tpu.py --onnx $W/models/onnx/$m.onnx --name $m --calib 120 --smart --finetune 2 --out $W/models/tpu_v2
    nice -n 19 $SPY linq/verify_quant.py --name $m --frames 140 --tpu-dir $W/models/tpu_v2
    mark quant2_$m
  fi
done
if ! stage bench; then $APY linq/bench.py --backend cuda cpu --runs 150 && mark bench; fi
echo NIGHT_CPU_DONE
