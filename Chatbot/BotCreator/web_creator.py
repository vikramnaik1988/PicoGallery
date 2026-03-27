#!/usr/bin/env python3
"""
Web UI for BotCreator.
Mirrors the terminal flow in a browser with the gallery dark-gold theme.
Run: python web_creator.py
Open: http://localhost:5678
"""

import os, sys, re, asyncio, time, threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
sys.path.insert(0, str(Path(__file__).parent))
from create_bot import verify_token

PORT     = int(os.environ.get("PORT", 5678))
_DIR     = Path(__file__).parent
_BC_ENV  = _DIR / ".env"          # API_ID / API_HASH stored here
_BOT_ENV = _DIR.parent / ".env"   # BOT_TOKEN / CHAT_ID stored here (Chatbot/.env)

# ── State ──────────────────────────────────────────────────────────────────────
_s = {
    "step":             "phone",   # phone | reuse | mytg_code | bot_details |
                                   # tg_code | creating | existing_bot | done | error
    "log":              [],
    "error":            None,
    "phone":            None,
    "api_id":           None,
    "api_hash":         None,
    "mytg_session":     None,
    "mytg_hash":        None,
    "phone_code_hash":  None,
    "already_authed":   False,
    "existing_token":   None,
    "existing_user":    None,
    "existing_chat_id": None,
    "bot_name":         None,
    "bot_user":         None,
    "token":            None,
    "chat_id":          None,
}

def _log(text=""):
    _s["log"].append(str(text))
    print(text)

def _log_sep(title=""):
    _log(f"\n{'─'*55}")
    if title:
        _log(f"  {title}")
        _log(f"{'─'*55}")

def _write_env(path: Path, key: str, value: str):
    path.touch(exist_ok=True)
    lines = path.read_text().splitlines()
    new = [l for l in lines if not l.startswith(f"{key}=")]
    new.append(f"{key}={value}")
    path.write_text("\n".join(new) + "\n")

def _read_env(path: Path) -> dict:
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result

# ── CSS ────────────────────────────────────────────────────────────────────────
_CSS = """
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg:      #0e0f11;
    --surface: #16181c;
    --border:  #2a2d35;
    --text:    #e8e9ec;
    --text2:   #8b8fa8;
    --text3:   #555972;
    --accent:  #f5c842;
    --red:     #ff5f5f;
    --green:   #4caf7d;
    --radius:  10px;
    --font-sans:  -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --font-serif: Georgia, 'Times New Roman', serif;
    --font-mono:  'Courier New', 'Consolas', monospace;
  }
  body {
    background: var(--bg); color: var(--text);
    font-family: var(--font-sans);
    min-height: 100vh; display: flex;
    align-items: flex-start; justify-content: center;
    padding: 28px 20px;
  }
  .card {
    width: 100%; max-width: 520px;
    padding: 36px 40px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 20px;
    box-shadow: 0 32px 80px rgba(0,0,0,0.4);
  }
  .logo { margin-bottom: 24px; }
  .logo h1 { font-family: var(--font-serif); font-size: 1.5rem; }
  .logo p  { font-size: 0.82rem; color: var(--text3); margin-top: 4px; }
  .terminal {
    font-family: var(--font-mono);
    background: #080909;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 14px 16px;
    font-size: 0.77rem;
    line-height: 1.7;
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 46vh;
    overflow-y: auto;
    margin-bottom: 20px;
    color: #b8bac8;
  }
  .terminal .t-banner  { color: var(--text2); }
  .terminal .t-prompt  { color: var(--accent); }
  .terminal .t-ok      { color: var(--green); }
  .terminal .t-err     { color: var(--red); }
  .terminal .t-sep     { color: var(--border); }
  label {
    display: block; font-size: 0.78rem; color: var(--text2);
    margin-bottom: 6px; margin-top: 16px;
    letter-spacing: 0.04em; text-transform: uppercase;
  }
  input {
    width: 100%; padding: 10px 14px;
    background: var(--bg); border: 1px solid var(--border);
    border-radius: var(--radius); color: var(--text);
    font-size: 0.95rem; font-family: var(--font-sans); outline: none;
  }
  input:focus { border-color: var(--accent); }
  button {
    width: 100%; margin-top: 20px; padding: 12px;
    background: linear-gradient(135deg,#9a6f00 0%,#c8860a 15%,#f5c842 50%,#c8860a 85%,#9a6f00 100%);
    color: #000; font-weight: 600; font-size: 0.95rem;
    border: none; border-radius: var(--radius);
    cursor: pointer; font-family: var(--font-sans);
  }
  button:active { opacity: 0.85; }
  .btn-ghost {
    background: transparent; color: var(--text3);
    border: 1px solid var(--border); margin-top: 10px;
  }
  .spinner {
    width: 36px; height: 36px;
    border: 3px solid var(--border); border-top-color: var(--accent);
    border-radius: 50%; animation: spin 0.8s linear infinite;
    margin: 16px auto;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .result-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 8px 0; border-bottom: 1px solid var(--border);
    font-size: 0.85rem;
  }
  .result-row:last-child { border-bottom: none; }
  .result-key  { color: var(--text2); }
  .result-val  { color: var(--accent); font-family: var(--font-mono); font-size: 0.78rem; word-break: break-all; text-align: right; max-width: 70%; }
</style>"""

