import asyncio
import json
import logging
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv

_botcreator_proc = None
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID", "1889279229")
TUNNEL_FILE = os.getenv("TUNNEL_FILE", "/home/admin/PicoGallery/tunnel.url")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def read_tunnel_url():
    try:
        with open(TUNNEL_FILE, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


# --- Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Helles-Galerie Bot\n\n"
        "/url    - Current gallery URL\n"
        "/status - Server status\n"
        "/help   - Show this message"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/url    - Current gallery URL\n"
        "/status - Server status"
    )


async def url_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = read_tunnel_url()
    if url:
        await update.message.reply_text(f"🔗 {url}")
    else:
        await update.message.reply_text("⚠️ Tunnel URL not available yet.")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = subprocess.run(["systemctl", "is-active", "picogallery"], capture_output=True, text=True)
    active = result.stdout.strip() == "active"
    url = read_tunnel_url()
    if active:
        msg = f"✅ Server is running"
        if url:
            msg += f"\n🔗 {url}"
    else:
        msg = "❌ Server is not running"
    await update.message.reply_text(msg)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error: {context.error}")


async def _pin_url(app, url: str):
    try:
        text = f"🔗 {url}"
        sent = await app.bot.send_message(chat_id=CHAT_ID, text=text)
        await app.bot.pin_chat_message(
            chat_id=CHAT_ID,
            message_id=sent.message_id,
            disable_notification=True,
        )
        logger.info(f"Pinned tunnel URL: {url}")
    except Exception as e:
        logger.warning(f"Could not pin URL: {e}")


async def _watch_tunnel_url(app, initial_url: str):
    """Re-pin whenever tunnel.url changes from the last known value."""
    pinned = initial_url
    while True:
        await asyncio.sleep(10)
        url = read_tunnel_url()
        if url and url != pinned:
            await _pin_url(app, url)
            pinned = url


async def on_startup(app):
    await app.bot.send_message(chat_id=CHAT_ID, text="✅ Helles-Galerie bot started")
    url = read_tunnel_url() or ""
    if url:
        await _pin_url(app, url)
    asyncio.create_task(_watch_tunnel_url(app, initial_url=url))


# --- Local HTTP server (port 3457) — lets the mobile app fetch the tunnel URL ---

class _UrlHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/url":
            url = read_tunnel_url() or ""
            body = json.dumps({"url": url}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/bot-config":
            token = os.getenv("TELEGRAM_TOKEN", "")
            chat_id = os.getenv("CHAT_ID", "")
            body = json.dumps({"token": token, "chat_id": chat_id}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/start-botcreator":
            global _botcreator_proc
            if _botcreator_proc is None or _botcreator_proc.poll() is not None:
                import socket, time as _time
                _botcreator_proc = subprocess.Popen(
                    ["python3", "/home/admin/PicoGallery/Chatbot/BotCreator/web_creator.py"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                # Wait until port 5678 is ready (up to 10 seconds)
                for _ in range(20):
                    try:
                        s = socket.create_connection(("127.0.0.1", 5678), timeout=0.5)
                        s.close()
                        break
                    except OSError:
                        _time.sleep(0.5)
                already = False
            else:
                already = True
            body = json.dumps({"started": not already, "already_running": already}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def _start_url_server():
    server = HTTPServer(("0.0.0.0", 3457), _UrlHandler)
    server.serve_forever()


# --- Main ---

def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_TOKEN not set in .env file")

    app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("url", url_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_error_handler(error_handler)

    threading.Thread(target=_start_url_server, daemon=True).start()
    logger.info("Bot is running... (URL server on port 3457)")
    app.run_polling()


if __name__ == "__main__":
    main()
