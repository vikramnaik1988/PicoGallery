#!/usr/bin/env python3
"""
Two-phase Telegram bot creator (fully headless / CLI).

  Phase 1 — Obtain API_ID + API_HASH from my.telegram.org
             (logs in via phone + SMS code, creates an App if needed)
             Writes / updates .env automatically.

  Phase 2 — Create a bot via @BotFather using Telethon
             (authenticates your account, runs the /newbot flow)
             Saves the bot token.
"""

import asyncio
import getpass
import os
import re
import sys
import time
from pathlib import Path

# ── dependency check ────────────────────────────────────────────────────────────
missing = []
try:
    import requests
except ImportError:
    missing.append("requests")

try:
    from bs4 import BeautifulSoup
except ImportError:
    missing.append("beautifulsoup4")

try:
    from dotenv import load_dotenv, set_key, dotenv_values
except ImportError:
    missing.append("python-dotenv")

try:
    from telethon import TelegramClient, events
    from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
except ImportError:
    missing.append("telethon")

if missing:
    print("Missing dependencies. Run:")
    print(f"  pip install {' '.join(missing)}")
    sys.exit(1)

# ── constants ──────────────────────────────────────────────────────────────────
MY_TG_BASE  = "https://my.telegram.org"
ENV_FILE    = Path(".env")

# ── helpers ────────────────────────────────────────────────────────────────────

def prompt(label: str, secret: bool = False) -> str:
    try:
        if secret:
            return getpass.getpass(f"  {label}: ").strip()
        return input(f"  {label}: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n\nAborted.")
        sys.exit(0)


def banner(text: str):
    print(f"\n{'─' * 55}")
    print(f"  {text}")
    print(f"{'─' * 55}")


def write_env(key: str, value: str):
    """Create .env if needed, then upsert key=value."""
    if not ENV_FILE.exists():
        ENV_FILE.write_text("")
    set_key(str(ENV_FILE), key, value, quote_mode="never")


# ══════════════════════════════════════════════════════════════════════════════
#  PHASE 1 — my.telegram.org  →  API_ID + API_HASH
# ══════════════════════════════════════════════════════════════════════════════

def mytg_login(session: requests.Session, phone: str) -> bool:
    """
    Send login code to *phone*, prompt user to enter it,
    POST to /auth/login.  Returns True on success.
    """
    # Step 1 — request code
    print(f"\n  Requesting login code for {phone} …")
    resp = session.post(
        f"{MY_TG_BASE}/auth/send_password",
        data={"phone": phone},
        timeout=15,
    )
    resp.raise_for_status()

    body = resp.text.strip()
    # response is JSON {"random_hash":"..."} or a plain string error
    try:
        import json
        data = json.loads(body)
        random_hash = data.get("random_hash") or data.get("hash") or data.get("phone_hash")
    except Exception:
        random_hash = None

    if not random_hash:
        # Some versions return the hash differently — try regex fallback
        m = re.search(r'"(?:random_hash|hash|phone_hash)"\s*:\s*"([^"]+)"', body)
        random_hash = m.group(1) if m else None

    if not random_hash:
        print(f"  ERROR: Unexpected response from my.telegram.org: {body[:200]}")
        return False

    # Step 2 — get code from user
    code = prompt("Enter the code Telegram sent to your app / SMS")

    # Step 3 — login
    resp = session.post(
        f"{MY_TG_BASE}/auth/login",
        data={"phone": phone, "random_hash": random_hash, "password": code},
        timeout=15,
    )
    resp.raise_for_status()

    if "true" in resp.text.lower() or resp.status_code == 200 and resp.url.endswith("/"):
        # Extra check: see if we're now on the logged-in homepage
        check = session.get(f"{MY_TG_BASE}/", timeout=10)
        if "Sign in" not in check.text:
            print("  Logged in to my.telegram.org.")
            return True

    # Even a redirect to /auth counts as success on some versions
    if "auth" not in resp.url:
        print("  Logged in to my.telegram.org.")
        return True

    print(f"  ERROR: Login failed. Response: {resp.text[:200]}")
    return False


