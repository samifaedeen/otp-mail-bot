# ================= PROFESSIONAL ASYNC OTP MAIL BOT =================

import os, json, asyncio, re, requests
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
import openpyxl
import pyotp

# ================= CONFIG =================
BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"
PASSWORD = "115599"

DONGVAN_API = "https://tools.dongvanfb.net/api/get_code_oauth2"
DONGVAN_API_KEY = "public_demo_key"

BASE = "data/users"
os.makedirs(BASE, exist_ok=True)

state = {}
otp_tasks = {}  # uid -> asyncio.Task

# ================= UI HELPERS =================
def box(text):
    return f"━━━━━━━━━━━━━━━━━━━━\n{text}\n━━━━━━━━━━━━━━━━━━━━"

def main_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Dashboard")],
        [KeyboardButton("➕ Add Mail"), KeyboardButton("📥 Get Mail")],
        [KeyboardButton("📬 Inbox"), KeyboardButton("🔐 2FA Tool")],
        [KeyboardButton("❓ Help")]
    ], resize_keyboard=True)

def cancel_btn():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_process")]
    ])

# ================= STORAGE =================
def udir(uid):
    d = f"{BASE}/{uid}"
    os.makedirs(d, exist_ok=True)
    return d

def load(uid, f, default):
    p = f"{udir(uid)}/{f}"
    if not os.path.exists(p):
        json.dump(default, open(p, "w"))
    return json.load(open(p))

def save(uid, f, data):
    json.dump(data, open(f"{udir(uid)}/{f}", "w"), indent=2)

def reset(uid):
    state[uid] = {"auth": True}

# ================= START =================
async def start(update: Update, ctx):
    uid = update.effective_user.id
    state[uid] = {"auth": False}
    await update.message.reply_text(
        "🔐 WELCOME\n\nএই বট ব্যবহার করতে পাসওয়ার্ড দিন:"
    )

# ================= TEXT HANDLER =================
async def text(update: Update, ctx):
    uid = update.effective_user.id
    msg = update.message.text.strip()

    if uid not in state:
        state[uid] = {"auth": False}

    # AUTH
    if not state[uid]["auth"]:
        if msg == PASSWORD:
            reset(uid)
            await update.message.reply_text(
                "✅ Login Successful",
                reply_markup=main_kb()
            )
        else:
            await update.message.reply_text("❌ Wrong Password")
        return

    # DASHBOARD
    if msg == "📊 Dashboard":
        mails = load(uid, "mails.json", [])
        used = load(uid, "used.json", [])
        await update.message.reply_text(
            box(
                "📊 YOUR DASHBOARD\n\n"
                f"📧 Total Mails : `{len(mails)+len(used)}`\n"
                f"📥 Available   : `{len(mails)}`\n"
                f"📤 Used        : `{len(used)}`"
            ),
            parse_mode="Markdown",
            reply_markup=main_kb()
        )
        return

    # ADD MAIL
    if msg == "➕ Add Mail":
        reset(uid)
        state[uid]["await_xlsx"] = True
        await update.message.reply_text(
            box(
                "➕ ADD MAIL\n\n"
                "Format:\n"
                "`email|password|refresh_token|client_id`\n\n"
                "📤 এখন .xlsx ফাইল আপলোড করুন"
            ),
            parse_mode="Markdown",
            reply_markup=cancel_btn()
        )
        return

    # GET MAIL
    if msg == "📥 Get Mail":
        mails = load(uid, "mails.json", [])
        used = load(uid, "used.json", [])

        if not mails:
            await update.message.reply_text("❌ কোনো মেইল নেই")
            return

        current = mails.pop(0)
        used.append(current)

        save(uid, "mails.json", mails)
        save(uid, "used.json", used)
        save(uid, "active.json", current)

        await update.message.reply_text(
            box("📧 EMAIL (Use anywhere)\n\n" + f"`{current['email']}`"),
            parse_mode="Markdown"
        )

        await update.message.reply_text(
            box("📦 FULL MAIL DATA\n\n" + f"`{current['full']}`"),
            parse_mode="Markdown"
        )

        await update.message.reply_text(
            "⬇️ নিচের Inbox বাটনে ক্লিক করুন\nএই মেইলের OTP আনার জন্য",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔎 Inbox", callback_data="check_inbox")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_process")]
            ])
        )
        return

    # 2FA
    if msg == "🔐 2FA Tool":
        reset(uid)
        state[uid]["await_2fa"] = True
        await update.message.reply_text(
            box(
                "🔐 2FA AUTHENTICATOR\n\n"
                "Rules:\n"
                "• ≥16 chars\n"
                "• A-Z & 2-7\n\n"
                "✍️ Send Secret"
            ),
            reply_markup=cancel_btn()
        )
        return

    if state[uid].get("await_2fa"):
        reset(uid)
        await handle_2fa(update, msg)
        return

