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
from urllib.parse import parse_qs, urlparse

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


CHATBOT_ENV_PATH  = "/home/admin/PicoGallery/Chatbot/.env" if PRODUCTION else os.path.join(os.path.dirname(__file__), "..", "Chatbot", ".env")
WEB_CREATOR_PY    = "/home/admin/PicoGallery/Chatbot/BotCreator/web_creator.py" if PRODUCTION else os.path.join(os.path.dirname(__file__), "..", "Chatbot", "BotCreator", "web_creator.py")

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


HTML_BOT_RECONFIGURE = """<!DOCTYPE html>
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
  <div class="top-label">⚠️ Bot Setup Required</div>
  <div class="card center-content">
    <h2>Reconnecting…</h2>
    <p>The hotspot will shut down.<br><br>
    Reconnect your phone to your home WiFi,<br>
    <strong>Lets create a personal chatbot:</strong></p>
    <p style="font-size:0.78rem;color:var(--text3);margin-top:4px;">It may take 30–60 seconds to start up.</p>
    <a href="HOSTNAME_URL" style="width:100%;text-decoration:none;">
      <button style="width:100%;margin-top:8px">Next →</button>
    </a>
  </div>
</body>
</html>""".replace("STYLE_PLACEHOLDER", _STYLE_READY)


_from_app = False  # Set when opened with ?mode=app

TUNNEL_FILE = "/home/admin/PicoGallery/tunnel.url"

def _read_tunnel_url():
    try:
        with open(TUNNEL_FILE) as f:
            return f.read().strip()
    except Exception:
        return ""

HTML_APP_WIFI_STATUS = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Helles-Galerie WiFi</title>
  STYLE_PLACEHOLDER
  <style>
    .top-label { position:fixed; top:24px; left:28px; font-size:0.95rem; font-weight:500; color:var(--accent); letter-spacing:0.02em; }
    .center-content { display:flex; flex-direction:column; align-items:center; text-align:center; gap:12px; }
    .center-content h2 { font-family:var(--font-serif); font-size:1.6rem; color:var(--text); letter-spacing:-0.02em; }
    .center-content p { font-size:0.88rem; color:var(--text2); line-height:1.6; }
    .url-box { background:var(--bg); border:1px solid var(--border); border-radius:var(--radius); padding:10px 14px; font-size:0.82rem; color:var(--accent); word-break:break-all; width:100%; }
    .btn-ghost { width:100%; margin-top:16px; padding:12px; background:transparent; color:var(--text3); border:1px solid var(--border); border-radius:var(--radius); cursor:pointer; font-size:0.95rem; font-family:var(--font-sans); }
  </style>
</head>
<body>
  <div class="top-label">✅ WiFi Connected</div>
  <div class="card center-content">
    <h2>WiFi Connected!</h2>
    <p>Connected to <strong style="color:var(--accent)">SSID_NAME</strong></p>
    GALLERY_SECTION
    <form method="POST" action="/reconfigure">
      <button class="btn-ghost" type="submit">Change WiFi Network</button>
    </form>
    <form method="POST" action="/stop-hotspot" style="width:100%;margin-top:8px">
      <button class="btn-ghost" type="submit">Close</button>
    </form>
  </div>
</body>
</html>""".replace("STYLE_PLACEHOLDER", _STYLE_READY)

HTML_APP_WIFI_CONNECTING = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Helles-Galerie WiFi</title>
  STYLE_PLACEHOLDER
  <style>
    .top-label { position:fixed; top:24px; left:28px; font-size:0.95rem; font-weight:500; color:var(--accent); letter-spacing:0.02em; }
    .center-content { display:flex; flex-direction:column; align-items:center; text-align:center; gap:12px; }
    .center-content h2 { font-family:var(--font-serif); font-size:1.6rem; color:var(--text); letter-spacing:-0.02em; }
    .center-content p { font-size:0.88rem; color:var(--text2); line-height:1.6; }
  </style>
</head>
<body>
  <div class="top-label">🔄 Connecting…</div>
  <div class="card center-content">
    <h2>Connecting…</h2>
    <p>The hotspot is shutting down.<br><br>
    Reconnect your phone to your home WiFi,<br>then close this page and use <strong style="color:var(--accent)">Bot Setup</strong> in the app.</p>
    <p style="font-size:0.78rem;color:var(--text3);margin-top:4px;">It may take 30–60 seconds to start up.</p>
  </div>
</body>
</html>""".replace("STYLE_PLACEHOLDER", _STYLE_READY)


def wifi_already_in_nm():
    """Returns the saved WiFi profile name if one exists in NetworkManager, else None."""
    if not PRODUCTION:
        return None
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "TYPE,NAME", "connection", "show"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[0] == "802-11-wireless" and parts[1] not in ("Hotspot", HOTSPOT_SSID):
                return parts[1]
    except Exception:
        pass
    return None


