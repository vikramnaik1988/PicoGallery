#!/bin/bash
# PicoGallery startup orchestrator
# Implements the full provisioning + bot setup + service start flow.

WIFI_FLAG="/home/admin/PicoGallery/.wifi_configured"
BC_ENV="/home/admin/PicoGallery/Chatbot/BotCreator/.env"
BOT_ENV="/home/admin/PicoGallery/Chatbot/.env"
PROVISION_PY="/home/admin/PicoGallery/deploy/provision.py"
WEB_CREATOR_PY="/home/admin/PicoGallery/Chatbot/BotCreator/web_creator.py"
VERIFY_PY="/home/admin/PicoGallery/Chatbot/BotCreator/verify.py"
TUNNEL_FILE="/home/admin/PicoGallery/tunnel.url"

log() { echo "[startup] $(date '+%H:%M:%S') $*"; }

# ── Helpers ───────────────────────────────────────────────────────────────────

stop_all_services() {
    log "Stopping all services..."
    systemctl stop picogallery chatbot botcreator 2>/dev/null || true
    sleep 1
}

# Runs hotspot provisioning and blocks until WiFi is configured.
# Sets NEW_CONFIG=true after returning.
run_provisioning() {
    log "Starting WiFi hotspot provisioning..."
    pkill -f "provision.py" 2>/dev/null || true
    fuser -k 80/tcp 2>/dev/null || true
    sleep 1
    python3 "$PROVISION_PY" &
    PROV_PID=$!
    log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log "  Connect to 'Helles-Setup' hotspot and open:"
    log "  http://10.42.0.1"
    log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    while [ ! -f "$WIFI_FLAG" ]; do
        sleep 2
    done
    kill $PROV_PID 2>/dev/null
    wait $PROV_PID 2>/dev/null
    log "WiFi configured."
    NEW_CONFIG=true
}

# Starts botcreator service and blocks until both TELEGRAM_TOKEN and CHAT_ID are saved.
run_bot_creator() {
    log "Starting botcreator service on http://raspberrypi.local:5678 ..."
    systemctl start botcreator
    log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log "  Open http://raspberrypi.local:5678 to create your bot."
    log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    while true; do
        TOKEN=$(grep '^TELEGRAM_TOKEN=' "$BOT_ENV" 2>/dev/null | cut -d= -f2)
        CHAT=$(grep  '^CHAT_ID='        "$BOT_ENV" 2>/dev/null | cut -d= -f2)
        [ -n "$TOKEN" ] && [ -n "$CHAT" ] && break
        sleep 2
    done
    log "Bot creator done — token and chat ID saved."
}

# Returns 0 (true) if TELEGRAM_TOKEN is non-empty in BOT_ENV.
chatbot_available() {
    local token
    token=$(grep '^TELEGRAM_TOKEN=' "$BOT_ENV" 2>/dev/null | cut -d= -f2)
    [ -n "$token" ]
}

# Sends the tunnel URL to the owner via Telegram.
message_url() {
    local token chat_id url
    token=$(grep  '^TELEGRAM_TOKEN=' "$BOT_ENV" 2>/dev/null | cut -d= -f2)
    chat_id=$(grep '^CHAT_ID='       "$BOT_ENV" 2>/dev/null | cut -d= -f2)
    [ -z "$token" ] || [ -z "$chat_id" ] && { log "Cannot message URL — token or chat_id missing."; return; }

    log "Waiting for tunnel URL..."
    for i in $(seq 1 30); do
        [ -f "$TUNNEL_FILE" ] && { url=$(cat "$TUNNEL_FILE"); break; }
        sleep 2
    done

    if [ -z "$url" ]; then
        log "Tunnel URL not available after 60s — skipping message."
        return
    fi

    log "Sending gallery URL to Telegram..."
    curl -s -X POST "https://api.telegram.org/bot${token}/sendMessage" \
        --data-urlencode "chat_id=${chat_id}" \
        --data-urlencode "text=Your gallery is live: ${url}" \
        >/dev/null && log "URL sent." || log "Failed to send URL."
}

# ── Main loop ─────────────────────────────────────────────────────────────────
while true; do

    # ── 1. Check WiFi ─────────────────────────────────────────────────────────
    NEW_CONFIG=false
    if [ ! -f "$WIFI_FLAG" ]; then
        log "No WiFi config found."
        run_provisioning   # sets NEW_CONFIG=true
    else
        log "WiFi already configured."
    fi

    # ── 2. Wait for internet ──────────────────────────────────────────────────
    log "Waiting for internet connection..."
    CONNECTED=false
    for i in $(seq 1 60); do
        if ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1; then
            log "Internet is up."
            CONNECTED=true
            break
        fi
        sleep 2
    done

    if ! $CONNECTED; then
        log "No internet after 120s — clearing WiFi config and re-provisioning."
        rm -f "$WIFI_FLAG"
        continue
    fi

    # ── 3. Chatbot check (behaviour differs: new config vs existing) ──────────
    if $NEW_CONFIG; then
        # New WiFi config: create bot if missing or broken
        log "New WiFi config — checking chatbot..."
        if chatbot_available; then
            log "Existing bot credentials found — verifying..."
            if python3 "$VERIFY_PY"; then
                log "Bot verified OK."
            else
                log "Bot verification failed — launching bot creator."
                run_bot_creator
            fi
        else
            log "No bot credentials — launching bot creator."
            run_bot_creator
        fi
    else
        # Existing WiFi config: bot MUST already be valid — no creation allowed
        log "Existing WiFi config — checking chatbot..."
        if chatbot_available; then
            log "Bot credentials found — verifying..."
            if python3 "$VERIFY_PY"; then
                log "Bot verified OK."
            else
                log "Bot verification failed — stopping services and re-provisioning."
                stop_all_services
                rm -f "$WIFI_FLAG"
                continue
            fi
        else
            log "No bot credentials — stopping services and re-provisioning."
            stop_all_services
            rm -f "$WIFI_FLAG"
            continue
        fi
    fi

    # ── 4. Start services ─────────────────────────────────────────────────────
    log "Starting chatbot service..."
    systemctl start chatbot

    log "Starting PicoGallery..."
    systemctl start picogallery

    log "Starting Cloudflare tunnel..."
    systemctl start cloudflared 2>/dev/null || log "cloudflared.service not found — skipping."

    # ── 5. Message the tunnel URL to the owner ────────────────────────────────
    message_url

    log "Startup complete."
    break

done
