import os, json, asyncio, shutil, urllib.parse, random, re
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import openpyxl
from typing import Optional
import pyotp

# ========== CONFIG ==========
TOKEN = os.getenv("BOT_TOKEN")         # <-- এখানে তোমার বট টোকেন
ADMIN_ID = 5946249492                  # <-- এখানে তোমার অ্যাডমিন ID (int)

WHATSAPP_NUMBER = "+8801913457749"     # WhatsApp যোগাযোগের নাম্বার
PAYMENT_NUMBER  = "+8801788245521"     # অটো পেমেন্টের প্রাপক নাম্বার (Bkash/Nagad)

OTP_WEBSITE_URL = "https://dongvanfb.net/read_mail_box/"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOME_DIR = BASE_DIR
os.makedirs(HOME_DIR, exist_ok=True)

EMAIL_FILE = os.path.join(HOME_DIR, "emails.json")
USERS_FILE = os.path.join(HOME_DIR, "users.json")

BACKUP_DIR = os.path.join(BASE_DIR, "TelegramBotBackup")
os.makedirs(BACKUP_DIR, exist_ok=True)

PRICES_DEFAULT = {"hotmail": 1.5, "outlook": 1.5, "gmail": 4.8}
PRICES = {}

# ========== EMAIL STORAGE ==========

if not os.path.exists(EMAIL_FILE):
    with open(EMAIL_FILE, "w") as f:
        json.dump({"hotmail": [], "outlook": [], "gmail": []}, f, indent=4)

with open(EMAIL_FILE, "r") as f:
    try:
        emails = json.load(f)
    except Exception:
        emails = {"hotmail": [], "outlook": [], "gmail": []}


def save_emails():
    tmp = EMAIL_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(emails, f, indent=4, ensure_ascii=False)
    os.replace(tmp, EMAIL_FILE)
    try:
        shutil.copy(EMAIL_FILE, os.path.join(BACKUP_DIR, "emails_backup.json"))
    except Exception:
        pass


# ========== USER STORAGE ==========
user_balance = {}
blocked_users = set()
user_purchased_emails = {}
user_info = {}  # {uid: {"username": "...", "full_name": "..."}}

USERS_TEMPLATE = {
    "balance": {},
    "blocked": [],
    "purchased": {},
    "prices": PRICES_DEFAULT,
    "info": {},
}


def load_users():
    global user_balance, blocked_users, user_purchased_emails, PRICES, user_info

    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, "w") as f:
            json.dump(USERS_TEMPLATE, f, indent=4)

    with open(USERS_FILE, "r") as f:
        try:
            data = json.load(f)
        except Exception:
            data = USERS_TEMPLATE

    user_balance = {int(k): float(v) for k, v in data.get("balance", {}).items()}
    blocked_users = set(int(u) for u in data.get("blocked", []))
    user_purchased_emails = {
        int(k): list(v) for k, v in data.get("purchased", {}).items()
    }
    user_info = {int(k): dict(v) for k, v in data.get("info", {}).items()}

    global PRICES
    PRICES = {k: float(v) for k, v in data.get("prices", PRICES_DEFAULT).items()}

    # sync prices with email types
    for mt in list(emails.keys()):
        if mt not in PRICES and isinstance(emails[mt], list):
            PRICES[mt] = 1.0

    for mt in list(PRICES.keys()):
        if mt not in emails:
            emails[mt] = []

    save_emails()
    save_users()


def save_users():
    data = {
        "balance": {str(k): float(v) for k, v in user_balance.items()},
        "blocked": list(int(u) for u in blocked_users),
        "purchased": {str(k): list(v) for k, v in user_purchased_emails.items()},
        "prices": {k: float(v) for k, v in PRICES.items()},
        "info": {str(k): v for k, v in user_info.items()},
    }
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(tmp, USERS_FILE)


# purchased mail message ids (never auto-delete)
user_purchased_mail_message_ids = {}  # { uid: [message_id, ...] }

# প্রতি ইউজারের শেষ bot মেসেজ
last_bot_message_ids = {}  # { uid: msg_id }

# pending actions
admin_pending_action = {}
user_pending_action = {}

# অটো টপআপ pending list
pending_topups = {}  # { verify_id: {user_id,...} }


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def build_whatsapp_link(user_id: int, name: Optional[str] = None):
    text = urllib.parse.quote(
        f"আমি এত টাকা লোড করতে চাই। আমার Telegram ID: {user_id}. Amount: "
    )
    return f"https://wa.me/{WHATSAPP_NUMBER.replace('+', '')}?text={text}"


async def delete_last_bot_message(update: Update):
    """User নতুন মেসেজ দিলে আগের bot মেসেজ ডিলিট করবে (যদি সেটা mail না হয়)।"""
    if not update.effective_chat:
        return
    uid = update.effective_user.id
    chat = update.effective_chat

    preserved = set(user_purchased_mail_message_ids.get(uid, []))
    last_id = last_bot_message_ids.get(uid)
    if last_id and last_id not in preserved:
        try:
            await chat.delete_message(last_id)
        except Exception:
            pass


# ===== MAIL LABEL + KEYBOARDS =====

def mail_type_label(mail_type: str) -> str:
    """
    মেইল টাইপ অনুযায়ী আলাদা ইমোজি + লেখা রিটার্ন করে।
    """
    t = mail_type.lower()

    if t == "gmail":
        return "📧 GMAIL Mail"
    if t == "hotmail":
        return "🔥 HOTMAIL Mail"
    if t == "outlook":
        return "📮 OUTLOOK Mail"

    # নতুন / কাস্টম টাইপ যেমন yahoo, aol ইত্যাদি
    return f"📦 {t.upper()} Mail"


def mail_menu_keyboard():
    rows = []

    for t in sorted(PRICES.keys()):
        label = mail_type_label(t)
        rows.append([KeyboardButton(label)])

    rows.append([KeyboardButton("⬅ Back")])
    rows.append([KeyboardButton("❌ Cancel")])

    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def admin_reply_keyboard():
    kb = [
        [
            KeyboardButton("💳 Admin: Balance"),
            KeyboardButton("✉️ Admin: Add Emails"),
        ],
        [
            KeyboardButton("🧩 Admin: Mail Types"),
            KeyboardButton("📁 Admin: Backup"),
        ],
        [
            KeyboardButton("📢 Admin: Notify"),
            KeyboardButton("👥 Admin: Users"),
        ],
        [
            KeyboardButton("🔄 Admin: Recover"),
            KeyboardButton("❌ Cancel"),
        ],
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)


def add_balance_menu_keyboard():
    kb = [
        [KeyboardButton("⚙️ অটো টাকা যোগ")],
        [KeyboardButton("👨‍💼 Admin থেকে টাকা যোগ")],
        [KeyboardButton("⬅ Back"), KeyboardButton("❌ Cancel")],
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)


def auto_amount_keyboard():
    kb = [
        [KeyboardButton("10"), KeyboardButton("30")],
        [KeyboardButton("50"), KeyboardButton("✏️ টাকা লেখ")],
        [KeyboardButton("⬅ Back"), KeyboardButton("❌ Cancel")],
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)


def auto_method_keyboard():
    kb = [
        [KeyboardButton("বিকাশ"), KeyboardButton("নগদ")],
        [KeyboardButton("⬅ Back"), KeyboardButton("❌ Cancel")],
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)


def auto_verify_keyboard():
    kb = [
        [KeyboardButton("✅ Verification")],
        [KeyboardButton("⬅ Back"), KeyboardButton("❌ Cancel")],
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)


