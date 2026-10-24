#!/bin/bash
# Разворотка LinQ Guard на стенде организаторов (Jupyter: без root, с интернетом).
#   nohup bash tools/stand_setup.sh > ~/work/linq/setup.log 2>&1 &
set -e
cd "$(dirname "$0")/.."; APP=$PWD
H=${LINQ_HOME:-$HOME/work/linq}; M=$H/models; D=$H/data
mkdir -p $M $D/videos/raw $D/videos/parts $H/bin
export PATH=$HOME/.local/bin:$H/bin:$PATH
PY=$H/.venv/bin/python; SPY=$H/sdk/.venv/bin/python
echo "== 1. окружение"
command -v uv >/dev/null || pip install --user -q uv
[ -x $PY ] || uv venv -q --python 3.12 $H/.venv
uv pip install -q --python $PY --index-url https://download.pytorch.org/whl/cpu torch torchvision
uv pip install -q --python $PY "numpy==1.26.4" opencv-python-headless onnxruntime fastapi "uvicorn[standard]" pyyaml pika \
  "protobuf>=4.21" httpx ultralytics onnx onnxslim huggingface_hub imageio-ffmpeg $HOME/work/packages/tpu_pycloud_format-0.0.1-py3-none-any.whl
ln -sf "$($PY -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')" $H/bin/ffmpeg
echo "== 2. SDK (фон)"
(
  mkdir -p $H/sdk && cd $H/sdk
  [ -d tpu_framework ] || { curl -sL -o fw.zip https://cloud.lin-q.ru/s/bEqr922zr6NXHy4/download && unzip -q -o fw.zip && unzip -q -o Framework/tpu_framework_2026.9.45.zip && rm -rf fw.zip Framework; }
  [ -x $SPY ] || uv venv -q --python 3.12 .venv
  uv pip install -q --python $SPY --no-deps tpu_framework/packets/*.whl
  uv pip install -q --python $SPY "numpy==1.26.4" "tensorflow==2.20.0" "keras==3.14.0" tf-keras "torch==2.9.1" "torchvision==0.24.1" \
    --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match \
    scipy dictdiffer click tabulate transformers "onnx~=1.16.0" onnxscript "onnxsim==0.6.5" onnx-graphsurgeon \
    onnxruntime-extensions onnxoptimizer rich networkx ordered-set frozendict humanize ml-dtypes setuptools simpy \
    sympy importlib-resources gmpy2 bitstruct parse "onnxruntime==1.29.0" "attrs==26.1.0" opencv-python-headless
  echo SDK_READY
) > $H/sdk.log 2>&1 &
SDK_PID=$!
echo "== 3. модели HF → ONNX"
if [ ! -f $M/registry.json ]; then
  cd $M
  $PY - <<'PY'
from huggingface_hub import hf_hub_download
for repo in ["Hansung-Cho/yolov8-ppe-detection", "keremberke/yolov8n-forklift-detection", "rabahdev/fire-smoke-yolov8n", "Beehzod/best_smoke_cigarette-detection"]:
    print(hf_hub_download(repo, "best.pt", local_dir="hf/" + repo.replace("/", "__")))
PY
  $PY $APP/tools/export_models.py --models $M
  $PY - <<PY
import json; p = "$M/registry.json"; r = json.load(open(p))
conf = {"pose": 0.4, "coco": 0.4, "ppe": 0.45, "forklift": 0.6, "fire": 0.3, "cigarette": 0.35}
for k, v in r.items(): v["conf"] = conf.get(k, 0.4)
json.dump(r, open(p, "w"), ensure_ascii=False, indent=1)
PY
  cd $APP
fi
echo MODELS_READY
echo "== 4. видео"
LINQ_DATA=$D bash tools/factory_videos.sh || echo "видео частично"
echo VIDEOS_READY
echo "== 5. компиляция под TPU"
wait $SDK_PID; tail -1 $H/sdk.log
mkdir -p $M/tpu /auto/cache/$(id -un)
for n in pose coco ppe forklift fire cigarette; do
  ls $M/tpu/${n}_b1_*_h1m.tpu >/dev/null 2>&1 || nice $SPY linq/compile_tpu.py --onnx $M/onnx/$n.onnx --name $n --videos $D/videos --out $M/tpu > $H/compile_$n.log 2>&1 &
done
wait
cp -f $M/tpu/*.tpu /auto/cache/$(id -un)/ && ls -la /auto/cache/$(id -un)
echo SETUP_DONE
