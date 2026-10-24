#!/bin/bash
# Сторож сервиса на стенде: без зрителей JupyterHub усыпляет простаивающий сервер (idle culler), а вместе с ним
# сервис, бот и туннель. Раз в 2 минуты сообщает хабу об активности сервера и поднимает упавшие сервис и туннель.
#   nohup setsid bash tools/stand_watchdog.sh > ~/work/linq/watchdog.log 2>&1 < /dev/null &
cd "$(dirname "$0")/.."
H=${LINQ_HOME:-$HOME/work/linq}
fails=0
while true; do
  now=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
  curl -s -o /dev/null -m 15 -X POST -H "Authorization: token $JUPYTERHUB_API_TOKEN" -H "Content-Type: application/json" \
    -d "{\"servers\": {\"\": {\"last_activity\": \"$now\"}}, \"last_activity\": \"$now\"}" "$JUPYTERHUB_ACTIVITY_URL"

  if ! pgrep -f "[c]loudflared tunnel" > /dev/null; then
    echo "$(date '+%d.%m %H:%M') туннель упал — поднимаю (бот перечитает новый адрес сам)"
    (nohup setsid bash tools/stand_public.sh >> "$H/tunnel.log" 2>&1 < /dev/null &)
  fi

  # сервис занят TPU и квотой CPU — одна неудачная проверка ещё не падение, перезапуск после трёх подряд
  code=$(curl -s -o /dev/null -m 20 -w "%{http_code}" localhost:8765/app/)
  if [ "$code" = "200" ]; then fails=0; else fails=$((fails + 1)); fi
  if ! pgrep -f "[u]vicorn api.main" > /dev/null || [ $fails -ge 3 ]; then
    echo "$(date '+%d.%m %H:%M') сервис не отвечает (код $code, подряд $fails) — перезапускаю"
    pkill -f "[u]vicorn api.main"; sleep 2
    (nohup setsid bash tools/stand_run.sh >> "$H/server.log" 2>&1 < /dev/null &)
    fails=0
    sleep 90
  fi
  sleep 120
done
