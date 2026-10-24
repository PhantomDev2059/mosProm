"""С ноутбука (VPN): залить скомпилированные программы .tpu и хвосты на стенд и (пере)запустить сервис.

    python tools/stand_deploy.py D:/dev/mosprom/models/guard/tpu
"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\dev\mosprom\tools")
import os  # noqa: E402

from linq_remote import Hub, load_env  # noqa: E402

load_env(Path(r"D:\dev\mosprom\.env"))
hub = Hub(os.environ["LINQ_HUB"], os.environ["LINQ_USER"], os.environ["LINQ_PASS"])
src = Path(sys.argv[1])
for d in ("linq", "linq/models", "linq/models/tpu"):
    if hub.s.get(f"{hub.base}/api/contents/{d}", timeout=30).status_code == 404:
        hub.s.put(f"{hub.base}/api/contents/{d}", json={"type": "directory"}, timeout=30).raise_for_status()
guard = Path(__file__).resolve().parent.parent
for f in ("tools/stand_tails.py", "tools/stand_run.sh", "api/main.py", "api/workers.py", "engine/backends.py"):
    hub.upload(guard / f, f"guard/{f}")
# хвосты не льём (десятки МБ через VPN) — stand_tails.py соберёт их на стенде из тамошних ONNX
for f in sorted(src.glob("*.tpu")) + sorted(src.glob("*_report.json")):
    print("upload", f.name, f"{f.stat().st_size / 1e6:.1f} MB", flush=True)
    hub.upload(f, f"linq/models/tpu/{f.name}")
ok = hub.exec(r'''
import subprocess
print(subprocess.run("""
cd ~/work/guard && ~/work/linq/.venv/bin/python tools/stand_tails.py
U=$(id -un); mkdir -p /auto/cache/$U && cp -f ~/work/linq/models/tpu/*.tpu /auto/cache/$U/ && ls /auto/cache/$U
pkill -f "[u]vicorn api.main" ; sleep 1
cd ~/work/guard && (nohup setsid bash tools/stand_run.sh > ~/work/linq/server.log 2>&1 &)
sleep 25; tail -20 ~/work/linq/server.log; curl -s -m 10 localhost:8765/api/cameras | head -c 300
""", shell=True, capture_output=True, text=True, executable="/bin/bash").stdout)
''')
print("http://10.0.114.30/user/%s/proxy/8765/" % hub.user)