def main_reply_keyboard(is_admin_user: bool = False):
    """
    মেইন মেনু – বড় স্টাইলের বাটন
    Row1: 📧 Buy Mail 📧
    Row2: 🔐 Get 2FA | 📬 OTP
    Row3: 💰 Balance | ➕ Add Balance
    Row4: 🤖 Support (+ Admin)
    """
    rows = [
        [KeyboardButton("📧 Buy Mail 📧")],
        [KeyboardButton("🔐 Get 2FA"), KeyboardButton("📬 OTP")],
        [KeyboardButton("💰 Balance"), KeyboardButton("➕ Add Balance")],
    ]

    if is_admin_user:
        rows.append([KeyboardButton("🤖 Support 🤖"), KeyboardButton("🔐 Admin")])
    else:
        rows.append([KeyboardButton("🤖 Support 🤖")])

    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# ===== /start =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await delete_last_bot_message(update)

    if uid not in user_balance:
        user_balance[uid] = 0.0
    if uid not in user_purchased_emails:
        user_purchased_emails[uid] = []
    if uid not in user_purchased_mail_message_ids:
        user_purchased_mail_message_ids[uid] = []

    user_info[uid] = {
        "username": update.effective_user.username or "",
        "full_name": update.effective_user.full_name or "",
    }
    save_users()

    if uid in blocked_users:
        msg = await update.message.reply_text(
            "❌ আপনাকে ব্লক করা হয়েছে।", reply_markup=main_reply_keyboard(False)
        )
        last_bot_message_ids[uid] = msg.message_id
        return

    msg = await update.message.reply_text(
        "💌 স্বাগতম HOTMAIL BAZAR 💌\nনিচের মেনু থেকে অপশন বেছে নিন।",
        reply_markup=main_reply_keyboard(is_admin(uid)),
    )
    last_bot_message_ids[uid] = msg.message_id


# ========== 2FA helper ==========
def validate_2fa_secret(raw: str):
    cleaned = re.sub(r"\s+", "", raw).upper()
    if len(cleaned) < 16:
        return False, "Secret কমপক্ষে ১৬ অক্ষরের হতে হবে।"
    for ch in cleaned:
        if ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567":
            return False, "Secret এ শুধু A–Z এবং 2–7 ব্যবহার করা যাবে।"
    return True, cleaned


