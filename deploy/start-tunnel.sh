#!/bin/bash
# Starts cloudflared and captures the tunnel URL into tunnel.url
# Sends Telegram notification only when the URL changes.
#
# Reads credentials from /home/admin/PicoGallery/Chatbot/.env

TUNNEL_FILE="/home/admin/PicoGallery/tunnel.url"
ENV_FILE="/home/admin/PicoGallery/Chatbot/.env"

# Load BOT_TOKEN and CHAT_ID from .env
if [[ -f "$ENV_FILE" ]]; then
  BOT_TOKEN=$(grep '^TELEGRAM_TOKEN=' "$ENV_FILE" | cut -d= -f2)
  CHAT_ID=$(grep '^CHAT_ID=' "$ENV_FILE" | cut -d= -f2)
fi

rm -f "$TUNNEL_FILE"

send_telegram() {
  local msg="$1"
  if [[ -n "$BOT_TOKEN" && -n "$CHAT_ID" ]]; then
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
      -d "chat_id=${CHAT_ID}&text=${msg}" > /dev/null
  fi
}

while true; do
  LAST_URL=""
  cloudflared tunnel --protocol http2 --url http://localhost:3456 2>&1 | while IFS= read -r line; do
    echo "$line"
    if [[ "$line" == *"Tunnel not found"* || "$line" == *"Unauthorized"* ]]; then
      echo "[tunnel] Tunnel expired, restarting..."
      pkill -f "cloudflared tunnel" 2>/dev/null
    fi
    if [[ "$line" == *"trycloudflare.com"* ]]; then
      url=$(echo "$line" | grep -oP 'https://[a-z0-9\-]+\.trycloudflare\.com')
      if [[ -n "$url" && "$url" != "$LAST_URL" ]]; then
        LAST_URL="$url"
        echo "$url" > "$TUNNEL_FILE"
        echo "[tunnel] URL saved: $url"
        send_telegram "🔗 Helles-Galerie is online: $url"
      fi
    fi
  done
  echo "[tunnel] cloudflared exited, restarting in 5s..."
  sleep 5
done
