#!/bin/bash
# Полная разворотка LinQ Guard на чистом vast-инстансе (Ubuntu, CUDA, supervisor).
# С ноутбука:  rsync -az --exclude .venv --exclude .git ./ root@HOST:/workspace/linq/app/ -e "ssh -p PORT"
#              ssh -p PORT root@HOST 'bash /workspace/linq/app/tools/bootstrap_vast.sh'
# Идемпотентен: повторный запуск пропускает готовое.
set -e
W=/workspace/linq
APP=$W/app
mkdir -p $W/models $W/data/videos/raw $W/data/videos/parts $W/state
cd $W

echo "== 1. окружение"
if [ ! -x $W/.venv/bin/python ]; then
  uv venv --python 3.12 $W/.venv
  . $W/.venv/bin/activate
  uv pip install --index-url https://download.pytorch.org/whl/cu124 torch torchvision
  uv pip install ultralytics onnx onnxslim "onnxruntime-gpu==1.22.0" opencv-python-headless fastapi "uvicorn[standard]" \
    pyyaml pytest httpx huggingface_hub scikit-learn
fi
. $W/.venv/bin/activate

echo "== 2. модели с Hugging Face и экспорт в ONNX"
if [ ! -f $W/models/registry.json ]; then
  cd $W/models
  python - <<'PY'
from huggingface_hub import hf_hub_download
for repo, f in [("Hansung-Cho/yolov8-ppe-detection", "best.pt"), ("keremberke/yolov8n-forklift-detection", "best.pt"),
                ("rabahdev/fire-smoke-yolov8n", "best.pt"), ("Beehzod/best_smoke_cigarette-detection", "best.pt")]:
    print(hf_hub_download(repo, f, local_dir="hf/" + repo.replace("/", "__")))
PY
  python $APP/tools/export_models.py --models $W/models
  python - <<'PY'
import json
p = "/workspace/linq/models/registry.json"; r = json.load(open(p))
conf = {"pose": 0.4, "coco": 0.4, "ppe": 0.45, "forklift": 0.6, "fire": 0.3, "cigarette": 0.35}
for k, v in r.items(): v["conf"] = conf.get(k, 0.4)
json.dump(r, open(p, "w"), ensure_ascii=False, indent=1)
PY
  cd $W
fi

echo "== 3. демо-видео (Mixkit, бесплатная лицензия) и падения UR Fall"
cd $W/data/videos
if [ ! -f smena_23-10_demo.mp4 ]; then
  while read id name; do
    [ -s raw/mixkit_$name.mp4 ] || curl -s -f -m 120 -o raw/mixkit_$name.mp4 https://assets.mixkit.co/videos/$id/$id-720.mp4 || echo "FAIL $id"
  done <<'LIST'
