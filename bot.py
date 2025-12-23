import os
import asyncio
import logging

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Render Environment Variable
PASSWORD = "115599"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# ================= UI =================
def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📊 Dashboard")],
            [KeyboardButton("➕ Add Mail"), KeyboardButton("📥 Get Mail")],
            [KeyboardButton("📬 Inbox"), KeyboardButton("🔐 2FA Tool")],
            [KeyboardButton("❓ Help")],
        ],
        resize_keyboard=True,
    )

def box(text: str) -> str:
    return f"━━━━━━━━━━━━━━━━━━━━\n{text}\n━━━━━━━━━━━━━━━━━━━━"

# ================= STATE =================
user_state = {}

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_state[user_id] = {"auth": False}

    await update.message.reply_text(
        "🔐 WELCOME\n\nএই বট ব্যবহার করতে পাসওয়ার্ড দিন:"
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id not in user_state:
        user_state[user_id] = {"auth": False}

    # ---------- AUTH ----------
    if not user_state[user_id]["auth"]:
        if text == PASSWORD:
            user_state[user_id]["auth"] = True
            await update.message.reply_text(
                "✅ Login Successful",
                reply_markup=main_keyboard(),
            )
        else:
            await update.message.reply_text("❌ Wrong Password")
        return

    # ---------- DASHBOARD ----------
    if text == "📊 Dashboard":
        await update.message.reply_text(
            box(
                "📊 YOUR DASHBOARD\n\n"
                "📧 Total Mails : `0`\n"
                "📥 Available   : `0`\n"
                "📤 Used        : `0`"
            ),
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )
        return

    # ---------- ADD MAIL ----------
    if text == "➕ Add Mail":
        await update.message.reply_text(
            box(
                "➕ ADD MAIL\n\n"
                "Format:\n"
                "`email|password|refresh_token|client_id`\n\n"
                "📤 এই ডেমো কোডে Mail storage যুক্ত করা হয়নি"
            ),
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )
        return

    # ---------- GET MAIL ----------
    if text == "📥 Get Mail":
        await update.message.reply_text(
            box(
                "📥 GET MAIL\n\n"
                "⚠️ এই ডেমো কোডে এখনো mail database যুক্ত করা হয়নি\n"
                "কিন্তু বট ঠিকভাবে চলছে ✅"
            ),
            reply_markup=main_keyboard(),
        )
        return

    # ---------- INBOX ----------
    if text == "📬 Inbox":
        await update.message.reply_text(
            box(
                "📬 INBOX\n\n"
                "⚠️ এই ডেমো কোডে OTP fetch যুক্ত করা হয়নি\n"
                "কিন্তু Render + Telegram ঠিকভাবে কাজ করছে ✅"
            ),
            reply_markup=main_keyboard(),
        )
        return

    # ---------- 2FA ----------
    if text == "🔐 2FA Tool":
        await update.message.reply_text(
            box(
                "🔐 2FA TOOL\n\n"
                "এই ডেমো ভার্সনে 2FA যুক্ত করা হয়নি\n"
                "কিন্তু core bot stable ভাবে চলছে ✅"
            ),
            reply_markup=main_keyboard(),
        )
        return

    # ---------- HELP ----------
    if text == "❓ Help":
        await update.message.reply_text(
            box(
                "❓ HELP\n\n"
                "📊 Dashboard → অবস্থা দেখুন\n"
                "➕ Add Mail → মেইল যোগ\n"
                "📥 Get Mail → মেইল নিন\n"
                "📬 Inbox → OTP চেক\n"
                "🔐 2FA Tool → 2FA কোড"
            ),
            reply_markup=main_keyboard(),
        )
        return

    # ---------- FALLBACK ----------
    await update.message.reply_text(
        "❓ কমান্ড বুঝতে পারিনি",
        reply_markup=main_keyboard(),
    )

# ================= MAIN =================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set in environment variables")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("🤖 Bot started successfully")
    app.run_polling()

if __name__ == "__main__":
    main()