def _colorize(text: str) -> str:
    """Apply span colors to terminal output."""
    lines = []
    for line in text.split("\n"):
        if line.startswith("===") or line.startswith("  Telegram Bot Creator"):
            lines.append(f'<span class="t-banner">{line}</span>')
        elif line.startswith("─") or line.startswith("\n─"):
            lines.append(f'<span class="t-sep">{line}</span>')
        elif "✓" in line or "OK" in line or "saved" in line.lower() or "logged in" in line.lower():
            lines.append(f'<span class="t-ok">{line}</span>')
        elif "error" in line.lower() or "failed" in line.lower() or "invalid" in line.lower():
            lines.append(f'<span class="t-err">{line}</span>')
        elif line.strip().endswith(":") or line.strip().endswith("…"):
            lines.append(f'<span class="t-prompt">{line}</span>')
        else:
            lines.append(line)
    return "\n".join(lines)

def _terminal_html() -> str:
    raw = "\n".join(_s["log"])
    raw = raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    colored = _colorize(raw)
    return (
        f'<div class="terminal" id="trm">{colored}</div>'
        '<script>var t=document.getElementById("trm");if(t)t.scrollTop=t.scrollHeight;</script>'
    )

def _page(title: str, body: str, refresh: int = 0) -> str:
    meta_refresh = f'<meta http-equiv="refresh" content="{refresh};url=/">' if refresh else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — Bot Creator</title>
  {meta_refresh}
  {_CSS}
</head>
<body>
  <div class="card">
    {body}
  </div>
