"""Работа с JupyterHub хакатона LinQ из командной строки (нужен поднятый VPN).

    python tools/linq_remote.py ls [path]             # список файлов в домашней папке
    python tools/linq_remote.py exec "!ls /auto/cache" # выполнить код в ядре на сервере
    python tools/linq_remote.py upload local [remote]  # загрузить файл в домашнюю папку
    python tools/linq_remote.py deploy model.tpu       # загрузить .tpu и положить в /auto/cache/<login>/
"""
import argparse
import base64
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

import requests
import websocket

ROOT = Path(__file__).resolve().parent.parent


def load_env(path=ROOT / '.env'):
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())


class Hub:
    def __init__(self, hub, user, password):
        self.hub, self.user = hub.rstrip('/'), user
        self.base = f'{self.hub}/user/{user}'
        self.s = requests.Session()
        page = self.s.get(f'{self.hub}/hub/login', timeout=15).text
        xsrf = re.search(r'name="_xsrf" value="([^"]+)"', page).group(1)
        r = self.s.post(f'{self.hub}/hub/login?next=', timeout=60,
                        data={'_xsrf': xsrf, 'username': user, 'password': password})
        if 'Invalid username or password' in r.text:
            sys.exit('JupyterHub: неверный логин или пароль')
        # дожидаемся старта пользовательского сервера и проходим OAuth до /user/<login>/
        for _ in range(60):
            if self.s.get(f'{self.base}/api/status', timeout=15).status_code == 200:
                break
            self.s.get(f'{self.base}/lab', timeout=30)
            time.sleep(2)
        else:
            sys.exit('JupyterHub: сервер пользователя не поднялся')
        self.s.headers['X-XSRFToken'] = self.s.cookies.get('_xsrf', path=f'/user/{user}/') or ''

    def ls(self, path=''):
        r = self.s.get(f'{self.base}/api/contents/{path}', params={'content': 1}, timeout=30)
        r.raise_for_status()
        return r.json()['content']

    def upload(self, local, remote, chunk=16 << 20):
        """Большие файлы — по частям (contents API: chunk 1..n, последний -1), целиком Jupyter отвечает 413."""
        raw = Path(local).read_bytes()
        parts = [raw[i:i + chunk] for i in range(0, len(raw), chunk)] or [b'']
        for n, part in enumerate(parts, 1):
            body = {'type': 'file', 'format': 'base64', 'name': Path(remote).name,
                    'content': base64.b64encode(part).decode()}
            if len(parts) > 1:
                body['chunk'] = -1 if n == len(parts) else n
            r = self.s.put(f'{self.base}/api/contents/{remote}', timeout=600, json=body)
            r.raise_for_status()

    def exec(self, code, timeout=300):
        # path='' запускает ядро в корне файлового браузера (~/work), а не в $HOME
        kernel = self.s.post(f'{self.base}/api/kernels', json={'name': 'python3', 'path': ''},
                             timeout=30).json()['id']
        try:
            cookies = '; '.join(f'{c.name}={c.value}' for c in self.s.cookies)
            ws = websocket.create_connection(
                f'{self.base.replace("http", "ws", 1)}/api/kernels/{kernel}/channels',
                header=[f'Cookie: {cookies}'], timeout=timeout)
            msg_id = uuid.uuid4().hex
            ws.send(json.dumps({
                'header': {'msg_id': msg_id, 'msg_type': 'execute_request', 'username': self.user,
                           'session': uuid.uuid4().hex, 'version': '5.3'},
                'parent_header': {}, 'metadata': {}, 'channel': 'shell',
                'content': {'code': code, 'silent': False, 'store_history': False,
                            'user_expressions': {}, 'allow_stdin': False, 'stop_on_error': True}}))
            ok = True
            while True:
                msg = json.loads(ws.recv())
                if msg.get('parent_header', {}).get('msg_id') != msg_id:
                    continue
                kind, content = msg['msg_type'], msg['content']
                if kind == 'stream':
                    print(content['text'], end='')
                elif kind in ('execute_result', 'display_data'):
                    print(content['data'].get('text/plain', ''))
                elif kind == 'error':
                    ok = False
                    print('\n'.join(content['traceback']), file=sys.stderr)
                elif kind == 'status' and content['execution_state'] == 'idle':
                    break
            ws.close()
            return ok
        finally:
            self.s.delete(f'{self.base}/api/kernels/{kernel}', timeout=30)


def main():
    load_env()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('ls').add_argument('path', nargs='?', default='')
    ex = sub.add_parser('exec')
    ex.add_argument('code', nargs='?')
    ex.add_argument('-f', '--file', help='выполнить код из локального файла')
    up = sub.add_parser('upload')
    up.add_argument('local')
    up.add_argument('remote', nargs='?')
    sub.add_parser('deploy').add_argument('program')
    args = p.parse_args()

    hub = Hub(os.environ['LINQ_HUB'], os.environ['LINQ_USER'], os.environ['LINQ_PASS'])
    if args.cmd == 'ls':
        for item in hub.ls(args.path):
            print(f"{item['type']:9} {item.get('size') or '':>12}  {item['name']}")
    elif args.cmd == 'exec':
        code = Path(args.file).read_text(encoding='utf-8') if args.file else args.code
        sys.exit(0 if hub.exec(code) else 1)
    elif args.cmd == 'upload':
        hub.upload(args.local, args.remote or Path(args.local).name)
    elif args.cmd == 'deploy':
        name = Path(args.program).name
        if 'programs' not in {i['name'] for i in hub.ls()}:
            hub.s.put(f'{hub.base}/api/contents/programs', json={'type': 'directory'}, timeout=30).raise_for_status()
        hub.upload(args.program, f'programs/{name}')
        cache = f'/auto/cache/{hub.user}'
        # ядро стартует в корне рабочего пространства, поэтому путь относительный;
        # через subprocess, чтобы ошибка cp провалила выполнение, а не молча напечаталась
        ok = hub.exec(
            'import subprocess\n'
            f'r = subprocess.run("mkdir -p {cache} && cp programs/{name} {cache}/ && ls -la {cache}",'
            ' shell=True, capture_output=True, text=True)\n'
            'print(r.stdout, r.stderr)\n'
            'assert r.returncode == 0, "deploy failed"')
        print(f'\nTARGET_URI: file://{cache}/{name}?raw=False' if ok else 'deploy failed')
        sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