# ===== TEXT HANDLER =====
async def text_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    uid = update.effective_user.id
    txt = update.message.text.strip()
    admin_flag = is_admin(uid)

    await delete_last_bot_message(update)

    if uid in blocked_users and txt != "/start":
        msg = await update.message.reply_text(
            "❌ আপনাকে ব্লক করা হয়েছে।", reply_markup=main_reply_keyboard(False)
        )
        last_bot_message_ids[uid] = msg.message_id
        return

    # ===== GLOBAL CANCEL =====
    if txt.lower() in ("❌ cancel", "cancel", "বাতিল", "ক্যান্সেল"):
        user_pending_action.pop(uid, None)
        admin_pending_action.pop(uid, None)
        m = await update.message.reply_text(
            "❌ বর্তমান প্রসেস ক্যান্সেল করা হয়েছে।",
            reply_markup=main_reply_keyboard(admin_flag),
        )
        last_bot_message_ids[uid] = m.message_id
        return

    # ===== BACK (user + admin) =====
    if txt == "⬅ Back":
        user_pending_action.pop(uid, None)
        admin_pending_action.pop(uid, None)
        m = await update.message.reply_text(
            "🔙 মূল মেনুতে ফিরে আসা হয়েছে।",
            reply_markup=main_reply_keyboard(admin_flag),
        )
        last_bot_message_ids[uid] = m.message_id
        return

    # ===== MAIN BUTTONS =====

    # Buy Mail -> submenu
    if txt == "📧 Buy Mail 📧":
        if not PRICES:
            m = await update.message.reply_text(
                "এখনও কোন মেইল টাইপ সেট করা নেই।",
                reply_markup=main_reply_keyboard(admin_flag),
            )
            last_bot_message_ids[uid] = m.message_id
            return
        m = await update.message.reply_text(
            "যে মেইল টাইপ কিনতে চান, নিচের লিস্ট থেকে সিলেক্ট করুন।",
            reply_markup=mail_menu_keyboard(),
        )
        last_bot_message_ids[uid] = m.message_id
        return

    # Add Balance main
    if txt == "➕ Add Balance":
        m = await update.message.reply_text(
            "আপনি কিভাবে টাকা এড করতে চান?",
            reply_markup=add_balance_menu_keyboard(),
        )
        last_bot_message_ids[uid] = m.message_id
        return

    # ===== ADD BALANCE SUB-OPTIONS =====

    # অটো টাকা যোগ – main flow শুরু
    if txt == "⚙️ অটো টাকা যোগ":
        user_pending_action[uid] = {"action": "auto_topup_amount"}
        m = await update.message.reply_text(
            "আপনি কত টাকা এড করতে চান?",
            reply_markup=auto_amount_keyboard(),
        )
        last_bot_message_ids[uid] = m.message_id
        return

    # Admin থেকে টাকা যোগ – WhatsApp
    if txt == "👨‍💼 Admin থেকে টাকা যোগ":
        name = update.effective_user.full_name
        wa = build_whatsapp_link(uid, name)

        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("💬 WhatsApp এ যোগাযোগ করুন", url=wa)]]
        )

        await update.message.reply_text(
            "Admin থেকে ব্যালেন্স যোগ করতে নিচের বাটনে চাপ দিন:",
            reply_markup=kb,
        )
        m2 = await update.message.reply_text(
            "🔙 মূল মেনু:",
            reply_markup=main_reply_keyboard(admin_flag),
        )
        last_bot_message_ids[uid] = m2.message_id
        return

    # OTP website
    if txt == "📬 OTP":
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📬 Open OTP Website", url=OTP_WEBSITE_URL)]]
        )
        await update.message.reply_text(
            "OTP Inbox দেখতে নিচের বাটনে চাপ দিন:", reply_markup=kb
        )
        m2 = await update.message.reply_text(
            "🔙 মূল মেনু:", reply_markup=main_reply_keyboard(admin_flag)
        )
        last_bot_message_ids[uid] = m2.message_id
        return

    # Support
    if txt == "🤖 Support 🤖":
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✉️ Support Group", url="https://t.me/mailbuysupport"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📩 যোগাযোগ (বট)",
                        url="https://t.me/Mailbazar_support_bot",
                    )
                ],
            ]
        )
        await update.message.reply_text(
            "Support এ যেতে নিচের যেকোন বাটনে চাপ দিন:",
            reply_markup=kb,
        )
        m2 = await update.message.reply_text(
            "🔙 মূল মেনু:", reply_markup=main_reply_keyboard(admin_flag)
        )
        last_bot_message_ids[uid] = m2.message_id
        return

    # 2FA start
    if txt in ("🔐 Get 2FA", "🔐 2FA", "🔐 2FA Authenticator"):
        user_pending_action[uid] = {"action": "2fa_facebook"}
        msg = (
            "🔐 *Facebook 2FA Authenticator*\n"
            "আপনার 2FA Secret Key বা otpauth URL পাঠান নিচের নিয়ম মেনে।\n\n"
            "🧩 *উদাহরণ Secret:*\n"
            "`ABCD EFGH IGK84 LM44 NSER3 LM44`\n\n"
            "অথবা পুরো otpauth লিঙ্ক:\n"
            "`otpauth://totp/...`\n\n"
            "⚠ *নিয়ম:*\n"
            "• কমপক্ষে ১৬টি অক্ষর\n"
            "• শুধুমাত্র *A–Z* এবং *2–7*\n"
            "• মাঝে মাঝে *space* ব্যবহার করতে হবে\n\n"
            "🔑 ভুল হলে একবার ম্যাসেজ দিয়ে প্রসেস ক্যান্সেল হবে।"
        )
        kb = ReplyKeyboardMarkup(
            [[KeyboardButton("❌ Cancel")]], resize_keyboard=True
        )
        m = await update.message.reply_text(
            msg, parse_mode="Markdown", reply_markup=kb
        )
        last_bot_message_ids[uid] = m.message_id
        return

    # Balance simple
    if txt == "💰 Balance":
        bal = user_balance.get(uid, 0.0)
        lines = []
        for mt, price in PRICES.items():
            stock = len(emails.get(mt, []))
            lines.append(f"▫️ {mt.upper()} — 📦 {stock} টি | 💰 {price} টাকা")

        dashboard_info = (
            "📦 *Stock Overview*\n\n" + "\n".join(lines)
            if lines
            else "কোন মেইল টাইপ সেট করা নেই।"
        )
        m = await update.message.reply_text(
            f"💰 আপনার Balance: *{bal}* টাকা\n\n{dashboard_info}",
            parse_mode="Markdown",
            reply_markup=main_reply_keyboard(admin_flag),
        )
        last_bot_message_ids[uid] = m.message_id
        return

    # Admin panel
    if txt == "🔐 Admin":
        if not admin_flag:
            m = await update.message.reply_text(
                "❌ আপনি অ্যাডমিন নন।", reply_markup=main_reply_keyboard(False)
            )
            last_bot_message_ids[uid] = m.message_id
            return
        m = await update.message.reply_text(
            "🔐 Admin Panel", reply_markup=admin_reply_keyboard()
        )
        last_bot_message_ids[uid] = m.message_id
        return

    # ===== USER PENDING ACTIONS =====
    if uid in user_pending_action:
        act = user_pending_action[uid].get("action")

        # ----- 2FA FLOW -----
        if act == "2fa_facebook":
            raw = txt.strip()

            if raw.lower().startswith("otpauth://"):
                try:
                    parsed = urllib.parse.urlparse(raw)
                    qs = urllib.parse.parse_qs(parsed.query)
                    secret_candidate_list = qs.get("secret", [])
                    if not secret_candidate_list:
                        ok, result = False, "otpauth লিঙ্কে secret পাওয়া যায়নি।"
                    else:
                        ok, result = validate_2fa_secret(secret_candidate_list[0])
                except Exception:
                    ok, result = False, "otpauth লিঙ্ক সঠিক নয়।"
            else:
                ok, result = validate_2fa_secret(raw)

            if not ok:
                user_pending_action.pop(uid, None)
                m = await update.message.reply_text(
                    f"❌ {result}\n\nপ্রসেস ক্যান্সেল করা হয়েছে।\nমেইন মেনু থেকে আবার 2FA শুরু করুন।",
                    reply_markup=main_reply_keyboard(admin_flag),
                )
                last_bot_message_ids[uid] = m.message_id
                return

            secret = result
            label = update.effective_user.username or f"user{uid}"
            label_full = f"{label}@facebook"
            issuer = "Facebook"

            uri = (
                f"otpauth://totp/{urllib.parse.quote(label_full)}"
                f"?secret={secret}&issuer={urllib.parse.quote(issuer)}&digits=6&period=30"
            )

            code_line = ""
            try:
                totp = pyotp.TOTP(secret)
                code = totp.now()
                code_line = (
                    "\n\n🔢 *বর্তমান ৬-digit কোড:*\n"
                    f"`{code}`\n\n"
                    "এই কোডটি এখনই Facebook 2FA / Login Approval-এ দিয়ে ভেরিফাই করুন।"
                )
            except Exception:
                code_line = (
                    "\n\n⚠ কোড জেনারেট করা যায়নি। শুধু উপরের otpauth লিঙ্কটি "
                    "Authenticator অ্যাপে স্ক্যান করুন।"
                )

            msg = (
                "✅ আপনার 2FA Secret Key ফরম্যাট সঠিক।\n\n"
                "🔗 *otpauth URL (QR তৈরির জন্য):*\n"
                f"`{uri}`"
                f"{code_line}\n\n"
                "⚠ এই Secret এবং কোড কাউকে শেয়ার করবেন না।"
            )
            m = await update.message.reply_text(
                msg,
                parse_mode="Markdown",
                reply_markup=main_reply_keyboard(admin_flag),
            )
            last_bot_message_ids[uid] = m.message_id
            user_pending_action.pop(uid, None)
            return

        # ----- AUTO TOPUP FLOWS -----
        if act == "auto_topup_amount":
            if txt in ("10", "30", "50"):
                amount = int(txt)
                user_pending_action[uid] = {
                    "action": "auto_topup_method",
                    "amount": amount,
                }
                m = await update.message.reply_text(
                    f"আপনি {amount} টাকা এড করতে চান।\nএখন পেমেন্ট মেথড সিলেক্ট করুন:",
                    reply_markup=auto_method_keyboard(),
                )
                last_bot_message_ids[uid] = m.message_id
                return
            if txt == "✏️ টাকা লেখ":
                user_pending_action[uid] = {"action": "auto_topup_custom_amount"}
                m = await update.message.reply_text(
                    "কত টাকা এড করতে চান? (সর্বনিম্ন ১০ টাকা)",
                    reply_markup=ReplyKeyboardMarkup(
                        [
                            [KeyboardButton("⬅ Back"), KeyboardButton("❌ Cancel")]
                        ],
                        resize_keyboard=True,
                    ),
                )
                last_bot_message_ids[uid] = m.message_id
                return

            m = await update.message.reply_text(
                "❌ সঠিক পরিমাণ নির্বাচন করেননি।\nঅনুগ্রহ করে বাটন থেকে বেছে নিন অথবা '✏️ টাকা লেখ' চাপুন।",
                reply_markup=auto_amount_keyboard(),
            )
            last_bot_message_ids[uid] = m.message_id
            return

        if act == "auto_topup_custom_amount":
            try:
                amount = int(txt)
                if amount < 10:
                    raise ValueError
            except Exception:
                m = await update.message.reply_text(
                    "❌ পরিমাণ সঠিক নয় (সর্বনিম্ন ১০ টাকা)। আবার লিখুন অথবা Cancel চাপুন।",
                    reply_markup=ReplyKeyboardMarkup(
                        [
                            [KeyboardButton("⬅ Back"), KeyboardButton("❌ Cancel")]
                        ],
                        resize_keyboard=True,
                    ),
                )
                last_bot_message_ids[uid] = m.message_id
                return

            user_pending_action[uid] = {"action": "auto_topup_method", "amount": amount}
            m = await update.message.reply_text(
                f"আপনি {amount} টাকা এড করতে চান।\nএখন পেমেন্ট মেথড সিলেক্ট করুন:",
                reply_markup=auto_method_keyboard(),
            )
            last_bot_message_ids[uid] = m.message_id
            return

        if act == "auto_topup_method":
            amount = user_pending_action[uid]["amount"]
            lower = txt.lower()
            if lower not in ("বিকাশ", "নগদ"):
                m = await update.message.reply_text(
                    "❌ পেমেন্ট মেথড সঠিক নয়। আবার বেছে নিন অথবা Cancel চাপুন।",
                    reply_markup=auto_method_keyboard(),
                )
                last_bot_message_ids[uid] = m.message_id
                return

            method = "বিকাশ" if lower == "বিকাশ" else "নগদ"
            user_pending_action[uid] = {
                "action": "auto_topup_wait_verify",
                "amount": amount,
                "method": method,
            }

            method_text = (
                "বিকাশ অ্যাপ এ যান, Send Money করুন।"
                if method == "বিকাশ"
                else "নগদ অ্যাপ এ যান, Send Money করুন।"
            )

            msg = (
                f"{method_text}\n"
                "অবশ্যই Transaction ID এবং যেই নাম্বার থেকে টাকা পাঠাবেন সেটার শেষ ৪ ডিজিট মনে রাখুন।\n\n"
                "প্রাপক নাম্বার (কপি করার জন্য):\n"
                f"`{PAYMENT_NUMBER}`\n\n"
                "লেনদেন শেষ হলে নিচের '✅ Verification' বাটনে চাপ দিন।"
            )
            m = await update.message.reply_text(
                msg,
                parse_mode="Markdown",
                reply_markup=auto_verify_keyboard(),
            )
            last_bot_message_ids[uid] = m.message_id
            return

        if act == "auto_topup_wait_verify":
            if txt == "✅ Verification":
                amount = user_pending_action[uid]["amount"]
                method = user_pending_action[uid]["method"]
                user_pending_action[uid] = {
                    "action": "auto_topup_ask_txid",
                    "amount": amount,
                    "method": method,
                }
                m = await update.message.reply_text(
                    "Transaction ID (উদাহরণ: `CKL9****`) এবং যে নাম্বার থেকে টাকা পাঠিয়েছেন তার শেষ ৪ সংখ্যা দিন।\n\n"
                    "ফরম্যাট:\n`TXID 1234`",
                    parse_mode="Markdown",
                    reply_markup=ReplyKeyboardMarkup(
                        [
                            [KeyboardButton("⬅ Back"), KeyboardButton("❌ Cancel")]
                        ],
                        resize_keyboard=True,
                    ),
                )
                last_bot_message_ids[uid] = m.message_id
                return

            m = await update.message.reply_text(
                "❌ প্রথমে '✅ Verification' বাটনে চাপ দিন, অথবা Cancel করুন।",
                reply_markup=auto_verify_keyboard(),
            )
            last_bot_message_ids[uid] = m.message_id
            return

        if act == "auto_topup_ask_txid":
            parts = txt.split()
            if len(parts) != 2:
                m = await update.message.reply_text(
                    "❌ ফরম্যাট ঠিক নেই। উদাহরণ: `ABCD1234 1234`\nআবার চেষ্টা করুন বা Cancel করুন।",
                    reply_markup=ReplyKeyboardMarkup(
                        [
                            [KeyboardButton("⬅ Back"), KeyboardButton("❌ Cancel")]
                        ],
                        resize_keyboard=True,
                    ),
                )
                last_bot_message_ids[uid] = m.message_id
                return

            txid, last4 = parts[0], parts[1]
            if not re.match(r"^[0-9]{4}$", last4):
                m = await update.message.reply_text(
                    "❌ শেষ ৪ সংখ্যা সঠিক নয়। আবার লিখুন অথবা Cancel করুন।",
                    reply_markup=ReplyKeyboardMarkup(
                        [
                            [KeyboardButton("⬅ Back"), KeyboardButton("❌ Cancel")]
                        ],
                        resize_keyboard=True,
                    ),
                )
                last_bot_message_ids[uid] = m.message_id
                return

            amount = user_pending_action[uid]["amount"]
            method = user_pending_action[uid]["method"]
            user_pending_action.pop(uid, None)

            verify_id = f"{uid}_{int(datetime.utcnow().timestamp())}"
            username = update.effective_user.username or "-"
            full_name = update.effective_user.full_name or "-"
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            pending_topups[verify_id] = {
                "user_id": uid,
                "username": username,
                "full_name": full_name,
                "amount": amount,
                "method": method,
                "txid": txid,
                "last4": last4,
                "time": now_str,
            }

            m = await update.message.reply_text(
                "✅ আপনার অটো Add Balance অনুরোধ পাঠানো হয়েছে।\n"
                "১–৫ মিনিটের মধ্যে ব্যালেন্স আপডেট হয়ে যাবে (Admin verification এর পর)।",
                reply_markup=main_reply_keyboard(admin_flag),
            )
            last_bot_message_ids[uid] = m.message_id

            if ADMIN_ID:
                safe_username = username if username not in (None, "", "-") else "-"
                text = (
                    "💳 নতুন অটো Add Balance রিকোয়েস্ট\n"
                    "--------------------------------\n"
                    f"User: {full_name} (@{safe_username})\n"
                    f"User ID: {uid}\n"
                    f"Amount (User selected): {amount} টাকা\n"
                    f"Method: {method}\n"
                    f"TXID: {txid}\n"
                    f"Sender last 4: {last4}\n"
                    f"Time: {now_str}\n"
                    f"Request ID: {verify_id}"
                )

                kb = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "✅ Confirm", callback_data=f"topup_confirm:{verify_id}"
                            ),
                            InlineKeyboardButton(
                                "❌ সঠিক নয়",
                                callback_data=f"topup_wrong:{verify_id}",
                            ),
                        ],
                        [
                            InlineKeyboardButton(
                                "📣 Notice", callback_data=f"topup_notice:{verify_id}"
                            ),
                            InlineKeyboardButton(
                                "🚫 Spam block করুন",
                                callback_data=f"topup_block:{verify_id}",
                            ),
                        ],
                    ]
                )

                try:
                    await context.bot.send_message(
                        chat_id=int(ADMIN_ID),
                        text=text,
                        reply_markup=kb,
                    )
                except Exception as e:
                    print("ADMIN_NOTIFY_ERROR:", e)

            return

    # ===== ADMIN SIDE ACTIONS =====
    if admin_flag:
        # ব্যালেন্স কন্ট্রোল সাব-মেনু
        if txt.startswith("💳 Admin: Balance"):
            admin_pending_action[uid] = {"action": "balance_menu"}
            kb = ReplyKeyboardMarkup(
                [
                    [
                        KeyboardButton("➕ Admin: Add Balance"),
                        KeyboardButton("🔻 Admin: Remove Balance"),
                    ],
                    [KeyboardButton("⬅ Back"), KeyboardButton("❌ Cancel")],
                ],
                resize_keyboard=True,
            )
            m = await update.message.reply_text(
                "আপনি কি ব্যালেন্স যোগ করবেন নাকি কমাবেন?",
                reply_markup=kb,
            )
            last_bot_message_ids[uid] = m.message_id
            return

        # টপআপ কনফার্ম এমাউন্ট
        if (
            uid in admin_pending_action
            and admin_pending_action[uid].get("action") == "topup_confirm_amount"
        ):
            verify_id = admin_pending_action[uid]["verify_id"]
            top = pending_topups.get(verify_id)
            if not top:
                admin_pending_action.pop(uid, None)
                m = await update.message.reply_text(
                    "❌ এই রিকোয়েস্ট পাওয়া যাচ্ছে না।",
                    reply_markup=admin_reply_keyboard(),
                )
                last_bot_message_ids[uid] = m.message_id
                return
            try:
                add_amount = float(txt)
            except Exception:
                admin_pending_action.pop(uid, None)
                m = await update.message.reply_text(
                    "❌ পরিমাণ সঠিক নয়। কনফার্ম প্রসেস ক্যান্সেল করা হয়েছে।",
                    reply_markup=admin_reply_keyboard(),
                )
                last_bot_message_ids[uid] = m.message_id
                return

            target_id = top["user_id"]
            user_balance[target_id] = user_balance.get(target_id, 0.0) + add_amount
            save_users()

            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=(
                        f"✅ আপনার ব্যালেন্স আপডেট হয়েছে: +{add_amount} টাকা\n"
                        f"🔢 নতুন ব্যালেন্স: {user_balance[target_id]} টাকা"
                    ),
                )
            except Exception:
                pass

            m = await update.message.reply_text(
                f"✅ {target_id} ইউজারের ব্যালেন্সে {add_amount} টাকা যোগ করা হয়েছে।",
                reply_markup=admin_reply_keyboard(),
            )
            last_bot_message_ids[uid] = m.message_id

            pending_topups.pop(verify_id, None)
            admin_pending_action.pop(uid, None)
            return

        # Notice
        if (
            uid in admin_pending_action
            and admin_pending_action[uid].get("action") == "topup_notice"
        ):
            verify_id = admin_pending_action[uid]["verify_id"]
            top = pending_topups.get(verify_id)
            if not top:
                admin_pending_action.pop(uid, None)
                m = await update.message.reply_text(
                    "❌ এই রিকোয়েস্ট আর নেই।",
                    reply_markup=admin_reply_keyboard(),
                )
                last_bot_message_ids[uid] = m.message_id
                return

            target_id = top["user_id"]
            notice = txt
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"📣 *Admin Notice:*\n{notice}",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

            m = await update.message.reply_text(
                "✅ Notice পাঠানো হয়েছে।", reply_markup=admin_reply_keyboard()
            )
            last_bot_message_ids[uid] = m.message_id
            admin_pending_action.pop(uid, None)
            return

        # Spam block confirm
        if (
            uid in admin_pending_action
            and admin_pending_action[uid].get("action") == "topup_block_confirm"
        ):
            verify_id = admin_pending_action[uid]["verify_id"]
            top = pending_topups.get(verify_id)
            if not top:
                admin_pending_action.pop(uid, None)
                m = await update.message.reply_text(
                    "❌ এই রিকোয়েস্ট আর নেই।",
                    reply_markup=admin_reply_keyboard(),
                )
                last_bot_message_ids[uid] = m.message_id
                return

            ans = txt.strip().lower()
            if ans in ("হ্যাঁ", "হা", "yes", "ji", "জি"):
                target_id = top["user_id"]
                blocked_users.add(target_id)
                save_users()
                try:
                    await context.bot.send_message(
                        chat_id=target_id,
                        text="🚫 আপনার অ্যাকাউন্টটি ব্লক করা হয়েছে। যদি ভুল মনে করেন, Support এ যোগাযোগ করুন।",
                    )
                except Exception:
                    pass
                m = await update.message.reply_text(
                    f"✅ User {target_id} ব্লক করা হয়েছে।",
                    reply_markup=admin_reply_keyboard(),
                )
                last_bot_message_ids[uid] = m.message_id
                pending_topups.pop(verify_id, None)
                admin_pending_action.pop(uid, None)
                return
            else:
                admin_pending_action.pop(uid, None)
                m = await update.message.reply_text(
                    "ℹ️ ব্লক অপারেশন ক্যান্সেল করা হয়েছে।",
                    reply_markup=admin_reply_keyboard(),
                )
                last_bot_message_ids[uid] = m.message_id
                return

        # ==== পুরনো অ্যাডমিন ফিচার ====

        # Add balance (manual adjust)
        if txt.startswith("➕ Admin: Add Balance"):
            admin_pending_action[uid] = {"action": "add_balance"}
            m = await update.message.reply_text(
                "ফরম্যাট:\n`user_id amount`\nউদাহরণ: `123456789 50`",
                parse_mode="Markdown",
            )
            last_bot_message_ids[uid] = m.message_id
            return

        if (
            uid in admin_pending_action
            and admin_pending_action[uid].get("action") == "add_balance"
            and not txt.startswith("➕ Admin: Add Balance")
        ):
            try:
                parts = txt.split()
                target_id = int(parts[0])
                amount = float(parts[1])
                user_balance[target_id] = user_balance.get(target_id, 0.0) + amount
                save_users()
                try:
                    await context.bot.send_message(
                        target_id,
                        f"💰 আপনার ব্যালেন্স পরিবর্তন হয়েছে। Amount: {amount} টাকা।",
                    )
                except Exception:
                    pass
                m = await update.message.reply_text(
                    f"✅ {amount} টাকা এডজাস্ট করা হয়েছে। ইউজার ID: {target_id}",
                    reply_markup=admin_reply_keyboard(),
                )
            except Exception:
                m = await update.message.reply_text(
                    "❌ ফরম্যাট ঠিক নেই। উদাহরণ: `123456789 50`",
                    parse_mode="Markdown",
                    reply_markup=admin_reply_keyboard(),
                )
            last_bot_message_ids[uid] = m.message_id
            admin_pending_action.pop(uid, None)
            return

        # Remove balance
        if txt.startswith("🔻 Admin: Remove Balance"):
            admin_pending_action[uid] = {"action": "remove_balance"}
            m = await update.message.reply_text(
                "যার থেকে টাকা কমাবেন, লিখুন:\n`user_id amount`\nউদাহরণ: `123456789 20`",
                parse_mode="Markdown",
            )
            last_bot_message_ids[uid] = m.message_id
            return

        if (
            uid in admin_pending_action
            and admin_pending_action[uid].get("action") == "remove_balance"
            and not txt.startswith("🔻 Admin: Remove Balance")
        ):
            try:
                parts = txt.split()
                target_id = int(parts[0])
                amount = float(parts[1])
                user_balance[target_id] = user_balance.get(target_id, 0.0) - amount
                save_users()
                try:
                    await context.bot.send_message(
                        target_id,
                        f"💰 আপনার ব্যালেন্স থেকে {amount} টাকা কমানো হয়েছে। বর্তমান ব্যালেন্স: {user_balance[target_id]}",
                    )
                except Exception:
                    pass
                m = await update.message.reply_text(
                    f"✅ {amount} টাকা রিমুভ করা হয়েছে। ইউজার ID: {target_id}",
                    reply_markup=admin_reply_keyboard(),
                )
            except Exception:
                m = await update.message.reply_text(
                    "❌ ফরম্যাট ঠিক নেই। উদাহরণ: `123456789 20`",
                    parse_mode="Markdown",
                    reply_markup=admin_reply_keyboard(),
                )
            last_bot_message_ids[uid] = m.message_id
            admin_pending_action.pop(uid, None)
            return

        # Add Emails
        if txt.startswith("✉️ Admin: Add Emails"):
            admin_pending_action[uid] = {"action": "add_emails_choose_type"}
            available = ", ".join(sorted(PRICES.keys()))
            m = await update.message.reply_text(
                "যে মেইল টাইপে যোগ করতে চান তার key লিখুন (যেমন: hotmail)\n"
                f"উপলব্ধ: {available}",
                reply_markup=admin_reply_keyboard(),
            )
            last_bot_message_ids[uid] = m.message_id
            return

        if (
            uid in admin_pending_action
            and admin_pending_action[uid].get("action") == "add_emails_choose_type"
        ):
            mail_type = txt.strip().lower()
            if mail_type not in PRICES:
                m = await update.message.reply_text(
                    "❌ এই নামের কোন মেইল টাইপ পাওয়া যায়নি।\n"
                    "আগে Mail Types থেকে টাইপ যোগ করুন অথবা সঠিক নাম দিন।",
                    reply_markup=admin_reply_keyboard(),
                )
                last_bot_message_ids[uid] = m.message_id
                return
            admin_pending_action[uid] = {"action": "add_emails", "type": mail_type}
            m = await update.message.reply_text(
                f"এখন `{mail_type}` টাইপের জন্য Excel (.xlsx) ফাইল পাঠান।",
                parse_mode="Markdown",
                reply_markup=admin_reply_keyboard(),
            )
            last_bot_message_ids[uid] = m.message_id
            return

        # Mail Types Manager
        if txt.startswith("🧩 Admin: Mail Types"):
            admin_pending_action[uid] = {"action": "mail_types"}
            helper = (
                "🧩 *Mail Types Manager*\n\n"
                "`add <name> <price>`  ➜ নতুন টাইপ যোগ\n"
                "`del <name>`          ➜ টাইপ মুছে ফেলুন\n"
                "`price <name> <p>`    ➜ নির্দিষ্ট টাইপের দাম ঠিক করুন\n"
                "`list`                ➜ সব টাইপ + স্টক দেখুন\n"
                "`done` বা `cancel`    ➜ বের হয়ে যান\n\n"
                "উদাহরণ:\n"
                "`add yahoo 2.5`\n"
                "`price gmail 5.2`\n"
                "`del outlook`"
            )
            m = await update.message.reply_text(
                helper, parse_mode="Markdown", reply_markup=admin_reply_keyboard()
            )
            last_bot_message_ids[uid] = m.message_id
            return

        if (
            uid in admin_pending_action
            and admin_pending_action[uid].get("action") == "mail_types"
        ):
            lower = txt.lower().strip()
            if lower in ("done", "cancel"):
                admin_pending_action.pop(uid, None)
                m = await update.message.reply_text(
                    "✅ Mail Types ম্যানেজার থেকে বের হয়ে এসেছেন।",
                    reply_markup=admin_reply_keyboard(),
                )
                last_bot_message_ids[uid] = m.message_id
                return

            if lower == "list":
                if not PRICES:
                    m = await update.message.reply_text(
                        "কোন মেইল টাইপ সেট করা নেই।",
                        reply_markup=admin_reply_keyboard(),
                    )
                    last_bot_message_ids[uid] = m.message_id
                    return
                lines = []
                for mt, price in PRICES.items():
                    stock = len(emails.get(mt, []))
                    lines.append(f"▫️ {mt.upper()} — 💰 {price} টাকা | 📦 {stock} টি")
                msg = "*Mail Types:*\n\n" + "\n".join(lines)
                m = await update.message.reply_text(
                    msg, parse_mode="Markdown", reply_markup=admin_reply_keyboard()
                )
                last_bot_message_ids[uid] = m.message_id
                return

            if lower.startswith("add "):
                try:
                    _, name, p = txt.split(maxsplit=2)
                    key = name.strip().lower()
                    if not re.match(r"^[a-z0-9_]+$", key):
                        m = await update.message.reply_text(
                            "❌ টাইপ নাম শুধুমাত্র a-z, 0-9 এবং _ হতে পারবে।",
                            reply_markup=admin_reply_keyboard(),
                        )
                        last_bot_message_ids[uid] = m.message_id
                        return
                    price = float(p)
                    if key in PRICES:
                        m = await update.message.reply_text(
                            "❌ এই নামে আগে থেকেই টাইপ আছে।",
                            reply_markup=admin_reply_keyboard(),
                        )
                        last_bot_message_ids[uid] = m.message_id
                        return
                    PRICES[key] = price
                    emails.setdefault(key, [])
                    save_emails()
                    save_users()
                    m = await update.message.reply_text(
                        f"✅ নতুন মেইল টাইপ `{key}` যোগ হয়েছে। দাম: {price}",
                        parse_mode="Markdown",
                        reply_markup=admin_reply_keyboard(),
                    )
                except Exception:
                    m = await update.message.reply_text(
                        "❌ ফরম্যাট ঠিক নেই। উদাহরণ: `add yahoo 2.5`",
                        parse_mode="Markdown",
                        reply_markup=admin_reply_keyboard(),
                    )
                last_bot_message_ids[uid] = m.message_id
                return

            if lower.startswith("del "):
                try:
                    _, name = txt.split(maxsplit=1)
                    key = name.strip().lower()
                    if key not in PRICES:
                        m = await update.message.reply_text(
                            "❌ এই নামে কোন টাইপ নেই।",
                            reply_markup=admin_reply_keyboard(),
                        )
                        last_bot_message_ids[uid] = m.message_id
                        return
                    PRICES.pop(key, None)
                    emails.pop(key, None)
                    save_emails()
                    save_users()
                    m = await update.message.reply_text(
                        f"✅ `{key}` টাইপটি মুছে ফেলা হয়েছে।",
                        parse_mode="Markdown",
                        reply_markup=admin_reply_keyboard(),
                    )
                except Exception:
                    m = await update.message.reply_text(
                        "❌ ফরম্যাট ঠিক নেই। উদাহরণ: `del outlook`",
                        parse_mode="Markdown",
                        reply_markup=admin_reply_keyboard(),
                    )
                last_bot_message_ids[uid] = m.message_id
                return

            if lower.startswith("price "):
                try:
                    _, name, p = txt.split(maxsplit=2)
                    key = name.strip().lower()
                    if key not in PRICES:
                        m = await update.message.reply_text(
                            "❌ এই নামে কোন টাইপ নেই।",
                            reply_markup=admin_reply_keyboard(),
                        )
                        last_bot_message_ids[uid] = m.message_id
                        return
                    price = float(p)
                    PRICES[key] = price
                    save_users()
                    m = await update.message.reply_text(
                        f"✅ `{key}` এর নতুন দাম: {price}",
                        parse_mode="Markdown",
                        reply_markup=admin_reply_keyboard(),
                    )
                except Exception:
                    m = await update.message.reply_text(
                        "❌ ফরম্যাট ঠিক নেই। উদাহরণ: `price gmail 5.0`",
                        parse_mode="Markdown",
                        reply_markup=admin_reply_keyboard(),
                    )
                last_bot_message_ids[uid] = m.message_id
                return

            m = await update.message.reply_text(
                "❌ অজানা কমান্ড। `list`, `add`, `del`, `price`, `done` ব্যবহার করুন।",
                parse_mode="Markdown",
                reply_markup=admin_reply_keyboard(),
            )
            last_bot_message_ids[uid] = m.message_id
            return

        # Admin Users manager
        if txt.startswith("👥 Admin: Users"):
            admin_pending_action[uid] = {"action": "users_manager"}
            msg = (
                "👥 *Users Manager*\n\n"
                "`list` ➜ সব ইউজার + ব্যালেন্স দেখুন\n"
                "`id <uid>` ➜ নির্দিষ্ট ইউজারের ডিটেলস\n"
                "`block <uid>` ➜ ইউজার ব্লক\n"
                "`unblock <uid>` ➜ ইউজার আনব্লক\n"
                "`done` ➜ বের হয়ে যান"
            )
            m = await update.message.reply_text(
                msg, parse_mode="Markdown", reply_markup=admin_reply_keyboard()
            )
            last_bot_message_ids[uid] = m.message_id
            return

        if (
            uid in admin_pending_action
            and admin_pending_action[uid].get("action") == "users_manager"
        ):
            lower = txt.lower().strip()
            if lower in ("done", "cancel"):
                admin_pending_action.pop(uid, None)
                m = await update.message.reply_text(
                    "✅ Users Manager থেকে বের হয়ে এসেছেন।",
                    reply_markup=admin_reply_keyboard(),
                )
                last_bot_message_ids[uid] = m.message_id
                return

            if lower == "list":
                if not user_balance:
                    m = await update.message.reply_text(
                        "কোন ইউজার নেই।", reply_markup=admin_reply_keyboard()
                    )
                    last_bot_message_ids[uid] = m.message_id
                    return
                lines = []
                for u, bal in user_balance.items():
                    info = user_info.get(u, {})
                    name = info.get("full_name") or "-"
                    uname = info.get("username") or "-"
                    lines.append(
                        f"🆔 `{u}` | 💰 {bal} | 👤 {name} (@{uname})"
                    )
                msg = "👥 *Users List:*\n\n" + "\n".join(lines)
                m = await update.message.reply_text(
                    msg, parse_mode="Markdown", reply_markup=admin_reply_keyboard()
                )
                last_bot_message_ids[uid] = m.message_id
                return

            if lower.startswith("id "):
                try:
                    _, sid = txt.split(maxsplit=1)
                    target = int(sid)
                except Exception:
                    m = await update.message.reply_text(
                        "❌ ফরম্যাট: `id 123456789`",
                        parse_mode="Markdown",
                        reply_markup=admin_reply_keyboard(),
                    )
                    last_bot_message_ids[uid] = m.message_id
                    return
                bal = user_balance.get(target, 0.0)
                info = user_info.get(target, {})
                name = info.get("full_name") or "-"
                uname = info.get("username") or "-"
                purchased = len(user_purchased_emails.get(target, []))
                block_status = "✅ Not blocked" if target not in blocked_users else "🚫 Blocked"
                msg = (
                    f"🆔 User ID: `{target}`\n"
                    f"👤 Name: {name}\n"
                    f"🔗 Username: @{uname}\n"
                    f"💰 Balance: {bal}\n"
                    f"📦 Purchased mails: {purchased}\n"
                    f"🚧 Status: {block_status}"
                )
                m = await update.message.reply_text(
                    msg, parse_mode="Markdown", reply_markup=admin_reply_keyboard()
                )
                last_bot_message_ids[uid] = m.message_id
                return

            if lower.startswith("block "):
                try:
                    _, sid = txt.split(maxsplit=1)
                    target = int(sid)
                except Exception:
                    m = await update.message.reply_text(
                        "❌ ফরম্যাট: `block 123456789`",
                        parse_mode="Markdown",
                        reply_markup=admin_reply_keyboard(),
                    )
                    last_bot_message_ids[uid] = m.message_id
                    return
                blocked_users.add(target)
                save_users()
                m = await update.message.reply_text(
                    f"🚫 User `{target}` ব্লক করা হয়েছে।",
                    parse_mode="Markdown",
                    reply_markup=admin_reply_keyboard(),
                )
                last_bot_message_ids[uid] = m.message_id
                return

            if lower.startswith("unblock "):
                try:
                    _, sid = txt.split(maxsplit=1)
                    target = int(sid)
                except Exception:
                    m = await update.message.reply_text(
                        "❌ ফরম্যাট: `unblock 123456789`",
                        parse_mode="Markdown",
                        reply_markup=admin_reply_keyboard(),
                    )
                    last_bot_message_ids[uid] = m.message_id
                    return
                blocked_users.discard(target)
                save_users()
                m = await update.message.reply_text(
                    f"✅ User `{target}` আনব্লক করা হয়েছে।",
                    parse_mode="Markdown",
                    reply_markup=admin_reply_keyboard(),
                )
                last_bot_message_ids[uid] = m.message_id
                return

            m = await update.message.reply_text(
                "❌ অজানা কমান্ড। `list`, `id`, `block`, `unblock`, `done` ব্যবহার করুন।",
                parse_mode="Markdown",
                reply_markup=admin_reply_keyboard(),
            )
            last_bot_message_ids[uid] = m.message_id
            return

        # Notify
        if txt.startswith("📢 Admin: Notify"):
            admin_pending_action[uid] = {"action": "notify"}
            m = await update.message.reply_text(
                "যে মেসেজ সবার কাছে পাঠাতে চান, সেটা এখন পাঠান।",
                reply_markup=admin_reply_keyboard(),
            )
            last_bot_message_ids[uid] = m.message_id
            return

        if (
            uid in admin_pending_action
            and admin_pending_action[uid].get("action") == "notify"
        ):
            message = txt
            for user in list(user_balance.keys()):
                try:
                    await context.bot.send_message(user, message)
                except Exception:
                    continue
            m = await update.message.reply_text(
                "✅ Notification পাঠানো হয়েছে।", reply_markup=admin_reply_keyboard()
            )
            last_bot_message_ids[uid] = m.message_id
            admin_pending_action.pop(uid, None)
            return

        # Backup
        if txt.startswith("📁 Admin: Backup"):
            backup_data = {
                "users": {
                    "balance": {str(k): float(v) for k, v in user_balance.items()},
                    "blocked": list(int(u) for u in blocked_users),
                    "purchased": {
                        str(k): list(v) for k, v in user_purchased_emails.items()
                    },
                    "info": {str(k): v for k, v in user_info.items()},
                },
                "emails": emails,
                "prices": PRICES,
            }
            backup_path = os.path.join(HOME_DIR, "bot_backup.json")
            with open(backup_path, "w") as f:
                json.dump(backup_data, f, indent=4, ensure_ascii=False)

            try:
                with open(backup_path, "rb") as f:
                    await context.bot.send_document(
                        chat_id=uid,
                        document=f,
                        filename="bot_backup.json",
                        caption="📁 Full backup generated.",
                    )
            except Exception:
                m = await update.message.reply_text(
                    "❌ Backup ফাইল পাঠাতে সমস্যা হয়েছে।",
                    reply_markup=admin_reply_keyboard(),
                )
                last_bot_message_ids[uid] = m.message_id
                return

            m = await update.message.reply_text(
                "✅ Backup প্রস্তুত এবং পাঠানো হয়েছে।",
                reply_markup=admin_reply_keyboard(),
            )
            last_bot_message_ids[uid] = m.message_id
            return

        # Recover
        if txt.startswith("🔄 Admin: Recover"):
            admin_pending_action[uid] = {"action": "recover_wait_file"}
            m = await update.message.reply_text(
                "পূর্বের backup JSON ফাইল (`bot_backup.json`) পাঠান।",
                reply_markup=admin_reply_keyboard(),
            )
            last_bot_message_ids[uid] = m.message_id
            return

    # ===== MAIL BUY BUTTONS =====
    # যেকোন টাইপের "XXX Mail" বাটনের জন্য কাজ করবে
    if txt.endswith(" Mail"):
        parts = txt.split()
        if len(parts) >= 2:
            mail_type_key = parts[1].lower()
            await buy_single_mail(update, context, mail_type_key)
            return

    # ===== fallback =====
    m = await update.message.reply_text(
        "বুঝতে পারিনি — নিচের মেনু থেকে অপশন সিলেক্ট করুন।",
        reply_markup=main_reply_keyboard(admin_flag),
    )
    last_bot_message_ids[uid] = m.message_id