</body>
</html>"""

# ── my.telegram.org helpers ────────────────────────────────────────────────────
def _mytg_send_code(phone: str):
    import requests as req
    MY_TG = "https://my.telegram.org"
    sess = req.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": MY_TG, "Origin": MY_TG,
    })
    _log(f"\n  Requesting login code for {phone} …")
    resp = sess.post(f"{MY_TG}/auth/send_password", data={"phone": phone}, timeout=15)
    resp.raise_for_status()
    body = resp.text.strip()
    try:
        import json as _j
        data = _j.loads(body)
        rh = data.get("random_hash") or data.get("hash") or data.get("phone_hash")
    except Exception:
        rh = None
    if not rh:
        m = re.search(r'"(?:random_hash|hash|phone_hash)"\s*:\s*"([^"]+)"', body)
        rh = m.group(1) if m else None
    if not rh:
        raise RuntimeError(f"Unexpected response from my.telegram.org: {body[:200]}")
    _s["mytg_session"] = sess
    _s["mytg_hash"]    = rh

def _mytg_verify(code: str):
    from bs4 import BeautifulSoup as BS
    MY_TG = "https://my.telegram.org"
    sess  = _s["mytg_session"]
    resp  = sess.post(f"{MY_TG}/auth/login",
        data={"phone": _s["phone"], "random_hash": _s["mytg_hash"], "password": code},
        timeout=15)
    resp.raise_for_status()
    _log("  Logged in to my.telegram.org.")
    r2   = sess.get(f"{MY_TG}/apps", timeout=15)
    soup = BS(r2.text, "html.parser")
    api_id   = _bc_extract(soup, ["app_id",   "api_id"])
    api_hash = _bc_extract(soup, ["app_hash", "api_hash"])
    if api_id and api_hash:
        _log(f"  Found existing app — API_ID: {api_id}")
        return api_id, api_hash
    # Create app automatically
    csrf_el  = soup.find("input", {"name": "hash"})
    csrf     = csrf_el["value"].strip() if csrf_el and csrf_el.get("value") else ""
    r3       = sess.post(f"{MY_TG}/apps/create", data={
        "hash": csrf, "app_title": "PicoGallery", "app_shortname": "picogallery",
        "app_url": "", "app_platform": "other", "app_desc": "",
    }, timeout=15)
    soup2    = BS(r3.text, "html.parser")
    api_id   = _bc_extract(soup2, ["app_id",   "api_id"])
    api_hash = _bc_extract(soup2, ["app_hash", "api_hash"])
    if not api_id or not api_hash:
        raise RuntimeError("Could not obtain API credentials from my.telegram.org")
    _log(f"  Found existing app — API_ID: {api_id}")
    return api_id, api_hash

def _bc_extract(soup, names):
    for name in names:
        el = soup.find("input", {"name": name})
        if el and el.get("value"):
            return el["value"].strip()
        label = soup.find(string=re.compile(name, re.I))
        if label:
            parent = label.find_parent()
            if parent:
                sib = parent.find_next("span") or parent.find_next("input")
                if sib:
                    val = sib.get("value") or sib.get_text(strip=True)
                    if val:
                        return val.strip()
    for span in soup.find_all("span", class_=re.compile("uneditable")):
        text = span.get_text(strip=True)
        if "id" in names[0] and re.fullmatch(r"\d{5,12}", text):
            return text
        if "hash" in names[0] and re.fullmatch(r"[a-f0-9]{32}", text):
            return text
    return ""

# ── Telethon helpers ───────────────────────────────────────────────────────────
def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

async def _tg_send_code_async() -> str | None:
    from telethon import TelegramClient
    phone    = _s["phone"]
    api_id   = int(_s["api_id"])
    api_hash = _s["api_hash"]
    session  = str(_DIR / f"session_{phone.replace('+','').replace(' ','')}")
    client   = TelegramClient(session, api_id, api_hash)
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        _log(f"  Already logged in as: {me.first_name} (@{me.username})")
        await client.disconnect()
        return None
    _log(f"  Sending auth code to {phone} …")
    sent = await client.send_code_request(phone)
    await client.disconnect()
    return sent.phone_code_hash

async def _create_bot_async(tg_code: str):
    from telethon import TelegramClient, events
    from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError
    phone    = _s["phone"]
    api_id   = int(_s["api_id"])
    api_hash = _s["api_hash"]
    bot_name = _s["bot_name"]
    bot_user = _s["bot_user"]
    session  = str(_DIR / f"session_{phone.replace('+','').replace(' ','')}")
    client   = TelegramClient(session, api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        try:
            await client.sign_in(phone, tg_code, phone_code_hash=_s["phone_code_hash"])
        except PhoneCodeInvalidError:
            await client.disconnect()
            raise RuntimeError("Invalid Telegram auth code")
        except SessionPasswordNeededError:
            await client.disconnect()
            raise RuntimeError("2FA password required — not supported in web UI")
    me = await client.get_me()
    _log(f"  Logged in as: {me.first_name} (@{me.username})")
    _log("\n  Talking to @BotFather …")
    bf = await client.get_entity("BotFather")

    async def send_wait(msg: str, timeout: int = 60) -> str:
        fut = asyncio.get_event_loop().create_future()
        @client.on(events.NewMessage(from_users="BotFather"))
        async def _h(ev):
            if not fut.done():
                fut.set_result(ev.raw_text)
        await client.send_message(bf, msg)
        try:
            reply = await asyncio.wait_for(fut, timeout=timeout)
            _log(f"\n  BotFather says:\n  {reply}")
            return reply
        except asyncio.TimeoutError:
            return ""
        finally:
            client.remove_event_handler(_h)

    await send_wait("/newbot")
    reply = await send_wait(bot_name)
    if any(w in reply.lower() for w in ("sorry", "invalid", "error")):
        await client.disconnect()
        raise RuntimeError(f"BotFather rejected bot name: {reply[:120]}")
    reply = await send_wait(bot_user)
    m = re.search(r"\d{5,12}:[A-Za-z0-9_\-]{35,}", reply)
    if not m:
        await client.disconnect()
        raise RuntimeError(f"Could not get token from BotFather: {reply[:120]}")
    token = m.group(0)
    _log(f"\n  ✓ Bot created!")
    _log(f"  Name    : {bot_name}")
    _log(f"  Username: @{bot_user}")
    _log(f"  Token   : {token}")
    _log(f"\n  Auto-sending /start to @{bot_user} via your account …")
    try:
        await client.send_message(f"@{bot_user}", "/start")
        _log(f"  /start sent.")
    except Exception as e:
        _log(f"  Could not auto-send /start: {e}")
        _log(f"  ➜ Please send any message to @{bot_user} in Telegram manually.")
    await client.disconnect()
    await asyncio.sleep(3)  # wait for Telegram to deliver /start before polling

    import requests as req
    _log(f"  Polling for incoming message from @{bot_user} …")
    _log(f"  ➜ Please send any message to @{bot_user} in Telegram now.")
    chat_id  = None
    deadline = time.time() + 120
    offset   = 0
    while time.time() < deadline:
        try:
            r    = req.get(f"https://api.telegram.org/bot{token}/getUpdates",
                           params={"offset": offset, "timeout": 5, "limit": 1}, timeout=10)
            data = r.json()
            if data.get("ok") and data.get("result"):
                upd    = data["result"][0]
                offset = upd["update_id"] + 1
                msg    = upd.get("message") or upd.get("channel_post")
                if msg and "chat" in msg:
                    chat_id = msg["chat"]["id"]
                    sender  = msg["chat"].get("username") or msg["chat"].get("first_name", "?")
                    _log(f"  Received message from {sender} — Chat ID: {chat_id}")
                    break
        except Exception:
            time.sleep(1)
    if not chat_id:
        _log(f"  Timed out waiting. Send /start to @{bot_user} and check getUpdates manually.")
    return token, chat_id

def _create_bot_thread(tg_code: str):
    try:
        token, chat_id = _run_async(_create_bot_async(tg_code))
        bot_user = _s.get("bot_user", "")
        chat_id_str = str(chat_id) if chat_id else ""

        # Save all fields to BotCreator/.env
        _write_env(_BC_ENV, "API_ID",       _s["api_id"])
        _write_env(_BC_ENV, "API_HASH",     _s["api_hash"])
        _write_env(_BC_ENV, "PHONE",        _s["phone"])
        _write_env(_BC_ENV, "BOT_TOKEN",    token)
        _write_env(_BC_ENV, "BOT_USERNAME", bot_user)
        _write_env(_BC_ENV, "CHAT_ID",      chat_id_str)
        _log(f"\n  Credentials saved to {_BC_ENV}")

        # Save bot fields to Chatbot/.env for the running bot service
        tunnel = "/home/admin/PicoGallery/tunnel.url"
        _write_env(_BOT_ENV, "TELEGRAM_TOKEN", token)
        _write_env(_BOT_ENV, "CHAT_ID",         chat_id_str)
        _write_env(_BOT_ENV, "TUNNEL_FILE",      tunnel)
        _log(f"  Bot config saved to {_BOT_ENV}")

        _s["token"]   = token
        _s["chat_id"] = chat_id
        _s["step"]    = "done"
    except Exception as e:
        _s["error"] = str(e)
        _log(f"\n  ERROR: {e}")
        _s["step"]  = "error"

# ── Page builders ──────────────────────────────────────────────────────────────
def _header(subtitle: str) -> str:
    return f"""
