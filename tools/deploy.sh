#!/bin/bash
# Выкатка на сервер одной командой: код → /workspace/linq/app, тесты на сервере, перезапуск сервиса
# с чистым журналом демо (старый журнал сохраняется рядом), в конце — публичный адрес мини-аппа.
#   tools/deploy.sh <порт> <хост>          # ssh-доступ root@<хост> -p <порт>
set -eo pipefail
PORT=${1:?укажите порт ssh}
HOST=${2:?укажите хост}
cd "$(dirname "$0")/.."
PY=$([ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)
$PY -m pytest -q tests | tail -1
rsync -az --exclude .venv --exclude .git --exclude __pycache__ --exclude .pytest_cache ./ \
  -e "ssh -p $PORT" "root@$HOST:/workspace/linq/app/"
ssh -p "$PORT" "root@$HOST" 'cd /workspace/linq/app && /workspace/linq/.venv/bin/python -m pytest -q tests | tail -1 \
  && bash tools/demo_reset.sh && echo "мини-апп: $(cat /workspace/linq/secrets/public_url 2>/dev/null)/app/"'