# ===== BUY MAIL =====
async def buy_single_mail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, mail_type: str
):
    uid = update.effective_user.id
    admin_flag = is_admin(uid)

    await delete_last_bot_message(update)

    if mail_type not in emails or not isinstance(emails.get(mail_type), list):
        m = await update.message.reply_text(
            "❌ এই টাইপের মেইল সাপোর্ট করা হচ্ছে না।",
            reply_markup=main_reply_keyboard(admin_flag),
        )
        last_bot_message_ids[uid] = m.message_id
        return

    if len(emails[mail_type]) == 0:
        m = await update.message.reply_text(
            "❌ এই টাইপের মেইল স্টক শেষ।",
            reply_markup=main_reply_keyboard(admin_flag),
        )
        last_bot_message_ids[uid] = m.message_id
        return

    price = PRICES.get(mail_type, 0.0)
    if user_balance.get(uid, 0.0) < price:
        m = await update.message.reply_text(
            "❌ পর্যাপ্ত ব্যালেন্স নেই।",
            reply_markup=main_reply_keyboard(admin_flag),
        )
        last_bot_message_ids[uid] = m.message_id
        return

    prev_balance = user_balance.get(uid, 0.0)
    await update.message.reply_text(
        f"💰 {mail_type.upper()} মেইল দাম: {price} টাকা\n"
        f"আপনার আগের ব্যালেন্স: {prev_balance} টাকা"
    )

    user_balance[uid] = prev_balance - price
    mail = emails[mail_type].pop(0)
    save_emails()

    user_purchased_emails.setdefault(uid, []).append(mail)
    save_users()

    mail_msg = await update.message.reply_text(
        mail, disable_web_page_preview=True
    )
    user_purchased_mail_message_ids.setdefault(uid, []).append(mail_msg.message_id)

    m = await update.message.reply_text(
        f"✅ আপনার মেইল সফলভাবে দেওয়া হয়েছে।\n\n"
        f"💰 নতুন ব্যালেন্স: {user_balance[uid]} টাকা",
        reply_markup=main_reply_keyboard(admin_flag),
    )
    last_bot_message_ids[uid] = m.message_id


