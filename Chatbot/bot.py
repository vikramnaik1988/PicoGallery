import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv

# Vision search (lives in Chatbot/vision/)
sys.path.insert(0, os.path.dirname(__file__))
try:
    from vision.query_parser import parse as parse_query
    from vision import store as vision_store
    _vision_ok = True
except Exception as _e:
    _vision_ok = False
    logging.getLogger(__name__).warning(f"Vision search unavailable: {_e}")

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
        "/search <query> - Search photos\n"
        "/index  - Index all photos for AI search\n"
        "/help   - Show this message\n\n"
        "Or just type what you're looking for:\n"
        "  dog beach\n"
        "  person red hat\n"
        "  cat indoor"
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


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/search <query> — search indexed photos by description."""
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text(
            "Usage: /search <description>\n"
            "Examples:\n"
            "  /search dog\n"
            "  /search person red beach\n"
            "  /search cat indoor"
        )
        return
    await _do_search(update, query)


async def natural_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain text messages as photo search queries."""
    query = update.message.text.strip()
    if not query or query.startswith("/"):
        return
    await _do_search(update, query)


async def _do_search(update: Update, query: str) -> None:
    if not _vision_ok:
        await update.message.reply_text("Vision search not available.")
        return

    total = vision_store.count()
    if total == 0:
        await update.message.reply_text(
            "No photos indexed yet.\n"
            "Run the indexer first:\n"
            "  cd ~/PicoGallery/Chatbot && python3 -m vision.indexer"
        )
        return

    parsed = parse_query(query)
    results = vision_store.search(
        objects=parsed.objects,
        scenes=parsed.scenes,
        attributes=parsed.attributes,
        persons=parsed.persons,
    )

    if not results:
        await update.message.reply_text(
            f"No photos found for: {query}\n"
            f"(searched {total} indexed photos)"
        )
        return

    lines = [f"Found {len(results)} photo(s) for '{query}':\n"]
    for r in results[:10]:
        scene = f" [{r['scene']}]" if r['scene'] else ""
        faces = f" 👤×{r['faces']}" if r['faces'] else ""
        lines.append(f"📷 {r['filename']}{scene}{faces}")

    if len(results) > 10:
        lines.append(f"...and {len(results) - 10} more.")

    await update.message.reply_text("\n".join(lines))


async def index_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/index — start background indexing of all photos."""
    await update.message.reply_text(
        "Starting photo indexing in background…\n"
        "This may take a while. I'll message you when done."
    )
    asyncio.create_task(_run_indexer(update))


async def _run_indexer(update: Update) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", "-m", "vision.indexer",
            cwd=os.path.dirname(__file__),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        total = vision_store.count()
        await update.message.reply_text(
            f"Indexing complete.\n"
            f"Total photos in database: {total}"
        )
    except Exception as e:
        await update.message.reply_text(f"Indexer error: {e}")


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
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("index", index_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, natural_search))
    app.add_error_handler(error_handler)

    threading.Thread(target=_start_url_server, daemon=True).start()
    logger.info("Bot is running... (URL server on port 3457)")
    app.run_polling()


if __name__ == "__main__":
    main()