<div class="logo">
  <h1>Bot Creator</h1>
  <p>{subtitle}</p>
</div>"""

def _page_phone() -> str:
    env = _read_env(_BC_ENV)
    has_creds = bool(env.get("API_ID") and env.get("API_HASH"))
    note = (f'<p style="font-size:0.78rem;color:var(--text3);margin-top:8px">'
            f'Existing API_ID={env["API_ID"]} found in .env.</p>') if has_creds else ""
    body = f"""
{_header("Step 1 — Enter your phone number")}
<form method="POST" action="/submit">
  <input type="hidden" name="action" value="phone">
  <label>Phone number (with country code)</label>
  <input type="tel" name="phone" placeholder="+491234567890" required autofocus>
  {note}
  <button type="submit">Continue →</button>
</form>"""
    return _page("Phone", body)

def _page_reuse() -> str:
    env    = _read_env(_BC_ENV)
    api_id = env.get("API_ID", "?")
    body   = f"""
{_header("Existing credentials found")}
{_terminal_html()}
<p style="font-size:0.85rem;color:var(--text2);margin-bottom:4px">
  Found <strong style="color:var(--accent)">API_ID={api_id}</strong> in .env.
  Use these credentials?
</p>
<form method="POST" action="/submit">
  <input type="hidden" name="action" value="reuse">
  <input type="hidden" name="choice" value="y">
  <button type="submit">Yes, reuse them</button>
