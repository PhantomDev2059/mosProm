#!/bin/bash
# Публичный HTTPS-адрес для мини-аппа Telegram: быстрый туннель Cloudflare к порту 8000 (без аккаунта и домена).
# Запускается supervisor-ом (программа tunnel); адрес меняется при каждом перезапуске — пишем его в
# $LINQ_SECRETS/public_url, бот перечитывает файл и обновляет кнопку мини-аппа.
# Через туннель наружу видны только мини-апп и его API (см. PUBLIC_PATHS в api/main.py).
set -e
SECRETS=${LINQ_SECRETS:-/workspace/linq/secrets}
BIN=/workspace/linq/bin/cloudflared
mkdir -p "$SECRETS" "$(dirname $BIN)"
if [ ! -x $BIN ]; then
  curl -sSL -o $BIN https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  chmod +x $BIN
fi
$BIN tunnel --no-autoupdate --url http://localhost:8000 2>&1 | while read -r line; do
  echo "$line"
  url=$(echo "$line" | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' || true)
  if [ -n "$url" ]; then echo "$url" > "$SECRETS/public_url"; echo "== публичный адрес: $url/app/"; fi
done
