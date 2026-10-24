#!/bin/bash
# Публичный HTTPS-адрес мини-аппа со стенда: быстрый туннель Cloudflare к порту сервиса (8765).
# Наружу видны только мини-апп, его API и снимки (PUBLIC_PATHS в api/main.py). Адрес пишется в
# $LINQ_SECRETS/public_url — бот перечитывает его сам.
#   nohup setsid bash tools/stand_public.sh > ~/work/linq/tunnel.log 2>&1 &
H=${LINQ_HOME:-$HOME/work/linq}
SECRETS=${LINQ_SECRETS:-$H/secrets}
BIN=$H/bin/cloudflared
mkdir -p "$SECRETS" "$(dirname $BIN)"
[ -x $BIN ] || { curl -sSL -o $BIN https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x $BIN; }
$BIN tunnel --no-autoupdate --protocol http2 --url http://localhost:8765 2>&1 | while read -r line; do
  echo "$line"
  url=$(echo "$line" | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' || true)
  if [ -n "$url" ]; then echo "$url" > "$SECRETS/public_url"; echo "== публичный адрес: $url/app/"; fi
done