</form>
<form method="POST" action="/submit">
  <input type="hidden" name="action" value="reuse">
  <input type="hidden" name="choice" value="n">
  <button type="submit" class="btn-ghost" style="width:100%;margin-top:10px;padding:12px;border-radius:var(--radius);cursor:pointer">No, get new credentials</button>
</form>"""
    return _page("Reuse credentials?", body)

def _page_mytg_code() -> str:
    body = f"""
{_header("Step 1 of 2 — my.telegram.org")}
{_terminal_html()}
<form method="POST" action="/submit">
  <input type="hidden" name="action" value="mytg_code">
  <label>Code sent to your Telegram app / SMS</label>
  <input type="text" name="code" placeholder="TY-xxxxx" inputmode="text" required autofocus>
  <button type="submit">Verify →</button>
</form>"""
    return _page("Enter code", body)

def _page_bot_details() -> str:
    body = f"""
{_header("Step 2 — Bot details")}
{_terminal_html()}
<form method="POST" action="/submit">
  <input type="hidden" name="action" value="bot_details">
  <label>Bot display name</label>
  <input type="text" name="bot_name" placeholder="My Gallery Bot" required autofocus>
  <label>Bot username (must end with <em>bot</em>)</label>
  <input type="text" name="bot_user" placeholder="mygallerybot" required>
  <button type="submit">Create Bot →</button>
</form>"""
    return _page("Bot details", body)

def _page_tg_code() -> str:
    body = f"""
{_header("Step 2 of 2 — Telegram login")}
{_terminal_html()}
<form method="POST" action="/submit">
  <input type="hidden" name="action" value="tg_code">
  <label>Auth code from your Telegram app</label>
  <input type="text" name="code" placeholder="12345" inputmode="numeric" required autofocus>
  <button type="submit">Sign in & Create Bot →</button>
</form>"""
    return _page("Telegram auth code", body)

def _page_sending_code() -> str:
    body = f"""
{_header("Sending code…")}
{_terminal_html()}
<div style="text-align:center">
  <div class="spinner"></div>
  <p style="font-size:0.82rem;color:var(--text3)">Requesting code from Telegram…</p>
</div>"""
    return _page("Sending code…", body, refresh=2)

def _page_creating() -> str:
    bot_user = _s.get("bot_user") or ""
    log_text   = "\n".join(_s["log"])
    is_polling = "Polling for incoming" in log_text
    body = f"""
{_header("Creating your bot…")}
{_terminal_html()}
<div style="text-align:center">
  <div class="spinner"></div>
  <p style="font-size:0.82rem;color:var(--text3)">
    {"Waiting for /start response…" if is_polling else "Talking to @BotFather…"}
  </p>
</div>"""
    return _page("Creating bot…", body, refresh=2)

def _page_existing_bot() -> str:
    token = _s.get("existing_token", "")
    user  = _s.get("existing_user",  "")
    cid   = _s.get("existing_chat_id", "")
    body  = f"""
{_header("Existing bot found")}
{_terminal_html()}
<div class="result-row"><span class="result-key">Username</span><span class="result-val">@{user}</span></div>
<div class="result-row"><span class="result-key">Token</span><span class="result-val">{token[:16]}…</span></div>
<div class="result-row"><span class="result-key">Chat ID</span><span class="result-val">{cid or "not set"}</span></div>
<p style="font-size:0.82rem;color:var(--text2);margin-top:16px">Create a new bot anyway?</p>
<form method="POST" action="/submit">
  <input type="hidden" name="action" value="confirm_new">
  <input type="hidden" name="choice" value="y">
  <button type="submit">Yes, create new bot</button>
</form>
<form method="POST" action="/submit">
  <input type="hidden" name="action" value="confirm_new">
  <input type="hidden" name="choice" value="n">
  <button type="submit" class="btn-ghost" style="width:100%;margin-top:10px;padding:12px;border-radius:var(--radius);cursor:pointer">No, done</button>
