#!/usr/bin/env python3
"""
Verify bot credentials from BotCreator/.env.
Uses the same verification logic as create_bot.py.
Exit 0 = valid, Exit 1 = invalid/missing.
"""

import sys, asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from create_bot import verify_token, verify_ownership

BC_ENV = Path(__file__).parent / ".env"


def read_env(path: Path) -> dict:
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


env      = read_env(BC_ENV)
token    = env.get("BOT_TOKEN",    "")
username = env.get("BOT_USERNAME", "")
api_id   = env.get("API_ID",       "")
api_hash = env.get("API_HASH",     "")
phone    = env.get("PHONE",        "")

if not token or not username:
    print("[verify] BOT_TOKEN or BOT_USERNAME missing.")
    sys.exit(1)

# Step 1 — verify token via Telegram getMe
print(f"[verify] Checking token for @{username}...")
if not verify_token(token, username):
    print("[verify] Token invalid.")
    sys.exit(1)

# Step 2 — verify ownership via Telethon (requires saved session + phone)
if api_id and api_hash and phone:
    print("[verify] Checking ownership via @BotFather /mybots...")
    try:
        ok = asyncio.run(verify_ownership(int(api_id), api_hash, phone, username))
        if not ok:
            print("[verify] Ownership check failed — bot not found under this account.")
            sys.exit(1)
    except Exception as e:
        print(f"[verify] Ownership check error: {e}")
        sys.exit(1)
else:
    print("[verify] Skipping ownership check (PHONE not in .env).")

print("[verify] All checks passed.")
sys.exit(0)
