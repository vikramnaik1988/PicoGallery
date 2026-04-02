# PicoGallery

**A lightweight, self-hosted photo gallery server for single-board computers.**

Written in Go. Runs on Raspberry Pi 4 (2 GB RAM) and similar ARM boards. Designed as a resource-conscious alternative to Immich, without ML/AI features.

---

## Features

- 📷 Photo & video upload, browsing, and streaming
- 🔄 Mobile app auto-backup with duplicate detection
- 📁 File browser with ZIP download support
- 🗂️ Albums and favorites/archive
- 🔍 Metadata search (date, GPS radius, camera model)
- 👤 Multi-user with per-user storage quotas
- 🔑 JWT auth + API key support
- 📡 Server-Sent Events for real-time sync progress
- 🗄️ SQLite — zero external DB dependency
- 🪶 < 50 MB idle RAM

---

## Quick Start

### From Binary (Raspberry Pi)

```bash
# Download the release for your board
wget https://github.com/picogallery/picogallery/releases/latest/download/picogallery-arm64
chmod +x picogallery-arm64

# Create a config
cp config.yaml.example config.yaml
# Edit config.yaml with your storage path and JWT secret

# Run
./picogallery-arm64
```

**Default admin login:** `admin@picogallery.local` / `admin`  
**⚠️ Change the password immediately after first login.**

### Docker Compose

```bash
cp config.yaml.example config.yaml
# Edit PICO_JWT_SECRET in docker-compose.yml
docker compose up -d
```

### Build from Source

```bash
git clone https://github.com/picogallery/picogallery.git
cd picogallery
go mod download

# Build for current platform
make build

# Cross-compile for Raspberry Pi 4 (64-bit OS)
make arm64

# Cross-compile for older Pi (32-bit OS)
make arm32
```

---

## Configuration

Copy `config.yaml.example` to `config.yaml` and edit:

```yaml
server:
  port: 3456
  host: "0.0.0.0"

storage:
  root: "/mnt/usbdrive/picogallery"   # Where photos are stored

auth:
  jwt_secret: "replace-with-random-string"

thumbnails:
  workers: 2    # Max 2 recommended on SBCs
```

All values can also be set via environment variables:
- `PICO_JWT_SECRET`
- `PICO_STORAGE_ROOT`
- `PICO_DB_PATH`

---

## API

Base URL: `http://<your-pi>:3456/api/v1`

### Authentication

```bash
# Get a token
curl -X POST http://pi.local:3456/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@picogallery.local","password":"admin"}'

# Use the token
curl http://pi.local:3456/api/v1/users/me \
  -H "Authorization: Bearer <token>"
```

### Upload a photo

```bash
curl -X POST http://pi.local:3456/api/v1/assets/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@/path/to/photo.jpg" \
  -F "device_asset_id=local_unique_id"
```

### Full API documentation

See [API_DOCUMENTATION.md](./API_DOCUMENTATION.md) for complete endpoint reference.

---

## Storage Layout

```
/storage-root/
  originals/
    <user_id>/
      2026/
        01/
          ast_abc123.jpg
  .thumbnails/
    small/
      as/
        ast_abc123.jpg
    preview/
      as/
        ast_abc123.jpg
  picogallery.db
```

---

## Performance on Raspberry Pi 4 (2 GB RAM)

| Metric | Measured |
|--------|----------|
| Idle RAM (server process) | ~35 MB |
| JPEG thumbnail (12 MP) | ~80 ms |
| Gallery list (100 photos) | ~12 ms |
| Concurrent backup clients | 10+ |

---

## Helles-Galerie Deployment (Raspberry Pi)

The `deploy/` directory contains a full zero-touch setup system for running PicoGallery on a Raspberry Pi with remote access via Cloudflare Tunnel and a Telegram bot.

### Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Raspberry Pi                        │
│                                                     │
│  startup.service → startup.sh                       │
│      │                                              │
│      ├─ WiFi not configured? → provision.py (AP)    │
│      ├─ No bot credentials?  → botcreator service   │
│      └─ All good → start services                   │
│                                                     │
│  picogallery.service   — gallery on :3456           │
│  chatbot.service       — Telegram bot on :3457      │
│  botcreator.service    — Bot Creator UI on :5678    │
│  cloudflared.service   — public tunnel              │
└─────────────────────────────────────────────────────┘
```

### Services

| Service | Port | Purpose |
|---------|------|---------|
| `picogallery` | 3456 | PicoGallery server |
| `chatbot` | 3457 | Telegram bot + URL API |
| `botcreator` | 5678 | Web UI to create Telegram bot |
| `cloudflared` | — | Cloudflare Tunnel (public URL) |

### First-Time Setup

1. Flash Raspberry Pi OS, clone repo to `/home/admin/PicoGallery`
2. Install and enable the startup service:
```bash
sudo cp deploy/startup.service /etc/systemd/system/
sudo cp deploy/botcreator.service /etc/systemd/system/
sudo cp deploy/chatbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable startup botcreator chatbot
```
3. On first boot, the Pi creates a WiFi hotspot **Helles-Setup**
4. Connect your phone to the hotspot, open `http://10.42.0.1` and enter your home WiFi credentials
5. After WiFi connects, open the mobile app → **Setup a new device → Bot Setup**
6. Follow the Bot Creator web UI to create a Telegram bot
7. The gallery starts automatically and sends the public URL to your Telegram bot

### Remote Access

The Telegram bot pins the current public URL as a message in your chat. The mobile app reads this automatically — no manual URL entry needed. When Cloudflare assigns a new URL (e.g. after restart), the bot detects the change within 10 seconds and re-pins.

### bot.py HTTP API (port 3457)

| Endpoint | Response |
|----------|----------|
| `GET /url` | `{"url": "https://xxx.trycloudflare.com"}` |
| `GET /bot-config` | `{"token": "...", "chat_id": "..."}` |
| `GET /start-botcreator` | Starts botcreator on demand |

### Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/url` | Returns the current gallery URL |
| `/status` | Shows if the gallery server is running |
| `/help` | Lists available commands |

---

## Reverse Proxy (HTTPS via Nginx)

```nginx
server {
    listen 443 ssl;
    server_name gallery.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/gallery.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/gallery.yourdomain.com/privkey.pem;

    client_max_body_size 500M;

    location / {
        proxy_pass http://localhost:3456;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

---

## What PicoGallery Does NOT Include (vs Immich)

| Feature | Reason |
|---------|--------|
| Face recognition | Requires PyTorch — 400 MB+ RAM |
| CLIP/semantic search | ML inference — not SBC-viable |
| Object detection | Same as above |
| LivePhoto playback | ffmpeg RAM overhead |
| OAuth/OIDC | Deferred to v2 |
| Partner/public sharing | Deferred to v2 |
| Memories ("x years ago") | Deferred to v2 |

---

## License

MIT