</form>"""
    return _page("Existing bot", body)

def _page_done() -> str:
    token   = _s.get("token",    "")
    bot_user = _s.get("bot_user", "")
    chat_id = _s.get("chat_id",  "")
    body    = f"""
{_header("All done!")}
{_terminal_html()}
<div style="margin-top:16px">
  <div class="result-row"><span class="result-key">Username</span><span class="result-val">@{bot_user}</span></div>
  <div class="result-row"><span class="result-key">Token</span><span class="result-val">{token[:24]}…</span></div>
  <div class="result-row"><span class="result-key">Chat ID</span><span class="result-val">{chat_id or "check getUpdates"}</span></div>
</div>
<p style="font-size:0.78rem;color:var(--text3);margin-top:16px">
  Credentials saved to <code style="color:var(--accent)">{_BOT_ENV}</code>
</p>
<form method="POST" action="/submit">
  <input type="hidden" name="action" value="restart">
  <button type="submit" class="btn-ghost" style="width:100%;margin-top:16px;padding:12px;border-radius:var(--radius);cursor:pointer">Create another bot</button>
</form>"""
    return _page("Done!", body)

def _page_error() -> str:
    err  = _s.get("error", "Unknown error")
    body = f"""
{_header("Something went wrong")}
{_terminal_html()}
<p style="font-size:0.85rem;color:var(--red);margin-bottom:16px">{err}</p>
<form method="POST" action="/submit">
  <input type="hidden" name="action" value="restart">
  <button type="submit">Try again</button>