# ===== CALLBACK HANDLER =====
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data or ""

    if data.startswith("topup_"):
        if uid != ADMIN_ID:
            await query.message.reply_text("❌ এই অপশন শুধুমাত্র Admin এর জন্য।")
            return

        try:
            action, verify_id = data.split(":", 1)
        except ValueError:
            return

        top = pending_topups.get(verify_id)

        if action == "topup_confirm":
            if not top:
                await query.message.reply_text("❌ এই রিকোয়েস্ট আর নেই।")
                return
            admin_pending_action[uid] = {
                "action": "topup_confirm_amount",
                "verify_id": verify_id,
            }
            await query.message.reply_text(
                f"কত টাকা এড করবেন? (User select: {top['amount']} টাকা)\nID: {verify_id}"
            )
            return

        if not top and action != "topup_block":
            await query.message.reply_text("❌ এই রিকোয়েস্ট আর নেই।")
            return

        if action == "topup_wrong":
            target_id = top["user_id"]
            try:
                kb = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "✉️ Support", url="https://t.me/mailbuysupport"
                            )
                        ]
                    ]
                )
                await context.bot.send_message(
                    chat_id=target_id,
                    text="❌ আপনার দেওয়া ট্রানজেকশন তথ্য সঠিক নয় — দয়া করে Support এ যোগাযোগ করুন।",
                    reply_markup=kb,
                )
            except Exception:
                pass
            await query.message.reply_text("ℹ️ ইউজারকে জানিয়ে দেয়া হয়েছে।")
            pending_topups.pop(verify_id, None)
            return

        if action == "topup_notice":
            admin_pending_action[uid] = {
                "action": "topup_notice",
                "verify_id": verify_id,
            }
            await query.message.reply_text("ইউজারকে পাঠানোর জন্য Notice লিখে পাঠান:")
            return

        if action == "topup_block":
            admin_pending_action[uid] = {
                "action": "topup_block_confirm",
                "verify_id": verify_id,
            }
            await query.message.reply_text(
                "সত্যি কি তাকে ব্লক করতে চান? `হ্যাঁ` / `না` লিখুন।"
            )
            return

    if data == "cancel":
        if uid in admin_pending_action:
            admin_pending_action.pop(uid, None)
        await query.message.reply_text(
            "🚫 Operation cancelled.",
            reply_markup=main_reply_keyboard(is_admin(uid)),
        )


