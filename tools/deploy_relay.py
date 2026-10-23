"""Выкладывает relay на сервер Jupyter и (пере)запускает его.

    python tools/deploy_relay.py            # код + метаданные моделей + перезапуск relay
    python tools/deploy_relay.py --programs # ещё и залить .tpu из models/ в /auto/cache/<login>/
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from linq_remote import ROOT, Hub, load_env  # noqa: E402

REMOTE_DIR = 'iceq'


def ensure_dir(hub, path):
    if hub.s.get(f'{hub.base}/api/contents/{path}', timeout=30).status_code == 404:
        hub.s.put(f'{hub.base}/api/contents/{path}', json={'type': 'directory'}, timeout=30).raise_for_status()


def deploy(hub, programs=False):
    ensure_dir(hub, REMOTE_DIR)
    ensure_dir(hub, f'{REMOTE_DIR}/models')
    for f in ('yolo.py', 'relay.py'):
        hub.upload(ROOT / 'app' / f, f'{REMOTE_DIR}/{f}')
    metas = sorted((ROOT / 'models').glob('*.json'))
    for meta in metas:
        hub.upload(meta, f'{REMOTE_DIR}/models/{meta.name}')
        if programs:
            import json
            program = ROOT / 'models' / json.loads(meta.read_text())['program']
            print(f'загрузка {program.name} ({program.stat().st_size / 1e6:.1f} МБ)...', flush=True)
            hub.upload(program, f'{REMOTE_DIR}/models/{program.name}')
    cache = f'/auto/cache/{hub.user}'
    return hub.exec(f'''
import os, signal, subprocess, time
os.chdir(os.path.expanduser('~/work/{REMOTE_DIR}'))
subprocess.run('mkdir -p {cache} && cp -f models/*.tpu {cache}/ 2>/dev/null; ls -la {cache}', shell=True)
if os.path.exists('relay.pid'):
    try:
        os.kill(int(open('relay.pid').read()), signal.SIGTERM)
    except (ProcessLookupError, ValueError):
        pass
p = subprocess.Popen('LINQ_USER={hub.user} nohup python -u relay.py >> relay.log 2>&1 & echo $!',
                     shell=True, stdout=subprocess.PIPE, text=True, start_new_session=True)
pid = p.stdout.read().strip()
open('relay.pid', 'w').write(pid)
time.sleep(3)
print('relay pid', pid)
print(subprocess.run('tail -5 relay.log', shell=True, capture_output=True, text=True).stdout)
''')


if __name__ == '__main__':
    load_env()
    p = argparse.ArgumentParser()
    p.add_argument('--programs', action='store_true')
    args = p.parse_args()
    hub = Hub(os.environ['LINQ_HUB'], os.environ['LINQ_USER'], os.environ['LINQ_PASS'])
    sys.exit(0 if deploy(hub, args.programs) else 1)
