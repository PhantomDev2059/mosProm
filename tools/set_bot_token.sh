#!/bin/bash
# Токен бота от BotFather — в файл на сервере, не в репозиторий и не в чат.
#   bash tools/set_bot_token.sh [порт] [хост]
PORT=${1:-63772}
HOST=${2:-118.68.92.236}
read -r -s -p "Вставьте токен бота от BotFather и нажмите Enter: " TOKEN
echo
[ -n "$TOKEN" ] || { echo "пусто — ничего не записал"; exit 1; }
printf '%s' "$TOKEN" | ssh -p "$PORT" "root@$HOST" \
  'umask 077; mkdir -p /workspace/linq/secrets; cat > /workspace/linq/secrets/tg_bot_token; supervisorctl restart linqguard >/dev/null; echo "токен записан, сервис перезапущен"'
