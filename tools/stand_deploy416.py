"""С ноутбука: залить программы 416×416 (models/guard416/tpu) на стенд как tpu_v2 и перезапустить сервис."""
import os
import sys
from pathlib import Path

sys.path.insert(0, r"D:\dev\mosprom\tools")
from linq_remote import Hub, load_env  # noqa: E402

load_env(Path(r"D:\dev\mosprom\.env"))
hub = Hub(os.environ["LINQ_HUB"], os.environ["LINQ_USER"], os.environ["LINQ_PASS"])
G = Path(__file__).resolve().parent.parent
src = Path(r"D:\dev\mosprom\models\guard416\tpu")
if hub.s.get(f"{hub.base}/api/contents/linq/models/tpu_v2", timeout=30).status_code == 404:
    hub.s.put(f"{hub.base}/api/contents/linq/models/tpu_v2", json={"type": "directory"}, timeout=30).raise_for_status()
names = []
for f in sorted(src.glob("*_b1_*_h1m.tpu")):
    name = f.name.split("_b1_")[0]
    tail = src / f"{name}_tail.onnx"
    if tail.exists():
        names.append(name)
        for x in (f, tail):
            print("upload", x.name, flush=True)
            hub.upload(x, f"linq/models/tpu_v2/{x.name}")
for f in ("api/workers.py", "engine/backends.py"):
    hub.upload(G / f, f"guard/{f}")
hub.exec(r'''
import json, os, subprocess, time
names = %r
p = os.path.expanduser("~/work/linq/models/registry.json"); r = json.load(open(p))
for n in names:
    r[n]["imgsz"] = 416
json.dump(r, open(p, "w"), ensure_ascii=False, indent=1)
sh = """
U=$(id -un); C=/auto/cache/$U; mkdir -p $C/tpu $C/tpu_v2
cp -f ~/work/linq/models/tpu/*.tpu $C/tpu/; cp -f ~/work/linq/models/tpu_v2/*.tpu $C/tpu_v2/; ls $C/tpu_v2
pkill -f "[u]vicorn api.main"; sleep 2
cd ~/work/guard && (nohup setsid bash tools/stand_run.sh > ~/work/linq/server.log 2>&1 < /dev/null &)
"""
print(subprocess.run(sh, shell=True, capture_output=True, text=True, executable="/bin/bash").stdout)
time.sleep(75)
m = json.loads(subprocess.run("curl -s -m 10 localhost:8765/api/metrics", shell=True, capture_output=True, text=True).stdout)
for x in m["models"]: print(x["model"], x["backend"], "infer p50", x.get("infer", {}).get("p50"))
for c in m["cameras"]: print(c["camera"], "fps", c["fps"], "proc p50", c.get("proc_p50"))
''' % names)
