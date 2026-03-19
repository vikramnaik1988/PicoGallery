#!/usr/bin/env python3
"""
Helles-Galerie WiFi provisioning server.
Runs automatically on first boot when no WiFi is configured.
Phone connects to the "Helles-Setup" hotspot and opens http://10.42.0.1
"""

import subprocess
import os
import sys
import base64
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

# ── Platform switch ────────────────────────────────────────────────────────────
# Set to True when running on Raspberry Pi, False for local Windows testing.
PRODUCTION = sys.platform != "win32"

# Embed adaptive-icon.png as base64
_ICON_PATH = os.path.join(os.path.dirname(__file__), "..", "web", "image", "adaptive-icon.png") if not PRODUCTION else "/home/admin/PicoGallery/adaptive-icon.png"
try:
    with open(_ICON_PATH, "rb") as _f:
        _LOGO_B64 = base64.b64encode(_f.read()).decode()
except FileNotFoundError:
    _LOGO_B64 = ""
# ──────────────────────────────────────────────────────────────────────────────

HOTSPOT_SSID     = "Helles-Setup"
HOTSPOT_PASSWORD = "helles123"
WIFI_CONFIG_FLAG = "/home/admin/PicoGallery/.wifi_configured"
PORT             = 80 if PRODUCTION else 8888

_BASE_STYLE = """
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg:      #0e0f11;
      --surface: #16181c;
      --border:  #2a2d35;
      --text:    #e8e9ec;
      --text2:   #8b8fa8;
      --text3:   #555972;
      --accent:  #f5c842;
      --red:     #ff5f5f;
      --radius:  10px;
      --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      --font-serif: Georgia, 'Times New Roman', serif;
    }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: var(--font-sans);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }}
    .card {{
      width: 100%;
      max-width: 380px;
      padding: 40px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 20px;
      box-shadow: 0 32px 80px rgba(0,0,0,0.4);
    }}
    .logo {{
      display: flex;
      flex-direction: column;
      align-items: center;
      margin-bottom: 32px;
    }}
    .logo svg {{
      width: 80px; height: 80px;
      border-radius: 20px;
      margin-bottom: 16px;
      background: #000;
      box-shadow: 0 6px 28px rgba(180,120,0,0.5);
    }}
    .logo h1 {{
      font-family: var(--font-serif);
      font-size: 1.8rem;
      letter-spacing: -0.02em;
    }}
    .logo p {{ font-size: 0.82rem; color: var(--text3); margin-top: 4px; }}
    label {{ display: block; font-size: 0.78rem; color: var(--text2); margin-bottom: 6px; margin-top: 16px; letter-spacing: 0.04em; text-transform: uppercase; }}
    select, input {{
      width: 100%;
      padding: 10px 14px;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      color: var(--text);
      font-size: 0.95rem;
      font-family: var(--font-sans);
      outline: none;
    }}
    select:focus, input:focus {{ border-color: var(--accent); }}
    button {{
      width: 100%;
      margin-top: 24px;
      padding: 12px;
      background: linear-gradient(135deg, #9a6f00 0%, #c8860a 15%, #f5c842 50%, #c8860a 85%, #9a6f00 100%);
      color: #000;
      font-weight: 600;
      font-size: 0.95rem;
      border: none;
      border-radius: var(--radius);
      cursor: pointer;
      font-family: var(--font-sans);
    }}
    button:active {{ opacity: 0.85; }}
    .center {{ text-align: center; }}
    .big-icon {{ font-size: 64px; margin-bottom: 16px; }}
    .sub {{ color: var(--text2); font-size: 0.88rem; margin-top: 8px; line-height: 1.6; }}
  </style>"""

# Pre-rendered style with real CSS braces (for use with .replace(), not .format())
_STYLE_READY = _BASE_STYLE.replace("{{", "{").replace("}}", "}")

_LOGO_SVG = f"""<img src="data:image/png;base64,{_LOGO_B64}" alt="Helles-Galerie" style="width:80px;height:80px;border-radius:20px;margin-bottom:16px;box-shadow:0 6px 28px rgba(180,120,0,0.5);object-fit:cover;" />"""

HTML_FORM = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Helles-Galerie Setup</title>
  {style}
</head>
<body>
  <div class="card">
    <div class="logo">
      {logo}
      <h1>Helles-Galerie</h1>
      <p>WiFi Setup</p>
    </div>
    <form method="POST" action="/configure">
      <label>WiFi Network</label>
      <select name="ssid">{ssid_options}</select>
      <label>Password</label>
      <input type="password" name="wifi_password" placeholder="WiFi password" required>
      <button type="submit">Connect</button>
    </form>
  </div>