# ================= XLSX =================
async def doc(update: Update, ctx):
    uid = update.effective_user.id
    if not state.get(uid, {}).get("await_xlsx"):
        return

    reset(uid)
    file = await update.message.document.get_file()
    path = f"/tmp/{uid}.xlsx"
    await file.download_to_drive(path)

    wb = openpyxl.load_workbook(path)
    ws = wb.active

    mails = load(uid, "mails.json", [])
    added = 0

    for row in ws.iter_rows(values_only=True):
        for cell in row:
            if isinstance(cell, str) and "|" in cell:
                p = cell.strip().split("|")
                if len(p) >= 3 and "@" in p[0]:
                    mails.append({
                        "email": p[0].strip(),
                        "refresh_token": p[2].strip(),
                        "full": cell.strip()
                    })
                    added += 1

    save(uid, "mails.json", mails)
    await update.message.reply_text(f"✅ `{added}` টি মেইল যোগ হয়েছে", parse_mode="Markdown")

# ================= CALLBACK =================
async def cb(update: Update, ctx):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id

    if q.data == "cancel_process":
        task = otp_tasks.pop(uid, None)
        if task:
            task.cancel()
        reset(uid)
        await q.message.reply_text("❌ Process cancelled", reply_markup=main_kb())
        return

    if q.data == "check_inbox":
        active = load(uid, "active.json", None)
        if not active:
            await q.message.reply_text("❌ No active mail")
            return

        task = asyncio.create_task(
            otp_flow(uid, q.message, active["email"], active["refresh_token"])
        )
        otp_tasks[uid] = task

# ================= OTP FLOW (ASYNC) =================
async def otp_flow(uid, msg, email, token):
    await msg.reply_text(
        box("🔄 OTP CHECKING\n⏳ 30 seconds wait..."),
        reply_markup=cancel_btn()
    )

    try:
        await asyncio.sleep(30)

        r = requests.post(
            DONGVAN_API,
            data={
                "email": email,
                "refresh_token": token,
                "apikey": DONGVAN_API_KEY
            },
            timeout=35
        )

        if r.status_code != 200:
            await msg.reply_text(
                "❌ OTP পাওয়া যায়নি\n\nকারণ: API server error"
            )
            return

        data = r.json()

        if data.get("status"):
            await msg.reply_text(
                box(f"🔐 OTP VERIFIED\n\nOTP → `{data['code']}`"),
                parse_mode="Markdown"
            )
        else:
            reason = data.get("msg", "Inbox-এ OTP নেই অথবা Token invalid")
            await msg.reply_text(
                f"❌ OTP পাওয়া যায়নি\n\nকারণ:\n• `{reason}`",
                parse_mode="Markdown"
            )

    except asyncio.CancelledError:
        pass
    except Exception as e:
        await msg.reply_text(
            f"❌ OTP পাওয়া যায়নি\n\nকারণ:\n• `{e}`",
            parse_mode="Markdown"
        )

# ================= 2FA =================
async def handle_2fa(update, text):
    clean = re.sub(r"\s+", "", text).upper()
    if not re.fullmatch(r"[A-Z2-7]{16,}", clean):
        await update.message.reply_text(
            "❌ Invalid 2FA Secret\n\nProcess cancelled",
            reply_markup=main_kb()
        )
        return

    totp = pyotp.TOTP(clean)
    code = totp.now()

    await update.message.reply_text(
        box(f"🔐 2FA VERIFIED\n\nOTP → `{code}`\n⚠ Valid 30 seconds"),
        parse_mode="Markdown",
        reply_markup=main_kb()
    )

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))
    app.add_handler(MessageHandler(filters.Document.ALL, doc))
    app.add_handler(CallbackQueryHandler(cb))
    app.run_polling()

if __name__ == "__main__":
    main()
