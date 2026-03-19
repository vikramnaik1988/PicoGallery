#!/usr/bin/env python3
"""
Helles-Galerie WiFi provisioning server.
Runs automatically on first boot when no WiFi is configured.
Phone connects to the "Helles-Setup" hotspot and opens http://10.42.0.1
"""

import subprocess
import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

# ── Platform switch ────────────────────────────────────────────────────────────
# Set to True when running on Raspberry Pi, False for local Windows testing.
PRODUCTION = sys.platform != "win32"
# ──────────────────────────────────────────────────────────────────────────────

HOTSPOT_SSID     = "Helles-Setup"
HOTSPOT_PASSWORD = "helles123"
WIFI_CONFIG_FLAG = "/home/admin/PicoGallery/.wifi_configured"
PORT             = 80 if PRODUCTION else 8888

HTML_FORM = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Helles-Galerie Setup</title>
  <style>
    *{{ box-sizing: border-box; }}
    body{{ font-family: sans-serif; max-width: 420px; margin: 40px auto; padding: 20px; background:#f5f5f5; }}
    h1{{ color:#c9a84c; margin-bottom:4px; }}
    p{{ color:#777; font-size:14px; margin-top:0; }}
    h2{{ font-size:15px; color:#333; margin:24px 0 6px; }}
    input, select{{ width:100%; padding:10px; margin-bottom:14px; border:1px solid #ccc; border-radius:8px; font-size:15px; background:#fff; }}
    button{{ width:100%; padding:14px; background:#c9a84c; color:#fff; border:none; border-radius:8px; font-size:16px; cursor:pointer; font-weight:bold; }}
    button:active{{ background:#b8973b; }}
  </style>
</head>
<body>
  <h1>Helles-Galerie</h1>
  <p>Connect to your home WiFi</p>
  <form method="POST" action="/configure">
    <h2>Home WiFi</h2>
    <select name="ssid">{ssid_options}</select>
    <input type="password" name="wifi_password" placeholder="WiFi Password" required>
    <button type="submit">Connect</button>
  </form>
</body>
</html>"""

HTML_SUCCESS = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Setup Complete</title>
  <style>
    body{{ font-family:sans-serif; max-width:420px; margin:60px auto; padding:20px; text-align:center; }}
    .icon{{ font-size:72px; }}
    h1{{ color:#c9a84c; }}
    p{{ color:#555; line-height:1.6; }}
  </style>
</head>
<body>
  <div class="icon">✅</div>
  <h1>Done!</h1>
  <p>The Pi is connecting to your WiFi network.</p>
  <p>Reconnect your phone to your home WiFi.<br>
     The gallery will be available shortly.</p>
</body>
</html>"""


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
        ssid          = params.get("ssid", [""])[0]
        wifi_password = params.get("wifi_password", [""])[0]

        self._respond(200, HTML_SUCCESS)
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