46753 construction_workers_helmets
31473 builders_working_site
1459 construction_workers_house
1436 two_construction_workers
23170 workers_blueprint_site
45848 forklift_warehouse
24127 worker_driving_warehouse
23789 loading_vehicle_operator
23551 man_walking_warehouse
23237 people_walking_warehouse
22032 engineers_factory
23378 worker_machine_corridor
47755 welding_factory
27696 welding_yard
23814 man_falls_snow
41764 running_hospital_hall
32809 man_runs_past
398 running_stairs_smoking
4919 phone_serving_coffee
4805 businessman_phone
41165 man_answering_phone
41354 woman_lighting_cigarette
39476 man_smoking_street
22587 building_burning
1243 wood_outdoor_fire
LIST
  for id in 22033 21795 20770 23550 23010 23406 2244 14785 21950 23107; do
    [ -s raw/normal_$id.mp4 ] || curl -s -f -m 120 -o raw/normal_$id.mp4 https://assets.mixkit.co/videos/$id/$id-720.mp4 || true
  done
  for i in 01 02 03 04 05 06 07 08; do
    [ -s raw/urfall_$i.mp4 ] || curl -s -f -m 120 -o raw/urfall_$i.mp4 https://fenix.ur.edu.pl/~mkepski/ds/data/fall-$i-cam0.mp4
  done
  norm() { ffmpeg -loglevel error -y -i "raw/$1" -an -vf "${3}scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=25,setsar=1" -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p "parts/$2"; }
  cam() { name=$1; shift; : > parts/$name.txt; for p in "$@"; do echo "file $p" >> parts/$name.txt; done; ffmpeg -loglevel error -y -f concat -safe 0 -i parts/$name.txt -c copy "$name.mp4"; }
  for f in raw/mixkit_*.mp4 raw/normal_*.mp4; do [ -s "$f" ] && norm $(basename $f) $(basename $f); done
  for i in 01 02 03 04 05 06 07 08; do norm urfall_$i.mp4 urfall_$i.mp4 "crop=320:240:320:0,"; done
  cam cam_site mixkit_construction_workers_house.mp4 mixkit_two_construction_workers.mp4 mixkit_workers_blueprint_site.mp4 mixkit_builders_working_site.mp4 mixkit_construction_workers_helmets.mp4
  cam cam_warehouse mixkit_forklift_warehouse.mp4 mixkit_worker_driving_warehouse.mp4 mixkit_loading_vehicle_operator.mp4 mixkit_man_walking_warehouse.mp4
  cam cam_shop mixkit_people_walking_warehouse.mp4 mixkit_engineers_factory.mp4 mixkit_worker_machine_corridor.mp4 mixkit_welding_factory.mp4
  cam cam_corridor urfall_01.mp4 urfall_02.mp4 urfall_03.mp4 urfall_04.mp4 urfall_05.mp4 urfall_06.mp4 urfall_07.mp4 urfall_08.mp4 mixkit_man_falls_snow.mp4
  cam cam_smoking mixkit_woman_lighting_cigarette.mp4 mixkit_man_smoking_street.mp4 mixkit_running_stairs_smoking.mp4
  cam cam_fire mixkit_building_burning.mp4 mixkit_wood_outdoor_fire.mp4 mixkit_welding_yard.mp4
  cam cam_hall mixkit_running_hospital_hall.mp4 mixkit_man_answering_phone.mp4 mixkit_man_runs_past.mp4 mixkit_businessman_phone.mp4 mixkit_phone_serving_coffee.mp4
  for c in cam_site cam_warehouse cam_shop cam_corridor cam_smoking cam_fire cam_hall; do cp $c.mp4 parts/; done
  cam smena_23-10_demo normal_22033.mp4 cam_site.mp4 normal_21795.mp4 normal_20770.mp4 cam_warehouse.mp4 normal_23550.mp4 normal_2244.mp4 cam_shop.mp4 normal_23010.mp4 cam_corridor.mp4 normal_14785.mp4 normal_21950.mp4 cam_smoking.mp4 normal_23406.mp4 cam_fire.mp4 normal_23107.mp4 cam_hall.mp4
fi
cd $W

echo "== 4. веб-сервис под supervisor"
cat > /etc/supervisor/conf.d/linqguard.conf <<EOF
[program:linqguard]
command=$APP/tools/run_server.sh
directory=$APP
autostart=true
autorestart=true
startsecs=5
stopasgroup=true
killasgroup=true
stdout_logfile=$W/data/server.log
stdout_logfile_maxbytes=20MB
redirect_stderr=true
EOF
supervisorctl reread >/dev/null && supervisorctl update

echo "== 5. SDK LinQ (Python 3.12.11) в фоне — нужен для компиляции .tpu"
# адрес и ключ доступа к SDK — из материалов кейса, в репозиторий не кладём:
#   export LINQ_SDK_URL=https://.../tpu_framework_2026.9.45.zip LINQ_SDK_SHARE=<ключ из инструкции LinQ>
if [ ! -x $W/sdk/.venv/bin/python ] && [ -z "$LINQ_SDK_URL" ]; then
  echo "   пропуск: задайте LINQ_SDK_URL и LINQ_SDK_SHARE"
elif [ ! -x $W/sdk/.venv/bin/python ]; then
  nohup bash -c "
    mkdir -p $W/sdk && cd $W/sdk
    curl -s -u '$LINQ_SDK_SHARE:' -o fw.zip '$LINQ_SDK_URL' && unzip -q -o fw.zip
    uv venv --python 3.12.11 $W/sdk/.venv && . $W/sdk/.venv/bin/activate && cd tpu_framework/packets
    uv pip install --no-deps *.whl
    uv pip install 'numpy==1.26.4' 'tensorflow==2.20.0' 'keras==3.14.0' tf-keras 'torch==2.9.1' 'torchvision==0.24.1' \
      --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match scipy dictdiffer click tabulate \
      transformers 'onnx~=1.16.0' onnxscript 'onnxsim==0.6.5' onnx-graphsurgeon onnxruntime-extensions onnxoptimizer rich networkx \
      ordered-set frozendict humanize ml-dtypes setuptools simpy sympy importlib-resources gmpy2 bitstruct parse 'onnxruntime==1.29.0' \
      'attrs==26.1.0' 'pytest==9.1.1' opencv-python-headless
    echo SDK_DONE" > $W/sdk_install.log 2>&1 < /dev/null &
fi
echo "BOOTSTRAP_DONE — сервис поднимется через ~30 с на порту 8000"