</body>
</html>""".replace("{style}", _BASE_STYLE).replace("{logo}", _LOGO_SVG)

HTML_SUCCESS = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Helles-Galerie Setup</title>
  STYLE_PLACEHOLDER
  <style>
    .top-label {
      position: fixed;
      top: 24px;
      left: 28px;
      font-size: 0.95rem;
      font-weight: 500;
      color: var(--accent);
      letter-spacing: 0.02em;
    }
    .center-content {
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      gap: 12px;
    }
    .center-content h2 {
      font-family: var(--font-serif);
      font-size: 1.6rem;
      color: var(--text);
      letter-spacing: -0.02em;
    }
    .center-content p {
      font-size: 0.88rem;
      color: var(--text2);
      line-height: 1.6;
    }
  </style>
</head>
<body>
  <div class="top-label">✅ Connected</div>
  <div class="card center-content">
    <h2>WiFi Connected!</h2>
    <p>The hotspot will now shut down.<br><br>
    Reconnect your phone to your home WiFi,<br><h3>Lets create a personal chatbot:</h3></p>
    <p style="font-size:0.78rem;color:var(--text3);margin-top:4px;">It may take 30–60 seconds to start up.</p>
    <a href="HOSTNAME_URL" style="width:100%;text-decoration:none;">
      <button style="width:100%;margin-top:8px">Next →</button>
    </a>
  </div>
</body>
</html>""".replace("STYLE_PLACEHOLDER", _STYLE_READY)


CHATBOT_ENV_PATH = "/home/admin/PicoGallery/Chatbot/.env" if PRODUCTION else os.path.join(os.path.dirname(__file__), "..", "Chatbot", ".env")

HTML_CHATBOT_SETUP = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Helles-Galerie Setup</title>
  STYLE_PLACEHOLDER
  <style>
    .top-label {
      position: fixed;
      top: 24px;
      left: 28px;
      font-size: 0.95rem;
      font-weight: 500;
      color: var(--accent);
      letter-spacing: 0.02em;
    }
    .hint {
      font-size: 0.78rem;
      color: var(--text3);
      margin-top: 4px;
      line-height: 1.5;
    }
    .hint a { color: var(--accent); text-decoration: none; }
  </style>
</head>
<body>
  <div class="top-label">✅ Connected</div>
  <div class="card">
    <div class="logo">
      <h1>Chatbot Setup</h1>
      <p>Connect your Telegram bot</p>
    </div>
    <form method="POST" action="/chatbot-setup">
      <label>Bot Token</label>
      <input type="text" name="bot_token" placeholder="123456:ABC-DEF..." required>
      <p class="hint">Get a token from <a href="https://t.me/BotFather">@BotFather</a> → /newbot</p>
      <label>Chat ID</label>
      <input type="text" name="chat_id" placeholder="Your Telegram user ID" required>
      <p class="hint">Send /start to <a href="https://t.me/userinfobot">@userinfobot</a> to get your ID</p>
      <button type="submit">Save & Start Bot</button>
    </form>
  </div>
</body>
</html>""".replace("STYLE_PLACEHOLDER", _STYLE_READY)

HTML_CHATBOT_DONE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Helles-Galerie Setup</title>
  STYLE_PLACEHOLDER
  <style>
    .top-label {
      position: fixed;
      top: 24px;
      left: 28px;
      font-size: 0.95rem;
      font-weight: 500;
      color: var(--accent);
      letter-spacing: 0.02em;
    }
    .center-content {
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      gap: 12px;
    }
    .center-content h2 {
      font-family: var(--font-serif);
      font-size: 1.6rem;
      color: var(--text);
      letter-spacing: -0.02em;
    }
    .center-content p {
      font-size: 0.88rem;
      color: var(--text2);
      line-height: 1.6;
    }
  </style>
</head>
<body>
  <div class="top-label">✅ Setup complete</div>
  <div class="card center-content">
    <h2>All done!</h2>
    <p>Your gallery is starting up.<br>You will receive a Telegram message<br>with the gallery link shortly.</p>
  </div>
</body>
</html>""".replace("STYLE_PLACEHOLDER", _STYLE_READY)


