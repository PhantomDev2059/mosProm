#!/bin/bash
# Сторож сервиса на стенде: без зрителей JupyterHub усыпляет простаивающий сервер (idle culler), а вместе с ним
# сервис, бот и туннель. Раз в 2 минуты сообщает хабу об активности сервера, поднимает упавшие сервис и туннель
# и пишет в Telegram (чат из $LINQ_SECRETS/alert_chat) о перезапусках, ошибках TPU, месте на диске; в 9:00 — сводка.
#   nohup setsid bash tools/stand_watchdog.sh > ~/work/linq/watchdog.log 2>&1 < /dev/null &
cd "$(dirname "$0")/.."
H=${LINQ_HOME:-$HOME/work/linq}
SECRETS=${LINQ_SECRETS:-$H/secrets}

tg() {
  local tok chat
  tok=$(cat "$SECRETS/tg_bot_token" 2>/dev/null); chat=$(cat "$SECRETS/alert_chat" 2>/dev/null)
  [ -n "$tok" ] && [ -n "$chat" ] || return
  curl -s -o /dev/null -m 20 "https://api.telegram.org/bot$tok/sendMessage" --data-urlencode "chat_id=$chat" \
    --data-urlencode "text=🛠 Стенд LinQ: $1"
}
say() { echo "$(TZ=Europe/Moscow date '+%d.%m %H:%M') $1"; tg "$1"; }

fails=0; down=0; log_pos=$(stat -c %s "$H/server.log" 2>/dev/null || echo 0); last_tpu=0; last_disk=0; summary_day=""
say "сторож запущен, слежу за сервисом, ботом и туннелем"
while true; do
  now=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
  curl -s -o /dev/null -m 15 -X POST -H "Authorization: token $JUPYTERHUB_API_TOKEN" -H "Content-Type: application/json" \
    -d "{\"servers\": {\"\": {\"last_activity\": \"$now\"}}, \"last_activity\": \"$now\"}" "$JUPYTERHUB_ACTIVITY_URL"

  if ! pgrep -f "[c]loudflared tunnel" > /dev/null; then
    old=$(cat "$SECRETS/public_url" 2>/dev/null)
    (nohup setsid bash tools/stand_public.sh >> "$H/tunnel.log" 2>&1 < /dev/null &)
    for i in $(seq 1 30); do sleep 2; new=$(cat "$SECRETS/public_url" 2>/dev/null); [ "$new" != "$old" ] && break; done
    say "туннель упал и поднят заново. Новый адрес мини-аппа: $new/app/ (бот подхватит сам)"
  fi

  # сервис занят TPU и квотой CPU — одна неудачная проверка ещё не падение, перезапуск после трёх подряд
  code=$(curl -s -o /dev/null -m 20 -w "%{http_code}" localhost:8765/app/)
  if [ "$code" = "200" ]; then
    [ $down = 1 ] && say "сервис снова отвечает ✅"
    fails=0; down=0
  else
    fails=$((fails + 1))
  fi
  if ! pgrep -f "[u]vicorn api.main" > /dev/null || [ $fails -ge 3 ]; then
    say "сервис не отвечает (код $code, проверок подряд: $fails) — перезапускаю"
    pkill -f "[u]vicorn api.main"; sleep 2
    (nohup setsid bash tools/stand_run.sh >> "$H/server.log" 2>&1 < /dev/null &)
    fails=0; down=1
    sleep 90
  fi

  # ошибки TPU в логе сервиса с прошлой проверки (не чаще раза в 30 минут)
  size=$(stat -c %s "$H/server.log" 2>/dev/null || echo 0)
  [ "$size" -lt "$log_pos" ] && log_pos=0
  errs=$(tail -c +$((log_pos + 1)) "$H/server.log" 2>/dev/null | grep -iE "tpu|rabbit|amqp|gateway" | grep -ciE "error|timeout|traceback|exception")
  log_pos=$size
  if [ "${errs:-0}" -ge 5 ] && [ $(( $(date +%s) - last_tpu )) -ge 1800 ]; then
    say "в логе $errs ошибок TPU/RabbitMQ за последние минуты — проверь стенд организаторов"
    last_tpu=$(date +%s)
  fi

  use=$(df -P "$H" | awk 'NR==2 {gsub("%", "", $5); print $5}')
  if [ "${use:-0}" -ge 95 ] && [ $(( $(date +%s) - last_disk )) -ge 3600 ]; then
    say "диск заполнен на ${use}% — ролики нарушений могут перестать сохраняться"
    last_disk=$(date +%s)
  fi

  # утренняя сводка перед защитой
  if [ "$(TZ=Europe/Moscow date +%H)" = "09" ] && [ "$summary_day" != "$(date +%F)" ]; then
    summary_day=$(date +%F)
    tg "доброе утро ✅ сервис отвечает (код $code), туннель $(cat "$SECRETS/public_url" 2>/dev/null)/app/, диск ${use}%"
  fi
  sleep 120
done