# ===== DOCUMENT HANDLER =====
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global emails, PRICES, user_balance, blocked_users, user_purchased_emails, user_info

    uid = update.effective_user.id
    if uid not in admin_pending_action:
        return

    action = admin_pending_action[uid].get("action")
    if action not in ("add_emails", "recover_wait_file"):
        return

    if not update.message or not update.message.document:
        return

    file = await update.message.document.get_file()
    filename = update.message.document.file_name or "file"

    if action == "add_emails":
        mail_type = admin_pending_action[uid]["type"]
        file_path = os.path.join(HOME_DIR, "upload.xlsx")
        await file.download_to_drive(file_path)

        wb = openpyxl.load_workbook(file_path)
        ws = wb.active
        count = 0
        for row in ws.iter_rows(values_only=True):
            for cell in row:
                if cell and isinstance(cell, str):
                    emails[mail_type].append(cell.strip())
                    count += 1
        save_emails()

        m = await update.message.reply_text(
            f"✅ {count} টি `{mail_type}` মেইল যোগ করা হয়েছে।",
            parse_mode="Markdown",
            reply_markup=admin_reply_keyboard(),
        )
        last_bot_message_ids[uid] = m.message_id
        admin_pending_action.pop(uid, None)
        return

    if action == "recover_wait_file":
        if not filename.lower().endswith(".json"):
            m = await update.message.reply_text(
                "❌ দয়া করে JSON backup ফাইল পাঠান (যেমন: bot_backup.json)।",
                reply_markup=admin_reply_keyboard(),
            )
            last_bot_message_ids[uid] = m.message_id
            return

        backup_path = os.path.join(HOME_DIR, "restore_backup.json")
        await file.download_to_drive(backup_path)

        try:
            with open(backup_path, "r") as f:
                data = json.load(f)

            emails = data.get("emails", {})
            PRICES = {k: float(v) for k, v in data.get("prices", {}).items()}

            users_data = data.get("users", {})
            user_balance = {
                int(k): float(v) for k, v in users_data.get("balance", {}).items()
            }
            blocked_users = set(int(u) for u in users_data.get("blocked", []))
            user_purchased_emails = {
                int(k): list(v) for k, v in users_data.get("purchased", {}).items()
            }
            user_info = {
                int(k): dict(v) for k, v in users_data.get("info", {}).items()
            }

            save_emails()
            save_users()

            m = await update.message.reply_text(
                "✅ Backup থেকে সফলভাবে পুনরুদ্ধার করা হয়েছে।",
                reply_markup=admin_reply_keyboard(),
            )
            last_bot_message_ids[uid] = m.message_id
        except Exception as e:
            m = await update.message.reply_text(
                f"❌ Recover করতে সমস্যা হয়েছে: {e}",
                reply_markup=admin_reply_keyboard(),
            )
            last_bot_message_ids[uid] = m.message_id

        admin_pending_action.pop(uid, None)
        return