</form>"""
    return _page("Error", body)

def _render() -> str:
    step = _s["step"]
    if step == "phone":         return _page_phone()
    if step == "reuse":         return _page_reuse()
    if step == "sending_code":  return _page_sending_code()
    if step == "mytg_code":     return _page_mytg_code()
    if step == "bot_details":   return _page_bot_details()
    if step == "tg_code":       return _page_tg_code()
    if step == "creating":      return _page_creating()
    if step == "existing_bot":  return _page_existing_bot()
    if step == "done":          return _page_done()
    if step == "error":         return _page_error()
    return _page_phone()

# ── HTTP Handler ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._send(200, _render())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        params = parse_qs(self.rfile.read(length).decode())
        action = params.get("action", [""])[0]

        try:
            if action == "restart":
                _s.update({"step": "phone", "log": [], "error": None,
                           "phone": None, "api_id": None, "api_hash": None,
                           "mytg_session": None, "mytg_hash": None,
                           "phone_code_hash": None, "already_authed": False,
                           "existing_token": None, "existing_user": None,
                           "existing_chat_id": None, "bot_name": None,
                           "bot_user": None, "token": None, "chat_id": None})

            elif action == "phone":
                phone = params.get("phone", [""])[0].strip()
                _s["phone"] = phone
                _s["log"]   = []
                _log("=" * 55)
                _log("  Telegram Bot Creator  —  fully headless CLI")
                _log("=" * 55)
                _log(f"\n  Phone number: {phone}")
                env = _read_env(_BC_ENV)
                if env.get("API_ID") and env.get("API_HASH"):
                    _s["api_id"]   = env["API_ID"]
                    _s["api_hash"] = env["API_HASH"]
                    _log(f"\n  Found existing credentials in .env (API_ID={env['API_ID']}).")
                    _log("  Use these? (Y/n):")
                    _s["step"] = "reuse"
                else:
                    _log_sep("Phase 1 — Obtain API credentials from my.telegram.org")
                    _s["step"] = "sending_code"
                    def _send_code_thread():
                        try:
                            _mytg_send_code(_s["phone"])
                            _log("  Enter the code Telegram sent to your app / SMS:")
                            _s["step"] = "mytg_code"
                        except Exception as e:
                            _s["error"] = str(e)
                            _s["step"]  = "error"
                    threading.Thread(target=_send_code_thread, daemon=True).start()

            elif action == "reuse":
                choice = params.get("choice", ["y"])[0].lower()
                _log(f"  Use these? (Y/n): {choice}")
                if choice == "n":
                    _s["api_id"] = _s["api_hash"] = None
                    _log_sep("Phase 1 — Obtain API credentials from my.telegram.org")
                    _mytg_send_code(_s["phone"])
                    _log("  Enter the code Telegram sent to your app / SMS:")
                    _s["step"] = "mytg_code"
                else:
                    # Check for existing bot
                    bot_env = _read_env(_BOT_ENV)
                    tok  = bot_env.get("TELEGRAM_TOKEN", "")
                    user = bot_env.get("BOT_USERNAME",   "")
                    cid  = bot_env.get("CHAT_ID",        "")
                    if tok:
                        _log_sep("Existing bot detected in .env — verifying token")
                        _log(f"  BOT_USERNAME : {user}")
                        _log(f"  BOT_TOKEN    : {tok[:10]}…")
                        if verify_token(tok, user or tok.split(":")[0]):
                            _log("  ✓ Token valid.")
                            _s.update({"existing_token": tok, "existing_user": user,
                                       "existing_chat_id": cid, "step": "existing_bot"})
                        else:
                            _log("  ✗ Token invalid or bot deleted — please create a new bot.")
                            _log_sep("Phase 2 — Create bot via @BotFather")
                            _log("  Bot display name (e.g. My Awesome Bot):")
                            _s["step"] = "bot_details"
                    else:
                        _log_sep("Phase 2 — Create bot via @BotFather")
                        _log("  Bot display name (e.g. My Awesome Bot):")
                        _s["step"] = "bot_details"

            elif action == "mytg_code":
                code = params.get("code", [""])[0].strip()
                _log(f"  Enter the code Telegram sent to your app / SMS: {code}")
                api_id, api_hash = _mytg_verify(code)
                _s["api_id"]   = api_id
                _s["api_hash"] = api_hash
                _write_env(_BC_ENV, "API_ID",   api_id)
                _write_env(_BC_ENV, "API_HASH",  api_hash)
                _log(f"\n  API_ID={api_id}  saved to .env")
                _log(f"  API_HASH={api_hash}  saved to .env")
                _log_sep("Phase 2 — Create bot via @BotFather")
                _log("  Bot display name (e.g. My Awesome Bot):")
                _s["step"] = "bot_details"

            elif action == "bot_details":
                bot_name = params.get("bot_name", [""])[0].strip()
                bot_user = params.get("bot_user", [""])[0].strip().lstrip("@")
                _s["bot_name"] = bot_name
                _s["bot_user"] = bot_user
                _log(f"  Bot display name: {bot_name}")
                _log(f"  Bot username: {bot_user}")
                _log("\n  Connecting to Telegram …")
                phone_code_hash = _run_async(_tg_send_code_async())
                _s["phone_code_hash"] = phone_code_hash
                _s["already_authed"]  = (phone_code_hash is None)
                if _s["already_authed"]:
                    _s["step"] = "creating"
                    threading.Thread(target=_create_bot_thread, args=("",), daemon=True).start()
                else:
                    _log("  Auth code (from SMS / Telegram app):")
                    _s["step"] = "tg_code"

            elif action == "tg_code":
                code = params.get("code", [""])[0].strip()
                _log(f"  Auth code (from SMS / Telegram app): {code}")
                _s["step"] = "creating"
                threading.Thread(target=_create_bot_thread, args=(code,), daemon=True).start()

            elif action == "confirm_new":
                choice = params.get("choice", ["n"])[0].lower()
                _log(f"\nCreate a new bot anyway? (y/N): {choice}")
                if choice == "y":
                    _log_sep("Phase 2 — Create bot via @BotFather")
                    _log("  Bot display name (e.g. My Awesome Bot):")
                    _s["step"] = "bot_details"
                else:
                    _log("\nDone.")
                    _s["token"]    = _s["existing_token"]
                    _s["bot_user"] = _s["existing_user"]
                    _s["chat_id"]  = _s["existing_chat_id"]
                    _write_env(_BC_ENV, "BOT_TOKEN",    _s["token"])
                    _write_env(_BC_ENV, "BOT_USERNAME", _s["bot_user"])
                    _write_env(_BC_ENV, "CHAT_ID",      _s["chat_id"] or "")
                    _s["step"]     = "done"

        except Exception as e:
            _s["error"] = str(e)
            _s["step"]  = "error"
            _log(f"\n  ERROR: {e}")

        self._redirect("/")

    def _send(self, code: int, html: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def _redirect(self, path: str):
        self.send_response(303)
        self.send_header("Location", path)
        self.end_headers()

    def log_message(self, fmt, *args):
        pass  # suppress access logs


# ── Main ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"Bot Creator web UI → http://localhost:{PORT}")
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