def mytg_get_or_create_app(session: requests.Session, phone: str) -> tuple[str, str] | None:
    """
    Visit /apps.  If an app already exists, return (api_id, api_hash).
    If not, ask for app details and create one.
    Returns None on failure.
    """
    resp = session.get(f"{MY_TG_BASE}/apps", timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # ── Try to read existing app ───────────────────────────────────────────────
    api_id   = _extract_field(soup, ["app_id",   "api_id"])
    api_hash = _extract_field(soup, ["app_hash", "api_hash"])

    if api_id and api_hash:
        print(f"  Found existing app — API_ID: {api_id}")
        return api_id, api_hash

    # ── No app yet — create one ────────────────────────────────────────────────
    # Check the page actually has a create form (not an error)
    create_form = soup.find("form", {"id": "create_app_form"}) or \
                  soup.find("form", action=re.compile(r"/apps/create"))

    if not create_form:
        # Try visiting /apps directly for the create page
        resp2 = session.get(f"{MY_TG_BASE}/apps/create", timeout=15)
        soup2 = BeautifulSoup(resp2.text, "html.parser")
        create_form = soup2.find("form") or soup2

    print("\n  No Telegram API app found — let's create one.")
    app_title     = prompt("App title (e.g. My Project)")
    app_shortname = prompt("App short name, lowercase letters/digits (e.g. myproject)")

    # CSRF hash hidden in the page
    csrf = _get_csrf(soup) or _get_csrf(BeautifulSoup(resp.text, "html.parser"))
    if not csrf:
        # Re-fetch apps page to grab fresh CSRF
        resp3 = session.get(f"{MY_TG_BASE}/apps", timeout=10)
        csrf = _get_csrf(BeautifulSoup(resp3.text, "html.parser"))

    payload = {
        "hash":          csrf or "",
        "app_title":     app_title,
        "app_shortname": app_shortname,
        "app_url":       "",
        "app_platform":  "other",
        "app_desc":      "",
    }

    resp = session.post(
        f"{MY_TG_BASE}/apps/create",
        data=payload,
        timeout=15,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    api_id   = _extract_field(soup, ["app_id",   "api_id"])
    api_hash = _extract_field(soup, ["app_hash", "api_hash"])

    if api_id and api_hash:
        return api_id, api_hash

    # Last-resort: search raw text for patterns
    id_m   = re.search(r"api_id[^\d]*(\d{5,12})",  resp.text)
    hash_m = re.search(r"api_hash[^a-f0-9]*([a-f0-9]{32})", resp.text)
    if id_m and hash_m:
        return id_m.group(1), hash_m.group(1)

    print("  ERROR: Could not extract API credentials from the page.")
    print("  Please create an app manually at https://my.telegram.org/apps")
    return None


def _extract_field(soup: "BeautifulSoup", names: list[str]) -> str:
    """Try to pull a value from input[name=…] or adjacent uneditable span."""
    for name in names:
        # <input type="text" name="app_id" value="...">
        el = soup.find("input", {"name": name})
        if el and el.get("value"):
            return el["value"].strip()

        # <span …>VALUE</span> next to a label containing the name
        label = soup.find(string=re.compile(name, re.I))
        if label:
            parent = label.find_parent()
            if parent:
                sibling = parent.find_next("span") or parent.find_next("input")
                if sibling:
                    val = sibling.get("value") or sibling.get_text(strip=True)
                    if val:
                        return val.strip()

    # Fallback: any uneditable span that looks numeric (api_id) or hex (api_hash)
    for span in soup.find_all("span", class_=re.compile("uneditable")):
        text = span.get_text(strip=True)
        if "id" in names[0] and re.fullmatch(r"\d{5,12}", text):
            return text
        if "hash" in names[0] and re.fullmatch(r"[a-f0-9]{32}", text):
            return text

    return ""


def _get_csrf(soup: "BeautifulSoup") -> str:
    el = soup.find("input", {"name": "hash"})
    return el["value"].strip() if el and el.get("value") else ""


def phase1_get_credentials(phone: str) -> tuple[str, str] | None:
    """Full Phase 1 flow. Returns (api_id, api_hash) or None."""
    session = requests.Session()
    session.headers.update({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36",
        "Referer":         MY_TG_BASE,
        "Origin":          MY_TG_BASE,
    })

    for attempt in range(1, 4):
        ok = mytg_login(session, phone)
        if ok:
            break
        if attempt < 3:
            retry = prompt("Retry? (Y/n)").lower()
            if retry == "n":
                return None
        else:
            print("  Too many failed attempts.")
            return None

    result = mytg_get_or_create_app(session, phone)
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  PHASE 2 — Telethon + @BotFather  →  Bot token
# ══════════════════════════════════════════════════════════════════════════════

async def wait_for_botfather(client: "TelegramClient", timeout: int = 90) -> str:
    future: asyncio.Future = asyncio.get_event_loop().create_future()

    @client.on(events.NewMessage(from_users="BotFather"))
    async def _handler(event):
        if not future.done():
            future.set_result(event.raw_text)

    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        return ""
    finally:
        client.remove_event_handler(_handler)


def verify_token(token: str, expected_username: str) -> bool:
    """
    Call getMe to confirm the token is valid and its username matches
    the BOT_USERNAME stored in .env.
    Returns True if both checks pass.
    """
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=10,
        )
        data = resp.json()
    except Exception as e:
        print(f"  ERROR calling getMe: {e}")
        return False

    if not data.get("ok"):
        print(f"  Token invalid — Telegram says: {data.get('description', 'unknown error')}")
        return False

    actual = data["result"].get("username", "")
    expected_clean = expected_username.lstrip("@").lower()
    actual_clean   = actual.lstrip("@").lower()

    if actual_clean != expected_clean:
        print(f"  Token mismatch — token belongs to @{actual}, not @{expected_username}")
        return False

    print(f"  Token OK — confirmed belongs to @{actual}")
    return True


async def verify_ownership(api_id: int, api_hash: str, phone: str, bot_user: str) -> bool:
    """
    Log in with Telethon (this phone number's session) and ask BotFather
    for /mybots.  BotFather returns bot names as inline keyboard buttons
    (not plain text), so we inspect message.buttons as well as raw_text.
    Returns True if ownership confirmed.
    """
    session_name = f"session_{phone.replace('+', '').replace(' ', '')}"
    client = TelegramClient(session_name, api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print("  No saved session for this phone — sending auth code …")
        await client.send_code_request(phone)
        code = prompt("Auth code (from SMS / Telegram app)")
        try:
            await client.sign_in(phone, code)
        except PhoneCodeInvalidError:
            print("  ERROR: Invalid auth code.")
            await client.disconnect()
            return False
        except SessionPasswordNeededError:
            pw = prompt("Two-step verification password", secret=True)
            await client.sign_in(password=pw)

    bf = await client.get_entity("BotFather")

    # Capture the full message object so we can read inline buttons
    future: asyncio.Future = asyncio.get_event_loop().create_future()

    @client.on(events.NewMessage(from_users="BotFather"))
    async def _handler(event):
        if not future.done():
            future.set_result(event.message)

    await client.send_message(bf, "/mybots")
    try:
        msg = await asyncio.wait_for(future, timeout=30)
    except asyncio.TimeoutError:
        msg = None
    finally:
        client.remove_event_handler(_handler)

    await client.disconnect()

    if not msg:
        print("  No reply from BotFather — could not verify ownership.")
        return False

    bot_clean = bot_user.lstrip("@").lower()

    # Check 1 — plain text (unlikely to contain the name, but cover it)
    text_found = bot_clean in msg.raw_text.lower()

    # Check 2 — inline keyboard buttons (this is where BotFather puts the bots)
    button_found = False
    if msg.buttons:
        for row in msg.buttons:
            for btn in row:
                if bot_clean in btn.text.lower():
                    button_found = True
                    break

    found = text_found or button_found

    if found:
        print(f"  Ownership OK — @{bot_user} is listed under this account.")
    else:
        print(f"  WARNING: @{bot_user} was NOT found in /mybots for this account.")
        print(f"  BotFather replied:\n  {reply}")

    return found


def fetch_chat_id(token: str, bot_user: str, timeout: int = 120) -> int | None:
    """
    Instruct the user to send /start to the new bot, then poll
    getUpdates until we receive a message and can extract the chat.id.
    """
    print(f"  Polling for incoming message from @{bot_user} …")

    api_url  = f"https://api.telegram.org/bot{token}"
    deadline = time.time() + timeout
    offset   = 0

    while time.time() < deadline:
        try:
            resp = requests.get(
                f"{api_url}/getUpdates",
                params={"offset": offset, "timeout": 10, "limit": 1},
                timeout=15,
            )
            data = resp.json()
        except Exception as e:
            print(f"  getUpdates error: {e}")
            time.sleep(3)
            continue

        if data.get("ok") and data.get("result"):
            update = data["result"][0]
            offset = update["update_id"] + 1          # ack the update
            msg    = update.get("message") or \
                     update.get("edited_message") or \
                     update.get("channel_post")
            if msg and "chat" in msg:
                chat_id = msg["chat"]["id"]
                sender  = msg["chat"].get("username") or \
                          msg["chat"].get("first_name", "unknown")
                print(f"  Received message from {sender} — Chat ID: {chat_id}")
                return chat_id

    print("  Timed out waiting for a message. You can get the chat ID later by")
    print(f"  sending /start to @{bot_user} and checking:")
    print(f"  https://api.telegram.org/bot{token}/getUpdates")
    return None


async def phase2_create_bot(api_id: int, api_hash: str, phone: str):
    banner("Phase 2 — Create bot via @BotFather")

    bot_name = prompt("Bot display name (e.g. My Awesome Bot)")
    bot_user = prompt("Bot username — no @ prefix, must end with 'bot' (e.g. myawesomebot)")
    bot_user = bot_user.lstrip("@")

    if not bot_user.lower().endswith("bot"):
        print("  NOTE: Telegram requires bot usernames to end with 'bot'.")
        if prompt("Continue anyway? (y/N)").lower() != "y":
            return

    session_name = f"session_{phone.replace('+', '').replace(' ', '')}"
    client = TelegramClient(session_name, api_id, api_hash)

    print("\n  Connecting to Telegram …")
    await client.connect()

    # ── Auth ───────────────────────────────────────────────────────────────────
    if not await client.is_user_authorized():
        print(f"  Sending auth code to {phone} …")
        await client.send_code_request(phone)
        code = prompt("Auth code (from SMS / Telegram app)")
        try:
            await client.sign_in(phone, code)
        except PhoneCodeInvalidError:
            print("  ERROR: Invalid auth code.")
            await client.disconnect()
            return
        except SessionPasswordNeededError:
            pw = prompt("Two-step verification password", secret=True)
            await client.sign_in(password=pw)

    me = await client.get_me()
    print(f"  Logged in as: {me.first_name} (@{me.username})")

    # ── BotFather ──────────────────────────────────────────────────────────────
    print("\n  Talking to @BotFather …")
    bf = await client.get_entity("BotFather")

    async def send_and_wait(msg: str, step: str) -> str:
        await client.send_message(bf, msg)
        reply = await wait_for_botfather(client)
        if not reply:
            print(f"  ERROR: No reply from @BotFather at step '{step}' (timeout).")
            return ""
        print(f"\n  BotFather says:\n  {reply}\n")
        return reply

    # /newbot
    reply = await send_and_wait("/newbot", "/newbot")
    if not reply:
        await client.disconnect(); return

    # Bot name
    reply = await send_and_wait(bot_name, "bot name")
    if not reply:
        await client.disconnect(); return
    if any(w in reply.lower() for w in ("sorry", "invalid", "error")):
        print("  BotFather rejected the bot name. Try a different name.")
        await client.disconnect(); return

    # Bot username
    reply = await send_and_wait(bot_user, "bot username")
    if not reply:
        await client.disconnect(); return

    # ── Extract token ──────────────────────────────────────────────────────────
    token_m = re.search(r"\d{5,12}:[A-Za-z0-9_\-]{35,}", reply)
    if token_m:
        token = token_m.group(0)
        print("  ✓ Bot created!")
        print(f"  Name    : {bot_name}")
        print(f"  Username: @{bot_user}")
        print(f"  Token   : {token}")

        # Persist token first
        write_env("BOT_TOKEN",    token)
        write_env("BOT_USERNAME", bot_user)
        print("\n  BOT_TOKEN and BOT_USERNAME saved to .env")

        # ── Step 3: auto-trigger the bot so we can read the chat ID ──────────
        # Use the already-authenticated Telethon session to send /start to
        # the new bot — no manual action required.
        print(f"\n  Auto-sending /start to @{bot_user} via your account …")
        try:
            await client.send_message(bot_user, "/start")
            print("  /start sent.")
        except Exception as e:
            print(f"  Could not auto-send /start: {e}")
            print(f"  Please send any message to @{bot_user} manually in Telegram.")

        await client.disconnect()

        # ── Step 4: poll getUpdates to capture the chat ID ────────────────────
        chat_id = fetch_chat_id(token, bot_user)
        if chat_id:
            write_env("CHAT_ID", str(chat_id))
            print(f"  CHAT_ID={chat_id} saved to .env")
    else:
        print("  Could not parse bot token from BotFather reply.")
        if any(w in reply.lower() for w in ("username", "sorry", "taken")):
            print("  Hint: The username may already be taken. Try a different one.")
        await client.disconnect()


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    print("=" * 55)
    print("  Telegram Bot Creator  —  fully headless CLI")
    print("=" * 55)

    # ── Phone number (shared by both phases) ──────────────────────────────────
    phone = prompt("Phone number with country code (e.g. +14155552671)")

    # ── Phase 1 ────────────────────────────────────────────────────────────────
    # Check if .env already has valid credentials
    load_dotenv(str(ENV_FILE))
    api_id_env   = os.getenv("API_ID",   "").strip()
    api_hash_env = os.getenv("API_HASH", "").strip()

    if api_id_env and api_hash_env:
        print(f"\n  Found existing credentials in .env (API_ID={api_id_env}).")
        reuse = prompt("Use these? (Y/n)").lower()
        if reuse == "n":
            api_id_env = api_hash_env = ""

    if not api_id_env or not api_hash_env:
        banner("Phase 1 — Obtain API credentials from my.telegram.org")
        result = phase1_get_credentials(phone)
        if not result:
            print("\nCould not obtain API credentials. Exiting.")
            sys.exit(1)
        api_id_env, api_hash_env = result
        write_env("API_ID",   api_id_env)
        write_env("API_HASH", api_hash_env)
        print(f"\n  API_ID={api_id_env}  saved to .env")
        print(f"  API_HASH={api_hash_env}  saved to .env")

    try:
        api_id = int(api_id_env)
    except ValueError:
        print(f"ERROR: API_ID '{api_id_env}' is not an integer.")
        sys.exit(1)

    # ── Optional: verify existing BOT_TOKEN / BOT_USERNAME ────────────────────
    bot_token_env = os.getenv("BOT_TOKEN",    "").strip()
    bot_user_env  = os.getenv("BOT_USERNAME", "").strip()

    if bot_token_env and bot_user_env:
        banner("Existing bot detected in .env — running verification")
        print(f"  BOT_USERNAME : {bot_user_env}")
        print(f"  BOT_TOKEN    : {bot_token_env[:10]}…\n")

        # Check 1 — token valid and username matches
        token_ok = verify_token(bot_token_env, bot_user_env)

        # Check 2 — bot was created by this phone number's account
        if token_ok:
            print()
            ownership_ok = await verify_ownership(
                api_id, api_hash_env, phone, bot_user_env
            )
        else:
            ownership_ok = False

        if token_ok and ownership_ok:
            print("\n  All checks passed — credentials are valid for this account.")

            # Fetch CHAT_ID if missing
            chat_id_env = os.getenv("CHAT_ID", "").strip()
            if not chat_id_env:
                print("  CHAT_ID not set — fetching now …")
                session_name = f"session_{phone.replace('+', '').replace(' ', '')}"
                client = TelegramClient(session_name, api_id, api_hash_env)
                await client.connect()
                try:
                    await client.send_message(bot_user_env, "/start")
                finally:
                    await client.disconnect()
                chat_id = fetch_chat_id(bot_token_env, bot_user_env)
                if chat_id:
                    write_env("CHAT_ID", str(chat_id))
                    print(f"  CHAT_ID={chat_id} saved to .env")
            else:
                print(f"  CHAT_ID already set: {chat_id_env}")

            action = prompt("\nCreate a new bot anyway? (y/N)").lower()
            if action != "y":
                print("\nDone.")
                return
        else:
            print("\n  Verification failed — proceeding to create / fix credentials.")

    # ── Phase 2 ────────────────────────────────────────────────────────────────
    await phase2_create_bot(api_id, api_hash_env, phone)

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
