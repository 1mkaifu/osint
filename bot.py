# bot.py
# -*- coding: utf-8 -*-

import re
import sqlite3
import requests
import telebot
import time
from telebot import types
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import logging
import gc
import os
from datetime import date
import threading
from flask import Flask
from threading import Thread
import socket

# ========== CONFIG ==========
# एनवायरनमेंट वेरिएबल्स से टोकन प्राप्त करें
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8463575200:AAHvaIN5nBh3r5WVc2vHH9inTVex7-jSQvk")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7945122206"))
DB_FILE = "users.db"

# ========== LOGGING SETUP ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ========== SPECIAL USER CONFIG ==========
SPECIAL_USERS = [
    {"id": 7945122206, "name": "Friend"},
    {"id": 8490964965, "name": "."},
    {"id": 5838583388, "name": "guruji"},
    {"id": 6471305444, "name": "bhai"},
    {"id": 7838600992, "name": "m"},
    {"id": 8252673561, "name": "PART 2"},
    {"id": 1183777595, "name": "bhau"},
    {"id": 6325766985, "name": "biro"},
     {"id": 1363848761, "name": "partner"}
]

# ========== DATABASE CLASS ==========
class Database:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(Database, cls).__new__(cls)
            return cls._instance
    
    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.conn = None
            self.cur = None
            self.connect()
            self.setup_tables()
            self.initialized = True
    
    def connect(self):
        try:
            self.conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=30)
            self.conn.row_factory = sqlite3.Row
            self.cur = self.conn.cursor()
            logger.info("Database connected successfully")
        except sqlite3.Error as e:
            logger.error(f"Database connection error: {e}")
            raise
    
    def setup_tables(self):
        try:
            # चेक टेबल एक्जिस्ट्स
            self.cur.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='users'
            """
            )
            table_exists = self.cur.fetchone()

            # टेबल बनाएं (अगर एक्जिस्ट नहीं है)
            self.cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            credits INTEGER DEFAULT 5
            )
            """
            )

            self.cur.execute("""
            CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            query TEXT,
            api_type TEXT,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
            
            # Add daily_credits table
            self.cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_credits (
            user_id INTEGER PRIMARY KEY,
            last_credit_date DATE
            )
            """
            )
            
            # Add blocked_users table
            self.cur.execute("""
            CREATE TABLE IF NOT EXISTS blocked_users (
            user_id INTEGER PRIMARY KEY,
            blocked_by INTEGER,
            reason TEXT,
            blocked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (blocked_by) REFERENCES users (user_id)
            )
            """
            )
            
            self.conn.commit()
            logger.info("Database tables setup successfully")
        except sqlite3.Error as e:
            logger.error(f"Database setup error: {e}")
            raise
    
    def get_cursor(self):
        try:
            if self.conn is None:
                self.connect()
            return self.conn.cursor()
        except sqlite3.Error as e:
            logger.error(f"Error getting cursor: {e}")
            self.connect()
            return self.conn.cursor()
    
    def commit(self):
        try:
            if self.conn:
                self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Commit error: {e}")
            self.connect()
            self.conn.commit()
    
    def close(self):
        try:
            if self.conn:
                self.conn.close()
                self.conn = None
                self.cur = None
        except sqlite3.Error as e:
            logger.error(f"Error closing database: {e}")

# ========== DNS CHECK FUNCTION ==========
def check_dns_resolution(hostname):
    try:
        socket.gethostbyname(hostname)
        logger.info(f"DNS resolution successful for {hostname}")
        return True
    except socket.gaierror as e:
        logger.error(f"DNS resolution failed for {hostname}: {e}")
        return False

