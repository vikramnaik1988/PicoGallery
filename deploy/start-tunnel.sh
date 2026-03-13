#!/bin/bash
# Starts cloudflared and captures the tunnel URL into tunnel.url
# so the gallery server can expose it via /api/v1/server/tunnel-url

TUNNEL_FILE="/home/admin/PicoGallery/tunnel.url"
rm -f "$TUNNEL_FILE"

cloudflared tunnel --url http://localhost:3456 2>&1 | while IFS= read -r line; do
  echo "$line"
  if [[ "$line" == *"trycloudflare.com"* ]]; then
    url=$(echo "$line" | grep -oP 'https://[a-z0-9\-]+\.trycloudflare\.com')
    if [[ -n "$url" ]]; then
      echo "$url" > "$TUNNEL_FILE"
      echo "[tunnel] URL saved: $url"
    fi
  fi
done
