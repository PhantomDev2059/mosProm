#!/bin/bash
# Перекомпиляция 416 с настройками Миши: 120 кадров калибровки с камер стенда, --smart, --finetune 2 (кроме pose).
set -e
G=/mnt/d/dev/mosprom-guard; M=/mnt/d/dev/mosprom/models/guard416; V=$M/calib_videos
mkdir -p $M/tpu_tuned
cd $G
run() { local extra="--smart --finetune 2"; [ $1 = pose ] && extra=""
  TF_CPP_MIN_LOG_LEVEL=2 OMP_NUM_THREADS=6 ~/linq/sdk/bin/python linq/compile_tpu.py --onnx $M/onnx/$1.onnx --name $1 \
    --videos $V --out $M/tpu_tuned --calib 120 $extra > $M/tuned_$1.log 2>&1 && echo "$1 готова" || echo "$1 ОШИБКА"; }
run pose & run ppe2 & run fire & wait
run coco & run ppe & run forklift & wait
~/linq/export/bin/python - <<PY
import json, onnx
from pathlib import Path
M = Path("$M")
for rep in sorted((M / "tpu_tuned").glob("*_report.json")):
    r = json.loads(rep.read_text()); n = r["name"]; src = M / "onnx" / f"{n}.onnx"
    if (M / "tpu_tuned" / f"{n}_b1_o5_h1m.tpu").exists() or list((M / "tpu_tuned").glob(f"{n}_b1_*_h1m.tpu")):
        onnx.utils.extract_model(str(src), str(M / "tpu_tuned" / f"{n}_tail.onnx"), r["cut"], [onnx.load(src).graph.output[0].name])
PY
ls -la $M/tpu_tuned/*.tpu; echo TUNED_DONE