def save_chatbot_env(bot_token, chat_id):
    tunnel_file = "/home/admin/PicoGallery/tunnel.url"
    content = f"TELEGRAM_TOKEN={bot_token}\nCHAT_ID={chat_id}\nTUNNEL_FILE={tunnel_file}\n"
    os.makedirs(os.path.dirname(CHATBOT_ENV_PATH), exist_ok=True)
    with open(CHATBOT_ENV_PATH, "w") as f:
        f.write(content)
    if PRODUCTION:
        subprocess.run(["sudo", "systemctl", "start", "chatbot"])
    else:
        print(f"[mock] Saved chatbot .env: token={bot_token[:8]}... chat_id={chat_id}")


def get_wifi_networks():
    if not PRODUCTION:
        try:
            result = subprocess.run(
                ["netsh", "wlan", "show", "networks"],
                capture_output=True, text=True, timeout=10
            )
            networks = []
            for line in result.stdout.split("\n"):
                if "SSID" in line and "BSSID" not in line:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        ssid = parts[1].strip()
                        if ssid:
                            networks.append(ssid)
            return networks if networks else ["HomeNetwork", "Neighbors_WiFi", "Office_5G"]
        except Exception:
            return ["HomeNetwork", "Neighbors_WiFi", "Office_5G"]
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL", "device", "wifi", "list", "--rescan", "yes"],
            capture_output=True, text=True, timeout=15
        )
        seen = set()
        networks = []
        for line in result.stdout.strip().split("\n"):
            parts = line.split(":")
            ssid = parts[0].strip()
            if ssid and ssid != HOTSPOT_SSID and ssid not in seen:
                seen.add(ssid)
                networks.append(ssid)
        return networks
    except Exception:
        return []


def apply_config(ssid, wifi_password):
    if not PRODUCTION:
        print(f"[mock] Would connect to WiFi: {ssid}")
        return

    import time
    # Wait briefly so the success page is delivered before hotspot goes down
    time.sleep(2)

    # Stop the hotspot so wlan0 is free to connect as a client
    subprocess.run(["nmcli", "connection", "down", "Hotspot"], timeout=10)
    time.sleep(1)

    # Connect Pi to home WiFi
    subprocess.run(
        ["nmcli", "device", "wifi", "connect", ssid, "password", wifi_password],
        timeout=30
    )

    # Mark WiFi as configured
    open(WIFI_CONFIG_FLAG, "w").close()

    # Start gallery services
    subprocess.run(["sudo", "systemctl", "start", "picogallery", "cloudflared", "chatbot"])

    # Stop provisioning service
    subprocess.run(["sudo", "systemctl", "stop", "helles-setup"])


class ProvisionHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/chatbot-setup":
            self._respond(200, HTML_CHATBOT_SETUP)
            return
        networks = get_wifi_networks()
        options = "\n".join(f'<option value="{n}">{n}</option>' for n in networks)
        if not options:
            options = '<option value="">No networks found — refresh</option>'
        html = HTML_FORM.format(ssid_options=options)
        self._respond(200, html)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        params = parse_qs(body)

        if self.path == "/chatbot-setup":
            bot_token = params.get("bot_token", [""])[0]
            chat_id   = params.get("chat_id",   [""])[0]
            self._respond(200, HTML_CHATBOT_DONE)
            threading.Thread(
                target=save_chatbot_env,
                args=(bot_token, chat_id),
                daemon=True
            ).start()
            return

        ssid          = params.get("ssid", [""])[0]
        wifi_password = params.get("wifi_password", [""])[0]

        hostname = socket.gethostname()
        page = HTML_SUCCESS.replace("HOSTNAME_URL", f"http://{hostname}.local:3456").replace("HOSTNAME_LABEL", f"{hostname}.local:3456")
        self._respond(200, page)
        threading.Thread(
            target=apply_config,
            args=(ssid, wifi_password),
            daemon=True
        ).start()

    def _respond(self, code, html):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, format, *args):
        pass


def needs_provisioning():
    return not os.path.exists(WIFI_CONFIG_FLAG)


def main():
    if not needs_provisioning():
        print("[provision] WiFi already configured, exiting.")
        return

    if PRODUCTION:
        print(f"[provision] Starting hotspot '{HOTSPOT_SSID}'...")
        subprocess.run([
            "nmcli", "device", "wifi", "hotspot",
            "ifname", "wlan0",
            "ssid", HOTSPOT_SSID,
            "password", HOTSPOT_PASSWORD
        ], check=True)
        print(f"[provision] Connect to '{HOTSPOT_SSID}' and open http://10.42.0.1")
    else:
        print(f"[dev] Running in Windows test mode on http://localhost:{PORT}")

    server = HTTPServer(("0.0.0.0", PORT), ProvisionHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