# ===== SLASH COMMANDS =====
async def cmd_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    admin_flag = is_admin(uid)
    await delete_last_bot_message(update)
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✉️ Support Group", url="https://t.me/mailbuysupport"
                )
            ],
            [
                InlineKeyboardButton(
                    "📩 যোগাযোগ (বট)", url="https://t.me/Mailbazar_support_bot"
                )
            ],
        ]
    )
    await update.message.reply_text(
        "Support এ যেতে নিচের যেকোন বাটনে চাপ দিন:",
        reply_markup=kb,
    )
    m2 = await update.message.reply_text(
        "🔙 মূল মেনু:",
        reply_markup=main_reply_keyboard(admin_flag),
    )
    last_bot_message_ids[uid] = m2.message_id


async def cmd_addbalance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    admin_flag = is_admin(uid)
    await delete_last_bot_message(update)
    m = await update.message.reply_text(
        "আপনি কিভাবে টাকা এড করতে চান?",
        reply_markup=add_balance_menu_keyboard(),
    )
    last_bot_message_ids[uid] = m.message_id


# ===== MAIN =====
def main():
    if not TOKEN or TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("Please set your bot token in TOKEN variable.")

    load_users()

    async def post_init(application):
        try:
            await application.bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("support", cmd_support))
    app.add_handler(CommandHandler("addbalance", cmd_addbalance))
    app.add_handler(CommandHandler("add_balance", cmd_addbalance))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_menu_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(callback_handler))

    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()