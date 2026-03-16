import logging
import os
import subprocess
from dotenv import load_dotenv
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


async def on_startup(app):
    url = read_tunnel_url()
    msg = "✅ Helles-Galerie bot started"
    if url:
        msg += f"\n🔗 {url}"
    await app.bot.send_message(chat_id=CHAT_ID, text=msg)


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

    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