def reconnect_saved_wifi(profile):
    """Bring down hotspot and reconnect to saved WiFi profile in background."""
    import time
    time.sleep(2)  # let the HTTP response be delivered first
    try:
        subprocess.run(["nmcli", "connection", "down", "Hotspot"], timeout=10, capture_output=True)
        time.sleep(1)
        subprocess.run(["nmcli", "connection", "up", profile], timeout=30, capture_output=True)
        open(WIFI_CONFIG_FLAG, "w").close()
        print(f"[provision] Reconnected to '{profile}', hotspot down.")
    except Exception as e:
        print(f"[provision] reconnect_saved_wifi error: {e}")


def start_web_creator():
    """Start web_creator.py in background if it exists."""
    if os.path.exists(WEB_CREATOR_PY):
        subprocess.run(["pkill", "-f", "web_creator.py"], capture_output=True)
        subprocess.run(["fuser", "-k", "5678/tcp"], capture_output=True)
        subprocess.Popen([sys.executable, WEB_CREATOR_PY])
        print("[provision] web_creator.py started on port 5678.")
    else:
        print(f"[provision] web_creator.py not found at {WEB_CREATOR_PY}")


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
        start_web_creator()
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

    # Start web_creator so user can set up bot after reconnecting to home WiFi
    start_web_creator()


class ProvisionHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _from_app
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        path = parsed.path
        if qs.get("mode", [""])[0] == "app":
            _from_app = True

        if path == "/chatbot-setup":
            self._respond(200, HTML_CHATBOT_SETUP)
            return

        saved_profile = wifi_already_in_nm()

        if _from_app:
            # App mode: show WiFi status only, no bot setup redirect
            if saved_profile:
                tunnel_url = _read_tunnel_url()
                if tunnel_url:
                    gallery_section = (
                        f'<p>Your gallery is live at:</p>'
                        f'<div class="url-box">{tunnel_url}</div>'
                    )
                else:
                    gallery_section = '<p style="font-size:0.78rem;color:var(--text3)">Gallery URL not available yet — it may still be starting up.</p>'
                page = (HTML_APP_WIFI_STATUS
                        .replace("SSID_NAME", saved_profile)
                        .replace("GALLERY_SECTION", gallery_section))
                self._respond(200, page)
            else:
                # WiFi not configured — show form
                networks = get_wifi_networks()
                options = "\n".join(f'<option value="{n}">{n}</option>' for n in networks)
                if not options:
                    options = '<option value="">No networks found — refresh</option>'
                self._respond(200, HTML_FORM.format(ssid_options=options))
            return

        # Direct browser: original behaviour
        if saved_profile:
            start_web_creator()
            hostname = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
            next_url = f"http://{hostname}.local:5678"
            page = HTML_BOT_RECONFIGURE.replace("HOSTNAME_URL", next_url)
            self._respond(200, page)
            threading.Thread(target=reconnect_saved_wifi, args=(saved_profile,), daemon=True).start()
            return
        networks = get_wifi_networks()
        options = "\n".join(f'<option value="{n}">{n}</option>' for n in networks)
        if not options:
            options = '<option value="">No networks found — refresh</option>'
        html = HTML_FORM.format(ssid_options=options)
        self._respond(200, html)

    def do_POST(self):
        global _from_app
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

        if self.path == "/reconfigure":
            # "Change WiFi Network" button in app mode — reset and show form
            networks = get_wifi_networks()
            options = "\n".join(f'<option value="{n}">{n}</option>' for n in networks)
            if not options:
                options = '<option value="">No networks found — refresh</option>'
            self._respond(200, HTML_FORM.format(ssid_options=options))
            return

        if self.path == "/stop-hotspot":
            html = HTML_APP_WIFI_CONNECTING.replace(
                "<h2>Connecting…</h2>",
                "<h2>Done!</h2>"
            ).replace(
                "The hotspot is shutting down.<br><br>\n    Reconnect your phone to your home WiFi,<br>then close this page and use <strong style=\"color:var(--accent)\">Bot Setup</strong> in the app.",
                "The hotspot is shutting down.<br><br>You can close this page."
            )
            self._respond(200, html)
            def _stop():
                import time
                time.sleep(1)
                subprocess.run(["nmcli", "connection", "down", "Hotspot"],
                               capture_output=True, timeout=10)
            threading.Thread(target=_stop, daemon=True).start()
            return

        ssid          = params.get("ssid", [""])[0]
        wifi_password = params.get("wifi_password", [""])[0]

        if _from_app:
            self._respond(200, HTML_APP_WIFI_CONNECTING)
        else:
            hostname = socket.gethostname()
            next_url = f"http://{hostname}.local:5678" if PRODUCTION else "http://localhost:5678"
            page = HTML_SUCCESS.replace("HOSTNAME_URL", next_url)
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

    HTTPServer.allow_reuse_address = True
    server = HTTPServer(("0.0.0.0", PORT), ProvisionHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