# ========== HTTP SESSION ==========
def create_http_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        read=2,
        connect=2,
        backoff_factor=1,
        status_forcelist=(500, 502, 504)
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def make_request(url, timeout=20):
    # Extract hostname from URL for DNS check
    hostname = url.split('/')[2] if '//' in url else url.split('/')[0]
    
    # Check DNS resolution before making request
    if not check_dns_resolution(hostname):
        logger.error(f"Cannot make request to {url} - DNS resolution failed for {hostname}")
        return None
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            session = create_http_session()
            response = session.get(url, timeout=timeout)
            logger.info(f"API Request: {url}, Status: {response.status_code}")
            
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as e:
                    logger.error(f"JSON decode error for {url}: {e}")
                    logger.error(f"Response text: {response.text[:500]}")
                    return None
            else:
                logger.warning(f"Request to {url} returned status {response.status_code}")
                logger.warning(f"Response text: {response.text[:500]}")
                return None
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error (attempt {attempt + 1}/{max_retries}) for {url}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            continue
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error for {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error for {url}: {e}")
            return None
    
    logger.error(f"Max retries reached for {url}")
    return None

# ========== ERROR HANDLER ==========
def handle_errors(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except sqlite3.Error as e:
            logger.error(f"Database error in {func.__name__}: {e}")
            global db
            db.close()
            db = Database()
            return None
        except Exception as e:
            logger.error(f"Unexpected error in {func.__name__}: {e}")
            return None
    return wrapper

# ========== INITIALIZATION ==========
try:
    # Check DNS resolution for Telegram API before initializing bot
    if not check_dns_resolution("api.telegram.org"):
        logger.error("Cannot initialize bot - DNS resolution for api.telegram.org failed")
    
    db = Database()
    bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
    logger.info("Bot initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize bot: {e}")
    raise

# ========== HELPERS ==========
@handle_errors
def init_user(uid: int):
    cur = db.get_cursor()
    cur.execute("INSERT OR IGNORE INTO users (user_id, credits) VALUES (?, 5)", (uid,))
    db.commit()

@handle_errors
def get_credits(uid: int) -> int:
    cur = db.get_cursor()
    cur.execute("SELECT credits FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    return row[0] if row else 0

@handle_errors
def set_credits(uid: int, val: int):
    cur = db.get_cursor()
    cur.execute("UPDATE users SET credits=? WHERE user_id=?", (val, uid))
    db.commit()

@handle_errors
def change_credits(uid: int, delta: int):
    init_user(uid)
    cur = db.get_cursor()
    cur.execute("SELECT credits FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    cur_ = row[0] if row else 0
    new = max(0, cur_ + delta)
    cur.execute("UPDATE users SET credits=? WHERE user_id=?", (new, uid))
    db.commit()
    return new

@handle_errors
def add_history(uid: int, query: str, api_type: str):
    cur = db.get_cursor()
    cur.execute("INSERT INTO history (user_id, query, api_type) VALUES (?, ?, ?)", (uid, query, api_type))
    db.commit()

def send_long(chat_id: int, text: str, reply_to: int = None):
    try:
        MAX = 4000
        if len(text) <= MAX:
            bot.send_message(chat_id, text, reply_to_message_id=reply_to)
            return
        
        parts = [text[i:i+MAX] for i in range(0, len(text), MAX)]
        for p in parts:
            bot.send_message(chat_id, p, reply_to_message_id=reply_to)
            del p
            gc.collect()
    except Exception as e:
        logger.error(f"Error in send_long: {e}")
        bot.send_message(chat_id, "Error sending message. Please try again later.")

def clean(s):
    if s is None:
        return "N/A"
    s = str(s).replace("\u200b", "").strip()
    return re.sub(r"\s+", " ", s)

def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID

def is_special_user(uid: int) -> bool:
    for user in SPECIAL_USERS:
        if user["id"] == uid:
            return True
    return False

@handle_errors
def is_user_blocked(uid: int) -> bool:
    cur = db.get_cursor()
    cur.execute("SELECT user_id FROM blocked_users WHERE user_id=?", (uid,))
    return cur.fetchone() is not None

@handle_errors
def block_user(uid: int, blocked_by: int, reason: str = "") -> bool:
    try:
        cur = db.get_cursor()
        cur.execute("SELECT user_id FROM blocked_users WHERE user_id=?", (uid,))
        if cur.fetchone():
            return False
        
        cur.execute("INSERT INTO blocked_users (user_id, blocked_by, reason) VALUES (?, ?, ?)", (uid, blocked_by, reason))
        db.commit()
        logger.info(f"User {uid} blocked by admin {blocked_by}. Reason: {reason}")
        return True
    except sqlite3.Error as e:
        logger.error(f"Error blocking user {uid}: {e}")
        return False

@handle_errors
def unblock_user(uid: int) -> bool:
    try:
        cur = db.get_cursor()
        cur.execute("SELECT user_id FROM blocked_users WHERE user_id=?", (uid,))
        if not cur.fetchone():
            return False
        
        cur.execute("DELETE FROM blocked_users WHERE user_id=?", (uid,))
        db.commit()
        logger.info(f"User {uid} unblocked")
        return True
    except sqlite3.Error as e:
        logger.error(f"Error unblocking user {uid}: {e}")
        return False

@handle_errors
def get_blocked_users():
    try:
        cur = db.get_cursor()
        cur.execute("""
        SELECT u.user_id, b.blocked_by, b.reason, b.blocked_at 
        FROM blocked_users b
        LEFT JOIN users u ON b.user_id = u.user_id
        ORDER BY b.blocked_at DESC
        """
        )
        return cur.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Error getting blocked users: {e}")
        return []

@handle_errors
def refund_credit(uid: int):
    init_user(uid)
    credits = get_credits(uid)
    set_credits(uid, credits + 1)

# ========== DAILY CREDITS FUNCTIONS ==========
@handle_errors
def check_and_give_daily_credits(uid: int) -> bool:
    today = date.today().isoformat()
    
    cur = db.get_cursor()
    cur.execute("SELECT last_credit_date FROM daily_credits WHERE user_id=?", (uid,))
    row = cur.fetchone()
    
    if not row or row[0] != today:
        change_credits(uid, 10)
        
        cur.execute("INSERT OR REPLACE INTO daily_credits (user_id, last_credit_date) VALUES (?, ?)", (uid, today))
        db.commit()
        
        return True
    return False

@handle_errors
def get_last_credit_date(uid: int) -> str:
    cur = db.get_cursor()
    cur.execute("SELECT last_credit_date FROM daily_credits WHERE user_id=?", (uid,))
    row = cur.fetchone()
    return row[0] if row else "Never"

# ========== ENSURE AND CHARGE FUNCTION ==========
@handle_errors
def ensure_and_charge(uid: int, chat_id: int) -> bool:
    if is_user_blocked(uid):
        bot.send_message(chat_id, "⚠️ <b>Your account has been blocked.</b>\n\nPlease contact admin for more information.")
        return False
        
    init_user(uid)
    
    if is_special_user(uid):
        return True
        
    credits = get_credits(uid)
    if credits <= 0:
        kb = types.InlineKeyboardMarkup(row_width=1)
        btn1 = types.InlineKeyboardButton("💳 Buy Credits", callback_data="buy_credits")
        kb.add(btn1)
        
        message_text = "❌ <b>No credits left.</b>\n\nYou can purchase more credits using the button below."
        
        bot.send_message(chat_id, message_text, reply_markup=kb)
        return False
    set_credits(uid, credits - 1)
    return True

# ========== START ==========
@bot.message_handler(commands=["start"])
@handle_errors
def cmd_start(m):
    try:
        uid = m.from_user.id
        
        if is_user_blocked(uid):
            bot.send_message(m.chat.id, "⚠️ <b>Your account has been blocked.</b>\n\nPlease contact admin for more information.")
            return

        init_user(uid)
        
        # विशेष यूजर्स के लिए क्रेडिट 999 सेट करें
        for user in SPECIAL_USERS:
            if user["id"] == uid:
                set_credits(uid, 999)
                break
        
        # Check and give daily credits (skip for special users)
        if not is_special_user(uid):
            check_and_give_daily_credits(uid)
        
        credits = get_credits(uid)

        # Main Menu Keyboard
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.row("👤 Telegram ID Info", "🇮🇳 India Number Info")
        kb.row("📱 Pakistan Number Info", "📮 Pincode Info")
        kb.row("🚘 Vehicle Info", "🆔 Aadhaar Info")
        kb.row("🧪 ICMR Number Info", "🏦 IFSC Code Info")
        kb.row("💸 UPI ID Info", "📋 Ration Card Info")
        kb.row("🔍 Truecaller Info")  # NEW: Truecaller button
        kb.row("💳 My Credits", "💳 Buy Credits", "🎁 Get Daily Credits", "📜 My History", "📞 Contact Admin", "🆔 My ID")
        if is_admin(uid):
            kb.row("⚙️ Admin Panel")

        start_text = f"""
━━━━━━━━━━━━━━━━━━
🤖 <b>InfoBot</b>
<i>Your Digital Info Assistant 🚀</i>
━━━━━━━━━━━━━━━━━━

🔍 <b>Available Services:</b>
👤 Telegram ID Info
🇮🇳 India Number Info
🇵🇰 Pakistan Number Info
📮 Pincode Details
🚘 Vehicle Info
🆔 Aadhaar Info
🧪 ICMR Number Info
🏦 IFSC Code Info
💸 UPI ID Info
📋 Ration Card Info
🔍 Truecaller Info  # NEW: Added to services list

💳 <b>Your Credits:</b> <code>{credits}</code>
🎁 <b>Daily Credits:</b> Get 10 free credits every day!
💰 <b>Buy More:</b> Use "Buy Credits" button for special offers!

⚠️ Each search costs <b>1 credit</b>.
Credits are refunded if no results found.
For recharge, use "Buy Credits" button or contact admin.

✅ <b>Choose an option below to begin!</b>

━━━━━━━━━━━━━━━━━━
© 2025 <b>InfoBot</b> | All Rights Reserved
📞 <a href="tg://user?id={ADMIN_ID}">Contact Admin</a>
━━━━━━━━━━━━━━━━━━
"""
        bot.send_message(m.chat.id, start_text, reply_markup=kb, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        bot.send_message(m.chat.id, "An error occurred. Please try again later.")

# ========== TRUECALLER INFO ==========
@bot.message_handler(func=lambda c: c.text == "🔍 Truecaller Info")
@handle_errors
def ask_truecaller_number(m):
    bot.send_message(m.chat.id, "📱 Send phone number with country code (e.g., 917078551517):")
    bot.register_next_step_handler(m, handle_truecaller_number)

@handle_errors
def handle_truecaller_number(m):
    try:
        num = m.text.strip()
        # Validate phone number format (country code + number, 10-15 digits)
        if not re.fullmatch(r"\d{10,15}", num):
            return bot.send_message(m.chat.id, "⚠️ Invalid phone number. Please enter a valid number with country code (e.g., 917078551517).")
        
        # Check credits
        if not ensure_and_charge(m.from_user.id, m.chat.id):
            return
        
        progress_msg = bot.send_message(m.chat.id, "🔍 Searching Truecaller information...")
        
        # Make API request
        data = make_request(f"https://chxphone.vercel.app/lookup?number={num}")
        
        try:
            bot.delete_message(m.chat.id, progress_msg.message_id)
        except:
            pass
        
        if not data:
            refund_credit(m.from_user.id)
            return bot.send_message(m.chat.id, "❌ No data found for this number.")
        
        # Extract information from API response
        number = clean(data.get('number'))
        name_info_raw = clean(data.get('name_info_raw'))
        photo_url = clean(data.get('photo_url'))
        
        # Extract flipcartstore details
        flipcartstore = data.get('flipcartstore', {})
        circle = clean(flipcartstore.get('circle'))
        country = clean(flipcartstore.get('country'))
        operator = clean(flipcartstore.get('operator'))
        phone_type = clean(flipcartstore.get('type'))
        valid = flipcartstore.get('valid')
        
        # Format the output message
        out = f"""
🔍 <b>Truecaller Information</b>
━━━━━━━━━━━━━━━━━━
📱 <b>Number:</b> {number}
👤 <b>Name:</b> {name_info_raw if name_info_raw else 'Not available'}
🖼️ <b>Photo:</b> {photo_url if photo_url else 'Not available'}

🌐 <b>Country:</b> {country}
📡 <b>Circle:</b> {circle}
📶 <b>Operator:</b> {operator if operator else 'Not available'}
📱 <b>Type:</b> {phone_type}
✅ <b>Valid:</b> {'Yes' if valid else 'No'}
━━━━━━━━━━━━━━━━━━
"""
        
        bot.send_message(m.chat.id, out, parse_mode="HTML")
        add_history(m.from_user.id, num, "TRUECALLER")
        
    except Exception as e:
        refund_credit(m.from_user.id)
        logger.error(f"Error in handle_truecaller_number: {e}")
        bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")

# ========== ADMIN PANEL ==========
@bot.message_handler(func=lambda c: c.text == "⚙️ Admin Panel")
@handle_errors
def admin_panel(m):
    if not is_admin(m.from_user.id):
        return
    
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("💳 Add Credits", "💸 Remove Credits")
    kb.row("👥 All Users", "📋 User History")
    kb.row("📢 Broadcast", "🌟 Special Users")
    kb.row("🚫 Block User", "✅ Unblock User", "📋 Blocked Users")
    kb.row("🔙 Back to Main Menu")
    
    bot.send_message(m.chat.id, "⚙️ <b>Admin Panel</b>\n\nChoose an option:", reply_markup=kb)

# Add Credits Handler
@bot.message_handler(func=lambda c: c.text == "💳 Add Credits")
@handle_errors
def add_credits_btn(m):
    if not is_admin(m.from_user.id):
        return
    
    msg = bot.send_message(m.chat.id, "💳 Send user ID and credits to add (format: user_id credits):")
    bot.register_next_step_handler(m, process_add_credits)

@handle_errors
def process_add_credits(m):
    try:
        if not is_admin(m.from_user.id):
            return
        
        parts = m.text.strip().split()
        if len(parts) != 2:
            return bot.send_message(m.chat.id, "❌ Invalid format. Please use: user_id credits")
        
        try:
            uid = int(parts[0])
            credits = int(parts[1])
        except ValueError:
            return bot.send_message(m.chat.id, "❌ Invalid user ID or credits value.")
        
        if credits <= 0:
            return bot.send_message(m.chat.id, "❌ Credits must be a positive number.")
        
        init_user(uid)
        current_credits = get_credits(uid)
        new_credits = change_credits(uid, credits)
        
        bot.send_message(m.chat.id, f"✅ Successfully added {credits} credits to user {uid}.\nPrevious balance: {current_credits}\nNew balance: {new_credits}")
        
        # Notify user
        try:
            bot.send_message(uid, f"🎉 {credits} credits have been added to your account!\nYour current balance: {new_credits}")
        except Exception as e:
            logger.error(f"Could not notify user {uid}: {e}")
    except Exception as e:
        logger.error(f"Error in process_add_credits: {e}")
        bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")

# Remove Credits Handler
@bot.message_handler(func=lambda c: c.text == "💸 Remove Credits")
@handle_errors
def remove_credits_btn(m):
    if not is_admin(m.from_user.id):
        return
    
    msg = bot.send_message(m.chat.id, "💸 Send user ID and credits to remove (format: user_id credits):")
    bot.register_next_step_handler(m, process_remove_credits)

@handle_errors
def process_remove_credits(m):
    try:
        if not is_admin(m.from_user.id):
            return
        
        parts = m.text.strip().split()
        if len(parts) != 2:
            return bot.send_message(m.chat.id, "❌ Invalid format. Please use: user_id credits")
        
        try:
            uid = int(parts[0])
            credits = int(parts[1])
        except ValueError:
            return bot.send_message(m.chat.id, "❌ Invalid user ID or credits value.")
        
        if credits <= 0:
            return bot.send_message(m.chat.id, "❌ Credits must be a positive number.")
        
        init_user(uid)
        current_credits = get_credits(uid)
        new_credits = change_credits(uid, -credits)
        
        bot.send_message(m.chat.id, f"✅ Successfully removed {credits} credits from user {uid}.\nPrevious balance: {current_credits}\nNew balance: {new_credits}")
        
        # Notify user
        try:
            bot.send_message(uid, f"❌ {credits} credits have been removed from your account.\nYour current balance: {new_credits}")
        except Exception as e:
            logger.error(f"Could not notify user {uid}: {e}")
    except Exception as e:
        logger.error(f"Error in process_remove_credits: {e}")
        bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")

# All Users Handler
@bot.message_handler(func=lambda c: c.text == "👥 All Users")
@handle_errors
def all_users_btn(m):
    if not is_admin(m.from_user.id):
        return
    
    cur = db.get_cursor()
    cur.execute("SELECT user_id FROM users ORDER BY user_id")
    users = [row[0] for row in cur.fetchall()]
    
    if not users:
        return bot.send_message(m.chat.id, "❌ No users found.")
    
    total_users = len(users)
    special_count = len(SPECIAL_USERS)
    normal_count = total_users - special_count
    
    out = f"""
👥 <b>All Users</b>
━━━━━━━━━━━━━━━━━━
📊 <b>Total Users:</b> {total_users}
🌟 <b>Special Users:</b> {special_count}
👤 <b>Normal Users:</b> {normal_count}

📋 <b>User List:</b>
"""
    
    # Show first 50 users to avoid message too long
    for i, uid in enumerate(users[:50], 1):
        special = " 🌟" if is_special_user(uid) else ""
        credits = get_credits(uid)
        out += f"\n{i}. <code>{uid}</code> - {credits} credits{special}"
    
    if len(users) > 50:
        out += f"\n\n... and {len(users) - 50} more users."
    
    send_long(m.chat.id, out)

# User History Handler
@bot.message_handler(func=lambda c: c.text == "📋 User History")
@handle_errors
def user_history_btn(m):
    if not is_admin(m.from_user.id):
        return
    
    msg = bot.send_message(m.chat.id, "📋 Send user ID to view history:")
    bot.register_next_step_handler(m, process_user_history)

@handle_errors
def process_user_history(m):
    try:
        if not is_admin(m.from_user.id):
            return
        
        try:
            uid = int(m.text.strip())
        except ValueError:
            return bot.send_message(m.chat.id, "❌ Invalid user ID.")
        
        cur = db.get_cursor()
        cur.execute("SELECT query, api_type, ts FROM history WHERE user_id=? ORDER BY id DESC LIMIT 50", (uid,))
        rows = cur.fetchall()
        
        if not rows:
            return bot.send_message(m.chat.id, f"❌ No history found for user {uid}.")
        
        out = f"""
📋 <b>User History for {uid}</b>
━━━━━━━━━━━━━━━━━━
"""
        
        for q, t, ts in rows:
            out += f"\n[{ts}] ({t}) {q}"
        
        send_long(m.chat.id, out)
    except Exception as e:
        logger.error(f"Error in process_user_history: {e}")
        bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")

# Broadcast Handler
@bot.message_handler(func=lambda c: c.text == "📢 Broadcast")
@handle_errors
def broadcast_btn(m):
    if not is_admin(m.from_user.id):
        return
    
    msg = bot.send_message(m.chat.id, "📢 Send the message to broadcast to all users:")
    bot.register_next_step_handler(m, process_broadcast)

@handle_errors
def process_broadcast(m):
    try:
        if not is_admin(m.from_user.id):
            return
        
        broadcast_message = m.text.strip()
        if not broadcast_message:
            return bot.send_message(m.chat.id, "❌ Message cannot be empty.")
        
        cur = db.get_cursor()
        cur.execute("SELECT user_id FROM users")
        users = [row[0] for row in cur.fetchall()]
        
        if not users:
            return bot.send_message(m.chat.id, "❌ No users found.")
        
        success_count = 0
        failed_count = 0
        
        progress_msg = bot.send_message(m.chat.id, f"📢 Broadcasting message to {len(users)} users...")
        
        for uid in users:
            try:
                # Skip blocked users
                if is_user_blocked(uid):
                    failed_count += 1
                    continue
                
                bot.send_message(uid, f"📢 <b>Broadcast Message</b>\n\n{broadcast_message}")
                success_count += 1
                time.sleep(0.1)  # Small delay to avoid flood limits
            except Exception as e:
                logger.error(f"Failed to send broadcast to {uid}: {e}")
                failed_count += 1
        
        try:
            bot.delete_message(m.chat.id, progress_msg.message_id)
        except:
            pass
        
        result_msg = f"""
✅ <b>Broadcast Completed</b>
━━━━━━━━━━━━━━━━━━
📊 <b>Total Users:</b> {len(users)}
✅ <b>Successful:</b> {success_count}
❌ <b>Failed:</b> {failed_count}
"""
        bot.send_message(m.chat.id, result_msg)
    except Exception as e:
        logger.error(f"Error in process_broadcast: {e}")
        bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")

# Special Users Handler
@bot.message_handler(func=lambda c: c.text == "🌟 Special Users")
@handle_errors
def special_users_btn(m):
    if not is_admin(m.from_user.id):
        return
    
    kb = types.InlineKeyboardMarkup(row_width=2)
    btn1 = types.InlineKeyboardButton("➕ Add Special User", callback_data="add_special")
    btn2 = types.InlineKeyboardButton("➖ Remove Special User", callback_data="remove_special")
    kb.add(btn1, btn2)
    
    # Show current special users
    out = "🌟 <b>Special Users</b>\n━━━━━━━━━━━━━━━━━━\n"
    for user in SPECIAL_USERS:
        out += f"🆔 <code>{user['id']}</code> - {user['name']}\n"
    
    bot.send_message(m.chat.id, out, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data in ["add_special", "remove_special"])
@handle_errors
def handle_special_user_callback(call):
    if not is_admin(call.from_user.id):
        return
    
    if call.data == "add_special":
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "➕ Send user ID and name to add as special user (format: user_id name):")
        bot.register_next_step_handler(msg, process_add_special_user)
    elif call.data == "remove_special":
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "➖ Send user ID to remove from special users:")
        bot.register_next_step_handler(msg, process_remove_special_user)

@handle_errors
def process_add_special_user(m):
    try:
        if not is_admin(m.from_user.id):
            return
        
        parts = m.text.strip().split(maxsplit=1)
        if len(parts) < 2:
            return bot.send_message(m.chat.id, "❌ Invalid format. Please use: user_id name")
        
        try:
            uid = int(parts[0])
            name = parts[1]
        except ValueError:
            return bot.send_message(m.chat.id, "❌ Invalid user ID.")
        
        # Check if already special
        if is_special_user(uid):
            return bot.send_message(m.chat.id, "❌ User is already a special user.")
        
        # Add to special users list
        SPECIAL_USERS.append({"id": uid, "name": name})
        
        # Set credits to 999
        init_user(uid)
        set_credits(uid, 999)
        
        bot.send_message(m.chat.id, f"✅ Successfully added {name} (ID: {uid}) as a special user.")
        
        # Notify user
        try:
            bot.send_message(uid, f"🌟 You have been added as a special user with unlimited credits!")
        except Exception as e:
            logger.error(f"Could not notify user {uid}: {e}")
    except Exception as e:
        logger.error(f"Error in process_add_special_user: {e}")
        bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")

@handle_errors
def process_remove_special_user(m):
    try:
        if not is_admin(m.from_user.id):
            return
        
        try:
            uid = int(m.text.strip())
        except ValueError:
            return bot.send_message(m.chat.id, "❌ Invalid user ID.")
        
        # Find and remove from special users list
        for i, user in enumerate(SPECIAL_USERS):
            if user["id"] == uid:
                SPECIAL_USERS.pop(i)
                
                # Reset credits to normal (5)
                init_user(uid)
                set_credits(uid, 5)
                
                bot.send_message(m.chat.id, f"✅ Successfully removed user {uid} from special users.")
                
                # Notify user
                try:
                    bot.send_message(uid, "❌ You have been removed from special users. Your credits have been reset to normal.")
                except Exception as e:
                    logger.error(f"Could not notify user {uid}: {e}")
                return
        
        bot.send_message(m.chat.id, "❌ User not found in special users list.")
    except Exception as e:
        logger.error(f"Error in process_remove_special_user: {e}")
        bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")

# Block/Unblock User Handlers
@bot.message_handler(func=lambda c: c.text=="🚫 Block User")
@handle_errors
def block_user_btn(m):
    if not is_admin(m.from_user.id): 
        return
    bot.send_message(m.chat.id,"🚫 Send user ID to block:")
    bot.register_next_step_handler(m,process_block_user)

@handle_errors
def process_block_user(m):
    try:
        uid=int(m.text.strip())
        
        cur = db.get_cursor()
        cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
        if not cur.fetchone():
            return bot.send_message(m.chat.id, "❌ User not found in database.")
        
        if is_user_blocked(uid):
            return bot.send_message(m.chat.id, "❌ User is already blocked.")
        
        msg = bot.send_message(m.chat.id, "🚫 Please provide a reason for blocking (optional):")
        bot.register_next_step_handler(msg, lambda msg: process_block_reason(msg, uid))
    except Exception as e:
        logger.error(f"Error in process_block_user: {e}")
        bot.send_message(m.chat.id, "❌ Invalid user ID.")

@handle_errors
def process_block_reason(m, uid):
    reason = m.text.strip()
    admin_id = m.from_user.id
    
    if block_user(uid, admin_id, reason):
        bot.send_message(m.chat.id, f"✅ User {uid} has been blocked successfully.\nReason: {reason}")
        
        try:
            bot.send_message(uid, f"⚠️ Your account has been blocked by admin.\nReason: {reason}\n\nContact admin for more information.")
        except Exception as e:
            logger.error(f"Could not notify blocked user {uid}: {e}")
    else:
        bot.send_message(m.chat.id, "❌ Failed to block user.")

@bot.message_handler(func=lambda c: c.text=="✅ Unblock User")
@handle_errors
def unblock_user_btn(m):
    if not is_admin(m.from_user.id): 
        return
    bot.send_message(m.chat.id,"✅ Send user ID to unblock:")
    bot.register_next_step_handler(m,process_unblock_user)

@handle_errors
def process_unblock_user(m):
    try:
        uid=int(m.text.strip())
        
        if not is_user_blocked(uid):
            return bot.send_message(m.chat.id, "❌ User is not blocked.")
        
        if unblock_user(uid):
            bot.send_message(m.chat.id, f"✅ User {uid} has been unblocked successfully.")
            
            try:
                bot.send_message(uid, "✅ Your account has been unblocked. You can now use the bot again.")
            except Exception as e:
                logger.error(f"Could not notify unblocked user {uid}: {e}")
        else:
            bot.send_message(m.chat.id, "❌ Failed to unblock user.")
    except Exception as e:
        logger.error(f"Error in process_unblock_user: {e}")
        bot.send_message(m.chat.id, "❌ Invalid user ID.")

@bot.message_handler(func=lambda c: c.text=="📋 Blocked Users")
@handle_errors
def blocked_users_btn(m):
    if not is_admin(m.from_user.id): 
        return
    
    blocked_users = get_blocked_users()
    if not blocked_users:
        return bot.send_message(m.chat.id, "✅ No blocked users found.")
    
    out = "📋 <b>Blocked Users List:</b>\n\n"
    for user in blocked_users:
        user_id = user[0]
        blocked_by = user[1]
        reason = user[2] if user[2] else "No reason provided"
        blocked_at = user[3]
        out += f"🆔 <b>User ID:</b> {user_id}\n"
        out += f"👤 <b>Blocked By:</b> {blocked_by}\n"
        out += f"📝 <b>Reason:</b> {reason}\n"
        out += f"📅 <b>Blocked At:</b> {blocked_at}\n"
        out += "━━━━━━━━━━━━━━━━━━\n"
    
    send_long(m.chat.id, out)

# Back to main menu handler
@bot.message_handler(func=lambda c: c.text == "🔙 Back to Main Menu")
@handle_errors
def back_to_main(m):
    cmd_start(m)

# ========== BUY CREDITS FEATURE ==========
@bot.message_handler(func=lambda c: c.text == "💳 Buy Credits")
@handle_errors
def buy_credits_btn(m):
    uid = m.from_user.id
    
    kb = types.InlineKeyboardMarkup(row_width=1)
    btn1 = types.InlineKeyboardButton("💎 100 Credits - ₹200", callback_data="buy_100")
    btn2 = types.InlineKeyboardButton("💎 200 Credits - ₹300", callback_data="buy_200")
    btn3 = types.InlineKeyboardButton("💎 500 Credits - ₹500", callback_data="buy_500")
    btn4 = types.InlineKeyboardButton("🔄 Custom Amount", callback_data="buy_custom")
    
    kb.add(btn1, btn2, btn3, btn4)
    
    buy_text = f"""
💳 <b>Credit Packs & Pricing</b>
━━━━━━━━━━━━━━━━━━━━━━━

💎 <b>1 – 100 Credits</b> 
👉 ₹2 per Credit 
✔️ Example: 50 Credits = ₹100 

💎 <b>101 – 499 Credits</b> 
👉 ₹1.5 per Credit 
✔️ Example: 200 Credits = ₹300 

💎 <b>500+ Credits</b> 
👉 ₹1 per Credit 
✔️ Example: 500 Credits = ₹500 

━━━━━━━━━━━━━━━━━━━━━━━
📥 <b>Payment Method:</b> 
UPI → mohd.kaifu@sbi 

⚠️ After payment, send screenshot to admin for quick approval.

💳 <b>Your Current Credits:</b> {get_credits(uid)}
"""
    
    bot.send_message(m.chat.id, buy_text, reply_markup=kb, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_"))
@handle_errors
def handle_buy_callback(call):
    uid = call.from_user.id
    
    if call.data == "buy_100":
        amount = "100 Credits for ₹200"
    elif call.data == "buy_200":
        amount = "200 Credits for ₹300"
    elif call.data == "buy_500":
        amount = "500 Credits for ₹500"
    elif call.data == "buy_custom":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "Please contact admin directly for custom credit amounts.")
        return
    
    payment_text = f"""
💳 <b>Payment Instructions</b>
━━━━━━━━━━━━━━━━━━━━━━━

You've selected: {amount}

📥 <b>Payment Method:</b> 
UPI → mohd.kaifu@sbi 

⚠️ <b>Steps:</b>
1. Send payment of the selected amount
2. Take a screenshot of the payment confirmation
3. Send the screenshot to admin with your user ID: <code>{uid}</code>
4. Admin will add credits to your account after verification

Thank you for your purchase!
"""
    
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, payment_text, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "buy_credits")
@handle_errors
def handle_buy_credits_callback(call):
    uid = call.from_user.id
    
    kb = types.InlineKeyboardMarkup(row_width=1)
    btn1 = types.InlineKeyboardButton("💎 100 Credits - ₹200", callback_data="buy_100")
    btn2 = types.InlineKeyboardButton("💎 200 Credits - ₹300", callback_data="buy_200")
    btn3 = types.InlineKeyboardButton("💎 500 Credits - ₹500", callback_data="buy_500")
    btn4 = types.InlineKeyboardButton("🔄 Custom Amount", callback_data="buy_custom")
    
    kb.add(btn1, btn2, btn3, btn4)
    
    buy_text = f"""
💳 <b>Credit Packs & Pricing</b>
━━━━━━━━━━━━━━━━━━━━━━━

💎 <b>1 – 100 Credits</b> 
👉 ₹2 per Credit 
✔️ Example: 50 Credits = ₹100 

💎 <b>101 – 499 Credits</b> 
👉 ₹1.5 per Credit 
✔️ Example: 200 Credits = ₹300 

💎 <b>500+ Credits</b> 
👉 ₹1 per Credit 
✔️ Example: 500 Credits = ₹500 

━━━━━━━━━━━━━━━━━━━━━━━
📥 <b>Payment Method:</b> 
UPI → mohd.kaifu@sbi 

⚠️ After payment, send screenshot to admin for quick approval.

💳 <b>Your Current Credits:</b> {get_credits(uid)}
"""
    
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, buy_text, reply_markup=kb, parse_mode="HTML")

# ========== MY HISTORY FEATURE ==========
@bot.message_handler(func=lambda c: c.text == "📜 My History")
@handle_errors
def my_history_btn(m):
    uid = m.from_user.id
    cur = db.get_cursor()
    cur.execute("SELECT query, api_type, ts FROM history WHERE user_id=? ORDER BY id DESC", (uid,))
    rows = cur.fetchall()
    
    if not rows:
        return bot.send_message(m.chat.id, "❌ No search history found.")
    
    out = "📜 <b>Your Complete Search History:</b>\n\n"
    for q, t, ts in rows:
        out += f"[{ts}] ({t}) {q}\n"
    
    send_long(m.chat.id, out)

# ========== BASIC BUTTON HANDLERS ==========
@bot.message_handler(func=lambda c: c.text == "🆔 My ID")
@handle_errors
def btn_myid(m):
    bot.send_message(m.chat.id, f"🆔 Your Telegram ID: <code>{m.from_user.id}</code>")

@bot.message_handler(func=lambda c: c.text == "💳 My Credits")
@handle_errors
def my_credits_btn(m):
    uid = m.from_user.id
    credits = get_credits(uid)
    
    if is_special_user(uid):
        bot.send_message(m.chat.id, f"💳 Your Credits: <b>{credits}</b>\n\n🌟 <i>You are a special user with unlimited searches!</i>")
    else:
        bot.send_message(m.chat.id, f"💳 Your Credits: <b>{credits}</b>")

@bot.message_handler(func=lambda c: c.text == "📞 Contact Admin")
@handle_errors
def contact_admin_btn(m):
    kb = types.InlineKeyboardMarkup()
    btn = types.InlineKeyboardButton("📞 Contact Admin", url=f"tg://user?id={ADMIN_ID}")
    kb.add(btn)
    bot.send_message(m.chat.id, "Click below to contact admin 👇", reply_markup=kb)

@bot.message_handler(func=lambda c: c.text == "🎁 Get Daily Credits")
@handle_errors
def daily_credits_btn(m):
    uid = m.from_user.id
    init_user(uid)
    
    if is_special_user(uid):
        return bot.send_message(m.chat.id, "🌟 You are a special user with unlimited credits!")
    
    if check_and_give_daily_credits(uid):
        credits = get_credits(uid)
        bot.send_message(m.chat.id, f"✅ You have received 10 daily credits!\n💳 Your current balance: {credits}")
    else:
        last_date = get_last_credit_date(uid)
        bot.send_message(m.chat.id, f"❌ You have already received your daily credits today.\n📅 Last credited: {last_date}\n\nPlease try again tomorrow.")

# ========== TELEGRAM ID INFO ==========
@bot.message_handler(func=lambda c: c.text == "👤 Telegram ID Info")
@handle_errors
def ask_tgid(m):
    bot.send_message(m.chat.id, "📩 Send Telegram User ID (numeric):")
    bot.register_next_step_handler(m, handle_tgid)

@handle_errors
def handle_tgid(m):
    try:
        q = m.text.strip()
        if not re.fullmatch(r"\d+", q):
            return bot.send_message(m.chat.id, "⚠️ Invalid Telegram ID. Please enter a numeric user ID.")
        
        if not ensure_and_charge(m.from_user.id, m.chat.id):
            return
        
        progress_msg = bot.send_message(m.chat.id, "🔍 Fetching Telegram user information...")
        
        data = make_request(f"https://tg-info-neon.vercel.app/user-details?user={q}")
        
        try:
            bot.delete_message(m.chat.id, progress_msg.message_id)
        except:
            pass
        
        if not data or not data.get("success"):
            refund_credit(m.from_user.id)
            return bot.send_message(m.chat.id, "❌ No data found for this Telegram ID.")
        
        d = data.get("data", {})
        
        first_name = clean(d.get('first_name'))
        last_name = clean(d.get('last_name'))
        full_name = f"{first_name} {last_name}".strip() if last_name else first_name
        
        first_msg_date = clean(d.get('first_msg_date'))
        last_msg_date = clean(d.get('last_msg_date'))
        
        activity_emoji = "✅" if d.get('is_active') else "❌"
        bot_emoji = "🤖" if d.get('is_bot') else "👤"
        
        out = f"""
{bot_emoji} <b>Telegram User Information</b>
━━━━━━━━━━━━━━━━━━
🆔 <b>User ID:</b> <code>{clean(d.get('id'))}</code>
👤 <b>Full Name:</b> {full_name}
{bot_emoji} <b>Is Bot:</b> {clean(d.get('is_bot'))}
{activity_emoji} <b>Active Status:</b> {clean(d.get('is_active'))}

📅 <b>First Message:</b> {first_msg_date}
📅 <b>Last Message:</b> {last_msg_date}

💬 <b>Total Messages:</b> {clean(d.get('total_msg_count'))}
👥 <b>Total Groups:</b> {clean(d.get('total_groups'))}
👨‍💼 <b>Admin in Groups:</b> {clean(d.get('adm_in_groups'))}
💬 <b>Messages in Groups:</b> {clean(d.get('msg_in_groups_count'))}

🔄 <b>Name Changes:</b> {clean(d.get('names_count'))}
@️ <b>Username Changes:</b> {clean(d.get('usernames_count'))}
━━━━━━━━━━━━━━━━━━
"""
        bot.send_message(m.chat.id, out, parse_mode="HTML")
        add_history(m.from_user.id, q, "TELEGRAM_ID")
    except Exception as e:
        refund_credit(m.from_user.id)
        logger.error(f"Error in handle_tgid: {e}")
        bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")

# ======= INDIA NUMBER HANDLER =======
@bot.message_handler(func=lambda message: message.text == "🇮🇳 India Number Info")
@handle_errors
def ask_india_number(message):
    bot.send_message(message.chat.id, "📱 Send 10-digit Indian mobile number:")
    bot.register_next_step_handler(message, handle_india_number_response)

@handle_errors
def handle_india_number_response(message):
    num = message.text.strip()
    
    if not re.fullmatch(r"\d{10}", num):
        return bot.send_message(message.chat.id, "⚠️ Invalid 10-digit number.")
    
    if not ensure_and_charge(message.from_user.id, message.chat.id):
        return
    
    progress_msg = bot.send_message(message.chat.id, "🔍 Searching for information...")
    
    try:
        r = requests.get(
            f"http://osintx.info/API/krobetahack.php?key=P6NW6D1&type=mobile&term={num}",
            timeout=30
        )
        
        try:
            bot.delete_message(message.chat.id, progress_msg.message_id)
        except:
            pass

        if r.status_code != 200:
            refund_credit(message.from_user.id)
            return bot.send_message(message.chat.id, "❌ API request failed. Try again later.")
        
        try:
            response_json = r.json()
        except ValueError:
            refund_credit(message.from_user.id)
            return bot.send_message(message.chat.id, "❌ Invalid API response format.")
        
        # Handle different response formats
        if isinstance(response_json, dict):
            data_list = response_json.get("data", [])
        elif isinstance(response_json, list):
            data_list = response_json
        else:
            logger.error(f"Unexpected response format: {type(response_json)}")
            refund_credit(message.from_user.id)
            return bot.send_message(message.chat.id, "📭 No Information Found!")
        
        # Check if data_list is empty
        if not data_list:
            refund_credit(message.from_user.id)
            return bot.send_message(message.chat.id, "📭 No Information Found!")

        # Header message
        header = f"""
📱 <b>Indian Number Lookup Results</b>
🔍 <b>Queried Number:</b> {num}
📊 <b>Total Records Found:</b> {len(data_list)}
━━━━━━━━━━━━━━━━━━
"""
        bot.send_message(message.chat.id, header, parse_mode="HTML")
        
        # Send each record
        for i, rec in enumerate(data_list, 1):
            try:
                # Process record based on the actual API response structure
                if isinstance(rec, dict):
                    # Get all fields from the record
                    rec_id = clean(str(rec.get("id", "")))
                    mobile = clean(rec.get("mobile", ""))
                    name = clean(rec.get("name", ""))
                    father_name = clean(rec.get("father_name", ""))
                    address_raw = rec.get("address", "")
                    alt_mobile = clean(rec.get("alt_mobile", ""))
                    circle = clean(rec.get("circle", ""))
                    id_number = clean(rec.get("id_number", ""))
                    email = clean(rec.get("email", "N/A"))
                    
                    # Clean address - split by "!" and remove duplicates
                    if address_raw:
                        address_parts = [part.strip() for part in address_raw.split("!") if part.strip()]
                        address = ", ".join(dict.fromkeys(address_parts))
                    else:
                        address = "N/A"
                else:
                    logger.error(f"Record #{i} is not a dictionary: {type(rec)}")
                    continue

                out = f"""
📋 <b>Record #{i}</b>
━━━━━━━━━━━━━━━━━━
👤 <b>Name:</b> {name}
👨‍👩‍👦 <b>Father/Guardian:</b> {father_name}
📱 <b>Primary Mobile:</b> {mobile}
📞 <b>Alternate Mobile:</b> {alt_mobile}
🌐 <b>Network Circle:</b> {circle}
🏠 <b>Address:</b> {address}
📧 <b>Email:</b> {email}
🆔 <b>ID:</b> {rec_id}
🇮🇳 <b>Aadhar Card:</b> {id_number if id_number else "N/A"}
"""
                bot.send_message(message.chat.id, out, parse_mode="HTML")
                time.sleep(0.1)  # avoid flood
            except Exception as e:
                logger.error(f"Error processing India record #{i}: {e}")
                continue

        # Footer
        footer = f"""
━━━━━━━━━━━━━━━━━━
✅ <b>Search completed successfully!</b>
💳 <b>Credits Used:</b> 1
📊 <b>Total Records:</b> {len(data_list)}
"""
        bot.send_message(message.chat.id, footer, parse_mode="HTML")
        add_history(message.from_user.id, num, "IND_NUMBER")
    
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error: {e}")
        try:
            bot.delete_message(message.chat.id, progress_msg.message_id)
        except:
            pass
        refund_credit(message.from_user.id)
        bot.send_message(message.chat.id, "❌ Network error. Please try again later.")
        
# ========== PAKISTAN NUMBER INFO ==========
@bot.message_handler(func=lambda c: c.text == "📱 Pakistan Number Info")
@handle_errors
def ask_pak_number(m):
    bot.send_message(m.chat.id, "📲 Send Pakistan number with country code (923XXXXXXXXX):")
    bot.register_next_step_handler(m, handle_pak_number)

@handle_errors
def handle_pak_number(m):
    try:
        num = m.text.strip()
        if not re.fullmatch(r"923\d{9}", num):
            return bot.send_message(m.chat.id, "⚠️ Invalid Pakistan number. Please enter in format: 923XXXXXXXXX")
        
        if not ensure_and_charge(m.from_user.id, m.chat.id):
            return
        
        progress_msg = bot.send_message(m.chat.id, "🔍 Searching for Pakistan number information...")
        
        data = make_request(f"https://pak-num-api.vercel.app/search?number={num}")
        
        try:
            bot.delete_message(m.chat.id, progress_msg.message_id)
        except:
            pass
        
        if not data or "results" not in data or not data["results"]:
            refund_credit(m.from_user.id)
            return bot.send_message(m.chat.id, "❌ No data found for this Pakistan number.")
        
        results = data.get("results", [])
        results_count = len(results)
        
        header = f"""
📱 <b>Pakistan Number Lookup Results</b>
🔍 <b>Queried Number:</b> {num}
📊 <b>Total Records Found:</b> {results_count}
━━━━━━━━━━━━━━━━━━
"""
        bot.send_message(m.chat.id, header, parse_mode="HTML")
        
        for i, rec in enumerate(results, 1):
            name = clean(rec.get('Name'))
            mobile = clean(rec.get('Mobile'))
            cnic = clean(rec.get('CNIC'))
            address = clean(rec.get('Address'))
            
            out = f"""
📋 <b>Record #{i}</b>
👤 <b>Name:</b> {name}
📱 <b>Mobile:</b> {mobile}
🇵🇰 <b>CNIC:</b> {cnic}
🏠 <b>Address:</b> {address}
━━━━━━━━━━━━━━━━━━
"""
            bot.send_message(m.chat.id, out, parse_mode="HTML")
        
        footer = f"""
✅ <b>Search completed successfully!</b>
💳 <b>Credits Used:</b> 1
📊 <b>Total Records:</b> {results_count}
"""
        bot.send_message(m.chat.id, footer, parse_mode="HTML")
        
        add_history(m.from_user.id, num, "PAK_NUMBER")
    except Exception as e:
        refund_credit(m.from_user.id)
        logger.error(f"Error in handle_pak_number: {e}")
        bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")

# ========== PINCODE INFO ==========
@bot.message_handler(func=lambda c: c.text == "📮 Pincode Info")
@handle_errors
def ask_pincode(m):
    bot.send_message(m.chat.id, "📮 Send 6-digit Indian pincode:")
    bot.register_next_step_handler(m, handle_pincode)

@handle_errors
def handle_pincode(m):
    try:
        pincode = m.text.strip()
        if not re.fullmatch(r"\d{6}", pincode):
            return bot.send_message(m.chat.id, "⚠️ Invalid pincode. Please enter a 6-digit pincode.")
        
        if not ensure_and_charge(m.from_user.id, m.chat.id):
            return
        
        progress_msg = bot.send_message(m.chat.id, "🔍 Searching for pincode information...")
        
        data = make_request(f"https://pincode-info-j4tnx.vercel.app/pincode={pincode}")
        
        try:
            bot.delete_message(m.chat.id, progress_msg.message_id)
        except:
            pass
        
        if not data or not isinstance(data, list) or len(data) == 0:
            refund_credit(m.from_user.id)
            return bot.send_message(m.chat.id, "❌ No data found for this pincode.")
        
        pincode_data = data[0]
        if pincode_data.get("Status") != "Success" or "PostOffice" not in pincode_data:
            refund_credit(m.from_user.id)
            return bot.send_message(m.chat.id, "❌ No data found for this pincode.")
        
        post_offices = pincode_data.get("PostOffice", [])
        if not post_offices:
            refund_credit(m.from_user.id)
            return bot.send_message(m.chat.id, "❌ No post office data found for this pincode.")
        
        message = pincode_data.get("Message", "")
        header = f"""
📮 <b>Pincode Information</b>
🔍 <b>Pincode:</b> {pincode}
📊 <b>{message}</b>
━━━━━━━━━━━━━━━━━━
"""
        bot.send_message(m.chat.id, header, parse_mode="HTML")
        
        for i, office in enumerate(post_offices, 1):
            name = clean(office.get("Name"))
            branch_type = clean(office.get("BranchType"))
            delivery_status = clean(office.get("DeliveryStatus"))
            district = clean(office.get("District"))
            division = clean(office.get("Division"))
            region = clean(office.get("Region"))
            block = clean(office.get("Block"))
            state = clean(office.get("State"))
            country = clean(office.get("Country"))
            
            delivery_emoji = "✅" if delivery_status == "Delivery" else "❌"
            
            out = f"""
📋 <b>Post Office #{i}</b>
🏢 <b>Name:</b> {name}
🏛️ <b>Type:</b> {branch_type}
{delivery_emoji} <b>Delivery Status:</b> {delivery_status}
📍 <b>District:</b> {district}
🗂️ <b>Division:</b> {division}
🌐 <b>Region:</b> {region}
🏘️ <b>Block:</b> {block}
🏛️ <b>State:</b> {state}
🌍 <b>Country:</b> {country}
━━━━━━━━━━━━━━━━━━
"""
            bot.send_message(m.chat.id, out, parse_mode="HTML")
        
        footer = f"""
✅ <b>Search completed successfully!</b>
💳 <b>Credits Used:</b> 1
📊 <b>Total Post Offices:</b> {len(post_offices)}
"""
        bot.send_message(m.chat.id, footer, parse_mode="HTML")
        
        add_history(m.from_user.id, pincode, "PINCODE")
    except Exception as e:
        refund_credit(m.from_user.id)
        logger.error(f"Error in handle_pincode: {e}")
        bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")
        
# ========== VEHICLE INFO ==========
@bot.message_handler(func=lambda c: c.text == "🚘 Vehicle Info")
@handle_errors
def ask_vehicle(m):
    bot.send_message(m.chat.id, "🚘 Send vehicle registration number (e.g., MH01AB1234):")
    bot.register_next_step_handler(m, handle_vehicle)

@handle_errors
def handle_vehicle(m):
    try:
        rc_number = m.text.strip().upper()
        if not re.match(r"^[A-Z]{2}\d{2}[A-Z]{1,2}\d{4}$", rc_number):
            return bot.send_message(m.chat.id, "⚠️ Invalid vehicle registration number. Please enter in format like MH01AB1234")
        
        if not ensure_and_charge(m.from_user.id, m.chat.id):
            return
        
        progress_msg = bot.send_message(m.chat.id, "🔍 Searching for vehicle information...")
        
        data = make_request(f"https://rc-info-ng.vercel.app/?rc={rc_number}")
        
        try:
            bot.delete_message(m.chat.id, progress_msg.message_id)
        except:
            pass
        
        if not data or not data.get("rc_number"):
            refund_credit(m.from_user.id)
            return bot.send_message(m.chat.id, "❌ No data found for this vehicle registration number.")
        
        rc_num = clean(data.get("rc_number"))
        owner_name = clean(data.get("owner_name"))
        father_name = clean(data.get("father_name"))
        model_name = clean(data.get("model_name"))
        maker_model = clean(data.get("maker_model"))
        vehicle_class = clean(data.get("vehicle_class"))
        fuel_type = clean(data.get("fuel_type"))
        registration_date = clean(data.get("registration_date"))
        insurance_company = clean(data.get("insurance_company"))
        insurance_no = clean(data.get("insurance_no"))
        insurance_expiry = clean(data.get("insurance_expiry"))
        fitness_upto = clean(data.get("fitness_upto"))
        rto = clean(data.get("rto"))
        address = clean(data.get("address"))
        city = clean(data.get("city"))
        phone = clean(data.get("phone"))
        
        fuel_emoji = "⛽" if fuel_type == "PETROL" else "🛢️" if fuel_type == "DIESEL" else "⚡" if fuel_type == "ELECTRIC" else "🔧"
        
        out = f"""
🚘 <b>Vehicle Information</b>
━━━━━━━━━━━━━━━━━━
📝 <b>Registration Number:</b> <code>{rc_num}</code>
👤 <b>Owner Name:</b> {owner_name}
👨‍👩‍👦 <b>Father's Name:</b> {father_name}
🏛️ <b>RTO:</b> {rto}
📍 <b>City:</b> {city}
📞 <b>Phone:</b> {phone}

🚗 <b>Vehicle Details:</b>
🏭 <b>Manufacturer:</b> {model_name}
🛵 <b>Model:</b> {maker_model}
🏷️ <b>Class:</b> {vehicle_class}
{fuel_emoji} <b>Fuel Type:</b> {fuel_type}
📅 <b>Registration Date:</b> {registration_date}

📋 <b>Insurance Details:</b>
🏢 <b>Company:</b> {insurance_company}
📄 <b>Policy Number:</b> {insurance_no}
📅 <b>Expiry Date:</b> {insurance_expiry}

📅 <b>Fitness Valid Upto:</b> {fitness_upto}

🏠 <b>Address:</b> {address}
━━━━━━━━━━━━━━━━━━
"""
        bot.send_message(m.chat.id, out, parse_mode="HTML")
        add_history(m.from_user.id, rc_number, "VEHICLE")
    except Exception as e:
        refund_credit(m.from_user.id)
        logger.error(f"Error in handle_vehicle: {e}")
        bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")

# ========== AADHAAR INFO ==========
@bot.message_handler(func=lambda c: c.text == "🆔 Aadhaar Info")
@handle_errors
def ask_aadhar(m):
    bot.send_message(m.chat.id, "🆔 Send 12-digit Aadhaar number: AND WIAT FOR 4-5 MINT BECOUSE ADHAR API IS SLOW 😥")
    bot.register_next_step_handler(m, handle_aadhar)

@handle_errors
def handle_aadhar(m):
    try:
        aid = m.text.strip()
        if not re.fullmatch(r"\d{12}", aid):
            return bot.send_message(m.chat.id, "⚠️ Invalid Aadhaar number. Please enter a 12-digit Aadhaar number.")
        
        if not ensure_and_charge(m.from_user.id, m.chat.id):
            return
        
        progress_msg = bot.send_message(m.chat.id, "🔍 Searching for Aadhaar information... (This may take 4-5 minutes)")
        
        try:
            r = requests.get(f"https://numinfoapi.zerovault.workers.dev/search/aadhar?value={aid}&key=bugsec", timeout=300)
            logger.info(f"Aadhaar API Response Status: {r.status_code}")
            
            try:
                bot.delete_message(m.chat.id, progress_msg.message_id)
            except:
                pass
            
            if r.status_code != 200:
                refund_credit(m.from_user.id)
                return bot.send_message(m.chat.id, "❌ API request failed. Please try again later.")
            
            try:
                # API से आया हुआ रॉ टेक्स्ट लें
                raw_response_text = r.text
                logger.info(f"Aadhaar API Raw Response: {raw_response_text[:500]}...") # लॉग में पहले 500 कैरेक्टर सेव करें
            except Exception as e:
                logger.error(f"Error reading response text: {e}")
                refund_credit(m.from_user.id)
                return bot.send_message(m.chat.id, "❌ Could not read API response.")

            # --- यहाँ मुख्य लॉजिक बदल गया है ---
            # हम अब JSON को पार्स नहीं करेंगे, बल्कि सीधे रॉ टेक्स्ट को भेजेंगे
            # लेकिन पहले चेक करेंगे कि रेस्पॉन्स खाली तो नहीं है
            if not raw_response_text or raw_response_text.strip() == "":
                refund_credit(m.from_user.id)
                return bot.send_message(m.chat.id, "📭 No Aadhaar Data Found!")
            
            # रेस्पॉन्स को एक सुंदर फॉर्मेट में भेजने के लिए प्रीपेयर करें
            header = f"""
🔍 <b>Raw API Response for Aadhaar:</b> {aid[:4]}XXXXXXXX{aid[-2:]}
━━━━━━━━━━━━━━━━━━
<code>
"""
            
            footer = f"""
</code>
━━━━━━━━━━━━━━━━━━
✅ <b>Search completed!</b>
💳 <b>Credits Used:</b> 1
"""
            
            # हेडर और फुटर के साथ पूरा मैसेज बनाएं
            full_message = header + raw_response_text + footer

            # अब `send_long` फंक्शन का इस्तेमाल करके लंबे मैसेज को छोटे हिस्सों में भेजें
            send_long(m.chat.id, full_message)
            
            add_history(m.from_user.id, aid, "AADHAAR_RAW")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error: {e}")
            try:
                bot.delete_message(m.chat.id, progress_msg.message_id)
            except:
                pass
            refund_credit(m.from_user.id)
            bot.send_message(m.chat.id, "❌ Network error. Please try again later.")
        except Exception as e:
            logger.error(f"Unexpected error in handle_aadhar: {e}")
            try:
                bot.delete_message(m.chat.id, progress_msg.message_id)
            except:
                pass
            refund_credit(m.from_user.id)
            bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")
    except Exception as e:
        logger.error(f"Outer error in handle_aadhar: {e}")
        refund_credit(m.from_user.id)
        bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")

# ========== ICMR INFO ==========
@bot.message_handler(func=lambda c: c.text == "🧪 ICMR Number Info")
@handle_errors
def ask_icmr(m):
    bot.send_message(m.chat.id, "🧪 Send 10-digit number for ICMR lookup:")
    bot.register_next_step_handler(m, handle_icmr)

@handle_errors
def handle_icmr(m):
    try:
        num = m.text.strip()
        if not re.fullmatch(r"\d{10}", num):
            return bot.send_message(m.chat.id, "⚠️ Invalid 10-digit number.")
        
        if not ensure_and_charge(m.from_user.id, m.chat.id):
            return
        
        progress_msg = bot.send_message(m.chat.id, "🔍 Searching for ICMR information...")
        
        data = make_request(f"https://raju09.serv00.net/ICMR/ICMR_api.php?phone={num}")
        
        try:
            bot.delete_message(m.chat.id, progress_msg.message_id)
        except:
            pass
        
        if not data or data.get("status") != "success" or not data.get("data"):
            refund_credit(m.from_user.id)
            return bot.send_message(m.chat.id, "📭 No ICMR Data Found!")
        
        records = data["data"]
        results_count = data.get("count", len(records))
        
        header = f"""
🧪 <b>ICMR Information Lookup Results</b>
🔍 <b>Phone Number:</b> {num}
📊 <b>Total Records Found:</b> {results_count}
━━━━━━━━━━━━━━━━━━
"""
        bot.send_message(m.chat.id, header, parse_mode="HTML")
        
        for i, rec in enumerate(records, 1):
            name = clean(rec.get("name"))
            fathers_name = clean(rec.get("fathersName"))
            phone_number = clean(rec.get("phoneNumber"))
            aadhar_number = clean(rec.get("aadharNumber"))
            age = clean(rec.get("age"))
            gender = clean(rec.get("gender"))
            address = clean(rec.get("address"))
            district = clean(rec.get("district"))
            pincode = clean(rec.get("pincode"))
            state = clean(rec.get("state"))
            town = clean(rec.get("town"))
            
            gender_emoji = "👩" if gender.lower() == "female" else "👨" if gender.lower() == "male" else "🧑"
            
            out = f"""
📋 <b>Record #{i}</b>
{gender_emoji} <b>Name:</b> {name}
👨‍👩‍👦 <b>Father's Name:</b> {fathers_name if fathers_name else "N/A"}
📱 <b>Phone Number:</b> {phone_number}
🆔 <b>Aadhaar Number:</b> {aadhar_number if aadhar_number else "N/A"}
🎂 <b>Age:</b> {age}
{gender_emoji} <b>Gender:</b> {gender}
🏠 <b>Address:</b> {address}
📍 <b>District:</b> {district}
🏙️ <b>Town:</b> {town if town else "N/A"}
📮 <b>Pincode:</b> {pincode if pincode else "N/A"}
🏛️ <b>State:</b> {state}
━━━━━━━━━━━━━━━━━━
"""
            bot.send_message(m.chat.id, out, parse_mode="HTML")
        
        footer = f"""
✅ <b>Search completed successfully!</b>
💳 <b>Credits Used:</b> 1
📊 <b>Total Records:</b> {results_count}
"""
        bot.send_message(m.chat.id, footer, parse_mode="HTML")
        
        add_history(m.from_user.id, num, "ICMR")
    except Exception as e:
        refund_credit(m.from_user.id)
        logger.error(f"Error in handle_icmr: {e}")
        bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")

# ========== IFSC CODE INFO ==========
@bot.message_handler(func=lambda c: c.text == "🏦 IFSC Code Info")
@handle_errors
def ask_ifsc(m):
    bot.send_message(m.chat.id, "🏦 Send 11-character IFSC code (e.g., SBIN0004843):")
    bot.register_next_step_handler(m, handle_ifsc)

@handle_errors
def handle_ifsc(m):
    try:
        ifsc_code = m.text.strip().upper()
        # IFSC कोड वैलिडेशन - 4 अक्षर, 7 अंक
        if not re.fullmatch(r"[A-Z]{4}\d{7}", ifsc_code):
            return bot.send_message(m.chat.id, "⚠️ Invalid IFSC code. Please enter a valid 11-character IFSC code (e.g., SBIN0004843).")
        
        # यूजर के क्रेडिट चेक करें और काटें
        if not ensure_and_charge(m.from_user.id, m.chat.id):
            return
        
        # प्रोग्रेस मैसेज भेजें
        progress_msg = bot.send_message(m.chat.id, "🔍 Searching for IFSC code information...")
        
        data = make_request(f"https://ifsc.razorpay.com/{ifsc_code}")
        
        # प्रोग्रेस मैसेज हटाएं
        try:
            bot.delete_message(m.chat.id, progress_msg.message_id)
        except:
            pass
        
        if not data or not data.get("IFSC"):
            refund_credit(m.from_user.id)
            return bot.send_message(m.chat.id, "❌ No data found for this IFSC code.")
        
        # डेटा निकालें
        bank = clean(data.get("BANK"))
        ifsc = clean(data.get("IFSC"))
        branch = clean(data.get("BRANCH"))
        address = clean(data.get("ADDRESS"))
        city = clean(data.get("CITY"))
        district = clean(data.get("DISTRICT"))
        state = clean(data.get("STATE"))
        contact = clean(data.get("CONTACT"))
        micr = clean(data.get("MICR"))
        centre = clean(data.get("CENTRE"))
        bankcode = clean(data.get("BANKCODE"))
        iso3166 = clean(data.get("ISO3166"))
        
        # सेवाएं निकालें
        upi = data.get("UPI", False)
        rtgs = data.get("RTGS", False)
        neft = data.get("NEFT", False)
        imps = data.get("IMPS", False)
        swift = clean(data.get("SWIFT"))
        
        # सेवाओं के लिए इमोजी
        upi_emoji = "✅" if upi else "❌"
        rtgs_emoji = "✅" if rtgs else "❌"
        neft_emoji = "✅" if neft else "❌"
        imps_emoji = "✅" if imps else "❌"
        swift_emoji = "✅" if swift else "❌"
        
        # आउटपुट फॉर्मेट करें
        out = f"""
🏦 <b>Bank Information</b>
━━━━━━━━━━━━━━━━━━
🏛️ <b>Bank Name:</b> {bank}
🆔 <b>IFSC Code:</b> <code>{ifsc}</code>
🏢 <b>Branch:</b> {branch}
🏠 <b>Address:</b> {address}
📍 <b>City:</b> {city}
🗺️ <b>District:</b> {district}
🏛️ <b>State:</b> {state}
📞 <b>Contact:</b> {contact if contact else "N/A"}
🔢 <b>MICR Code:</b> {micr}
🏛️ <b>Centre:</b> {centre}
🆔 <b>Bank Code:</b> {bankcode}
🌍 <b>ISO Code:</b> {iso3166}

💸 <b>Available Services:</b>
{upi_emoji} <b>UPI:</b> {"Available" if upi else "Not Available"}
{rtgs_emoji} <b>RTGS:</b> {"Available" if rtgs else "Not Available"}
{neft_emoji} <b>NEFT:</b> {"Available" if neft else "Not Available"}
{imps_emoji} <b>IMPS:</b> {"Available" if imps else "Not Available"}
{swift_emoji} <b>SWIFT:</b> {swift if swift else "Not Available"}
━━━━━━━━━━━━━━━━━━
"""
        bot.send_message(m.chat.id, out, parse_mode="HTML")
        add_history(m.from_user.id, ifsc_code, "IFSC")
    except Exception as e:
        refund_credit(m.from_user.id)
        logger.error(f"Error in handle_ifsc: {e}")
        bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")

# ========== UPI ID INFO ==========
@bot.message_handler(func=lambda c: c.text == "💸 UPI ID Info")
@handle_errors
def ask_upi(m):
    bot.send_message(m.chat.id, "💸 Send UPI ID (e.g., mohd.kaifu@sbi):")
    bot.register_next_step_handler(m, handle_upi)

@handle_errors
def handle_upi(m):
    try:
        upi_id = m.text.strip()
        # UPI ID वैलिडेशन - बेसिक फॉर्मेट चेक
        if not re.fullmatch(r"[a-zA-Z0-9._-]+@[a-zA-Z0-9]+", upi_id):
            return bot.send_message(m.chat.id, "⚠️ Invalid UPI ID format. Please enter a valid UPI ID (e.g., mohd.kaifu@sbi).")
        
        # यूजर के क्रेडिट चेक करें और काटें
        if not ensure_and_charge(m.from_user.id, m.chat.id):
            return
        
        # प्रोग्रेस मैसेज भेजें
        progress_msg = bot.send_message(m.chat.id, "🔍 Searching for UPI ID information...")
        
        data = make_request(f"https://upi-info.vercel.app/api/upi?upi_id={upi_id}&key=456")
        
        # प्रोग्रेस मैसेज हटाएं
        try:
            bot.delete_message(m.chat.id, progress_msg.message_id)
        except:
            pass
        
        if not data or not data.get("vpa_details"):
            refund_credit(m.from_user.id)
            return bot.send_message(m.chat.id, "❌ No data found for this UPI ID.")
        
        # VPA डिटेल्स निकालें
        vpa_details = data.get("vpa_details", {})
        vpa = clean(vpa_details.get("vpa"))
        name = clean(vpa_details.get("name"))
        ifsc = clean(vpa_details.get("ifsc"))
        
        # बैंक डिटेल्स निकालें
        bank_details = data.get("bank_details_raw", {})
        bank = clean(bank_details.get("BANK"))
        branch = clean(bank_details.get("BRANCH"))
        address = clean(bank_details.get("ADDRESS"))
        city = clean(bank_details.get("CITY"))
        district = clean(bank_details.get("DISTRICT"))
        state = clean(bank_details.get("STATE"))
        contact = clean(bank_details.get("CONTACT"))
        micr = clean(bank_details.get("MICR"))
        centre = clean(bank_details.get("CENTRE"))
        bankcode = clean(bank_details.get("BANKCODE"))
        iso3166 = clean(bank_details.get("ISO3166"))
        
        # सेवाएं निकालें
        upi = bank_details.get("UPI", False)
        rtgs = bank_details.get("RTGS", False)
        neft = bank_details.get("NEFT", False)
        imps = bank_details.get("IMPS", False)
        swift = clean(bank_details.get("SWIFT"))
        
        # सेवाओं के लिए इमोजी
        upi_emoji = "✅" if upi else "❌"
        rtgs_emoji = "✅" if rtgs else "❌"
        neft_emoji = "✅" if neft else "❌"
        imps_emoji = "✅" if imps else "❌"
        swift_emoji = "✅" if swift else "❌"
        
        # आउटपुट फॉर्मेट करें
        out = f"""
💸 <b>UPI ID Information</b>
━━━━━━━━━━━━━━━━━━
💳 <b>UPI ID:</b> <code>{vpa}</code>
👤 <b>Account Holder:</b> {name}
🆔 <b>IFSC Code:</b> {ifsc}

🏦 <b>Bank Details:</b>
🏛️ <b>Bank Name:</b> {bank}
🏢 <b>Branch:</b> {branch}
🏠 <b>Address:</b> {address}
📍 <b>City:</b> {city}
🗺️ <b>District:</b> {district}
🏛️ <b>State:</b> {state}
📞 <b>Contact:</b> {contact if contact else "N/A"}
🔢 <b>MICR Code:</b> {micr}
🏛️ <b>Centre:</b> {centre}
🆔 <b>Bank Code:</b> {bankcode}
🌍 <b>ISO Code:</b> {iso3166}

💸 <b>Available Services:</b>
{upi_emoji} <b>UPI:</b> {"Available" if upi else "Not Available"}
{rtgs_emoji} <b>RTGS:</b> {"Available" if rtgs else "Not Available"}
{neft_emoji} <b>NEFT:</b> {"Available" if neft else "Not Available"}
{imps_emoji} <b>IMPS:</b> {"Available" if imps else "Not Available"}
{swift_emoji} <b>SWIFT:</b> {swift if swift else "Not Available"}
━━━━━━━━━━━━━━━━━━
"""
        bot.send_message(m.chat.id, out, parse_mode="HTML")
        add_history(m.from_user.id, upi_id, "UPI")
    except Exception as e:
        refund_credit(m.from_user.id)
        logger.error(f"Error in handle_upi: {e}")
        bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")

# ========== RATION CARD INFO ==========
@bot.message_handler(func=lambda c: c.text == "📋 Ration Card Info")
@handle_errors
def ask_ration(m):
    bot.send_message(m.chat.id, "📋 Send 12-digit Aadhaar number linked to ration card:")
    bot.register_next_step_handler(m, handle_ration)

@handle_errors
def handle_ration(m):
    try:
        aadhaar = m.text.strip()
        if not re.fullmatch(r"\d{12}", aadhaar):
            return bot.send_message(m.chat.id, "⚠️ Invalid Aadhaar number. Please enter a 12-digit Aadhaar number.")
        
        # यूजर के क्रेडिट चेक करें और काटें
        if not ensure_and_charge(m.from_user.id, m.chat.id):
            return
        
        # प्रोग्रेस मैसेज भेजें
        progress_msg = bot.send_message(m.chat.id, "🔍 Searching for ration card information...")
        
        data = make_request(f"https://family-members-n5um.vercel.app/fetch?aadhaar={aadhaar}&key=paidchx")
        
        # प्रोग्रेस मैसेज हटाएं
        try:
            bot.delete_message(m.chat.id, progress_msg.message_id)
        except:
            pass
        
        if not data or not data.get("rcId"):
            refund_credit(m.from_user.id)
            return bot.send_message(m.chat.id, "❌ No ration card data found for this Aadhaar number.")
        
        # बेसिक जानकारी निकालें
        rc_id = clean(data.get("rcId"))
        scheme_id = clean(data.get("schemeId"))
        scheme_name = clean(data.get("schemeName"))
        address = clean(data.get("address"))
        home_state_name = clean(data.get("homeStateName"))
        home_dist_name = clean(data.get("homeDistName"))
        allowed_onorc = clean(data.get("allowed_onorc"))
        dup_uid_status = clean(data.get("dup_uid_status"))
        fps_id = clean(data.get("fpsId"))
        
        # स्कीम के लिए इमोजी
        scheme_emoji = "🍚" if scheme_id == "PHH" else "🍛" if scheme_id == "AY" else "📋"
        
        # आउटपुट हेडर
        header = f"""
📋 <b>Ration Card Information</b>
━━━━━━━━━━━━━━━━━━
🆔 <b>Ration Card ID:</b> {rc_id}
{scheme_emoji} <b>Scheme:</b> {scheme_name} ({scheme_id})
🏛️ <b>State:</b> {home_state_name}
📍 <b>District:</b> {home_dist_name}
🏠 <b>Address:</b> {address}
✅ <b>Allowed ONORC:</b> {allowed_onorc}
🔄 <b>Duplicate UID Status:</b> {dup_uid_status}
🏪 <b>FPS ID:</b> {fps_id}
━━━━━━━━━━━━━━━━━━
"""
        bot.send_message(m.chat.id, header, parse_mode="HTML")
        
        # फैमिली मेंबर्स की जानकारी
        member_details = data.get("memberDetailsList", [])
        if member_details:
            members_header = f"""
👨‍👩‍👧‍👦 <b>Family Members ({len(member_details)})</b>
━━━━━━━━━━━━━━━━━━
"""
            bot.send_message(m.chat.id, members_header, parse_mode="HTML")
            
            for i, member in enumerate(member_details, 1):
                member_id = clean(member.get("memberId"))
                member_name = clean(member.get("memberName"))
                relationship_code = clean(member.get("relationship_code"))
                relationship_name = clean(member.get("releationship_name"))
                uid_status = clean(member.get("uid"))
                
                # UID स्टेटस के लिए इमोजी
                uid_emoji = "✅" if uid_status == "Yes" else "❌"
                
                # रिश्ते के लिए इमोजी
                rel_emoji = "👤" if relationship_name == "SELF" else "👨" if "SON" in relationship_name else "👩" if "DAUGHTER" in relationship_name else "👴" if "FATHER" in relationship_name else "👵" if "MOTHER" in relationship_name else "🧑"
                
                member_out = f"""
📋 <b>Member #{i}</b>
{rel_emoji} <b>Name:</b> {member_name}
🔗 <b>Relationship:</b> {relationship_name}
{uid_emoji} <b>Aadhaar Linked:</b> {uid_status}
━━━━━━━━━━━━━━━━━━
"""
                bot.send_message(m.chat.id, member_out, parse_mode="HTML")
        
        # फुटर मैसेज
        footer = f"""
✅ <b>Search completed successfully!</b>
💳 <b>Credits Used:</b> 1
👨‍👩‍👧‍👦 <b>Total Family Members:</b> {len(member_details)}
"""
        bot.send_message(m.chat.id, footer, parse_mode="HTML")
        
        add_history(m.from_user.id, aadhaar, "RATION_CARD")
    except Exception as e:
        refund_credit(m.from_user.id)
        logger.error(f"Error in handle_ration: {e}")
        bot.send_message(m.chat.id, f"⚠️ Error: <code>{str(e)}</code>")

# ========== WEB SERVER FOR KEEPING BOT ALIVE ==========
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_web_server():
    app.run(host='0.0.0.0', port=8080)

# Start the web server in a separate thread
Thread(target=run_web_server).start()

# ========== START THE BOT ==========
if __name__ == "__main__":
    logger.info("Starting bot...")
    while True:
        try:
            logger.info("Polling started...")
            bot.polling(none_stop=True)
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
            time.sleep(10)  # Wait before restarting polling