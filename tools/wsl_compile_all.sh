#!/bin/bash
# Экспорт и компиляция всех моделей в WSL (SDK стоит в ~/linq/sdk), результат — для заливки на стенд.
set -e
cd /mnt/d/dev/mosprom-guard
M=/mnt/d/dev/mosprom/models/guard; mkdir -p $M
~/linq/export/bin/pip install -q huggingface_hub
cd $M
[ -f registry.json ] || { ~/linq/export/bin/python - <<'PY'
from huggingface_hub import hf_hub_download
for repo in ["Hansung-Cho/yolov8-ppe-detection", "keremberke/yolov8n-forklift-detection", "rabahdev/fire-smoke-yolov8n", "Beehzod/best_smoke_cigarette-detection"]:
    print(hf_hub_download(repo, "best.pt", local_dir="hf/" + repo.replace("/", "__")))
PY
cp -n ../yolov8n-pose.pt ../yolov8s.pt . 2>/dev/null || true
~/linq/export/bin/python /mnt/d/dev/mosprom-guard/tools/export_models.py --models $M; }
cd /mnt/d/dev/mosprom-guard
for n in pose coco ppe forklift fire cigarette; do
  ls $M/tpu/${n}_b1_*_h1m.tpu >/dev/null 2>&1 || TF_CPP_MIN_LOG_LEVEL=2 ~/linq/sdk/bin/python linq/compile_tpu.py \
    --onnx $M/onnx/$n.onnx --name $n --videos /mnt/d/dev/mosprom/app/static/samples --out $M/tpu > $M/compile_$n.log 2>&1 &
done
wait
ls -la $M/tpu/*.tpu $M/tpu/*_tail.onnx
echo ALL_COMPILED
