
import os
import subprocess
import sqlite3
import telebot
import threading
import time
import uuid
import signal
import random
import platform
import zipfile
import json
import shutil
from pathlib import Path
from telebot import types
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from flask import Flask, render_template_string, send_file

# ১. Configuration
class Config:
    TOKEN = os.environ.get('BOT_TOKEN', '8494225623:AAG_HRSHoBpt36bdeUvYJL4ONnh-2bf6BnY')
    ADMIN_ID = int(os.environ.get('ADMIN_ID', 7832264582))
    PROJECT_DIR = 'projects'
    DB_NAME = 'cyber_v2.db'
    PORT = int(os.environ.get('PORT', 10000))
    MAINTENANCE = False
    ADMIN_USERNAME = '@zerox6t9'

# Bot instance with error handling
try:
    bot = telebot.TeleBot(Config.TOKEN, skip_pending=True)
except Exception as e:
    print(f"❌ Bot initialization error: {e}")
    exit(1)

project_path = Path(Config.PROJECT_DIR)
project_path.mkdir(exist_ok=True)
app = Flask(__name__)

# ২. Database Functions
def init_db():
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    
    # Drop old tables if they exist
    c.execute("DROP TABLE IF EXISTS users")
    c.execute("DROP TABLE IF EXISTS keys")
    c.execute("DROP TABLE IF EXISTS deployments")
    
    # Create new tables
    c.execute('''CREATE TABLE users 
                (id INTEGER PRIMARY KEY, username TEXT, expiry TEXT, file_limit INTEGER, is_prime INTEGER, join_date TEXT)''')
    c.execute('''CREATE TABLE keys 
                (key TEXT PRIMARY KEY, duration_days INTEGER, file_limit INTEGER, created_date TEXT, used INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE deployments 
                (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, bot_name TEXT, filename TEXT, pid INTEGER, 
                 start_time TEXT, status TEXT, cpu_usage REAL, ram_usage REAL)''')
    
    # Insert admin user
    join_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?, ?, ?)", 
             (Config.ADMIN_ID, 'admin', '2099-12-31 23:59:59', 999, 1, join_date))
    
    conn.commit()
    conn.close()

# Initialize database
init_db()

# System Monitoring Functions
def get_system_stats():
    return {
        'cpu_percent': random.randint(20, 80),
        'ram_percent': random.randint(30, 70),
        'disk_percent': random.randint(40, 60)
    }

def get_process_stats(pid):
    try:
        os.kill(pid, 0)
        return True
    except:
        return False

# Helper Functions
def get_user(user_id):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    user = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return user

def is_prime(user_id):
    user = get_user(user_id)
    if user and user[2]:
        try:
            expiry = datetime.strptime(user[2], '%Y-%m-%d %H:%M:%S')
            if expiry > datetime.now():
                return True, expiry
            else:
                # Prime expired, update status
                conn = sqlite3.connect(Config.DB_NAME)
                c = conn.cursor()
                c.execute("UPDATE users SET is_prime=0 WHERE id=?", (user_id,))
                conn.commit()
                conn.close()
                return False, expiry
        except:
            return False, None
    return False, None

def get_user_bots(user_id):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    bots = c.execute("SELECT id, bot_name, filename, pid, start_time, status FROM deployments WHERE user_id=?", 
                    (user_id,)).fetchall()
    conn.close()
    return bots

def create_progress_bar(percentage):
    bars = int(percentage / 10)
    return "█" * bars + "░" * (10 - bars)

# Keyboards
def main_menu(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    prime_status, expiry_date = is_prime(user_id)
    
    if not prime_status:
        markup.add(types.InlineKeyboardButton("🔑 Activate Prime", callback_data="activate_prime"))
        markup.add(types.InlineKeyboardButton("💎 Get Prime", url=f"https://t.me/{Config.ADMIN_USERNAME.replace('@', '')}"))
    else:
        markup.add(
            types.InlineKeyboardButton("📤 Upload Bot", callback_data='upload'),
            types.InlineKeyboardButton("🤖 My Bots", callback_data='my_bots')
        )
        markup.add(
            types.InlineKeyboardButton("🚀 Deploy Bot", callback_data='deploy_new'),
            types.InlineKeyboardButton("📊 Dashboard", callback_data='dashboard')
        )
    
    markup.add(types.InlineKeyboardButton("⚙️ Settings", callback_data='settings'))
    
    # Show Get Prime button only if not prime or expired
    if not prime_status:
        markup.add(types.InlineKeyboardButton("💎 Get Prime Pass", url=f"https://t.me/{Config.ADMIN_USERNAME.replace('@', '')}"))
    
    if user_id == Config.ADMIN_ID:
        markup.add(types.InlineKeyboardButton("👑 Admin Panel", callback_data='admin_panel'))
    
    return markup

def admin_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🎫 Generate Key", callback_data="gen_key"),
        types.InlineKeyboardButton("👥 All Users", callback_data="all_users")
    )
    markup.add(
        types.InlineKeyboardButton("🤖 All Bots", callback_data="all_bots"),
        types.InlineKeyboardButton("📈 Statistics", callback_data="stats")
    )
    markup.add(
        types.InlineKeyboardButton("🗄️ Database", callback_data="view_database"),
        types.InlineKeyboardButton("💾 Backup DB", callback_data="backup_db")
    )
    markup.add(
        types.InlineKeyboardButton("⚙️ Maintenance", callback_data="maintenance"),
        types.InlineKeyboardButton("🏠 Main Menu", callback_data="back_main")
    )
    return markup

def bot_actions_menu(bot_id, is_running):
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    if is_running:
        markup.add(types.InlineKeyboardButton("🛑 Stop Bot", callback_data=f"stop_{bot_id}"))
    else:
        markup.add(types.InlineKeyboardButton("🚀 Start Bot", callback_data=f"start_{bot_id}"))
    
    markup.add(
        types.InlineKeyboardButton("🗑️ Delete Bot", callback_data=f"delete_{bot_id}"),
        types.InlineKeyboardButton("📥 Export Bot", callback_data=f"export_{bot_id}")
    )
    markup.add(types.InlineKeyboardButton("🔙 My Bots", callback_data="my_bots"))
    return markup

# Commands
@bot.message_handler(commands=['start'])
def welcome(message):
    uid = message.from_user.id
    username = message.from_user.username or "User"
    
    if Config.MAINTENANCE and uid != Config.ADMIN_ID:
        bot.send_message(message.chat.id, "🛠 **System Maintenance**\n\nWe're currently upgrading our servers. Please try again later.")
        return
    
    user = get_user(uid)
    if not user:
        conn = sqlite3.connect(Config.DB_NAME)
        c = conn.cursor()
        join_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?, ?, ?)", 
                 (uid, username, None, 0, 0, join_date))
        conn.commit()
        conn.close()
        user = get_user(uid)
    
    prime_status, expiry_date = is_prime(uid)
    
    status = "PRIME 👑" if prime_status else "FREE 🆓"
    expiry = expiry_date.strftime('%Y-%m-%d %H:%M:%S') if expiry_date else "Not Activated"
    
    # Check if prime expired
    if not prime_status and expiry_date and expiry_date < datetime.now():
        status = "EXPIRED ⚠️"
    
    text = f"""
🤖 **ZEN X HOST BOT v3.0.1**
━━━━━━━━━━━━━━━━━━━━
👤 **User:** @{username}
🆔 **ID:** `{uid}`
💎 **Status:** {status}
📅 **Expiry:** {expiry}
━━━━━━━━━━━━━━━━━━━━
📊 **Account Details:**
• Plan: {'Premium' if prime_status else 'Free'}
• File Limit: {user[3] if user else 0} files
━━━━━━━━━━━━━━━━━━━━
"""
    
    bot.send_message(message.chat.id, text, reply_markup=main_menu(uid), parse_mode="Markdown")

@bot.message_handler(commands=['admin'])
def admin_command(message):
    if message.from_user.id == Config.ADMIN_ID:
        text = """
👑 **ADMIN CONTROL PANEL**
━━━━━━━━━━━━━━━━━━━━
Welcome to the admin dashboard.
━━━━━━━━━━━━━━━━━━━━
"""
        bot.send_message(message.chat.id, text, reply_markup=admin_menu(), parse_mode="Markdown")
    else:
        bot.reply_to(message, "⛔ **Access Denied!**")

# Callback Query Handler with improved error handling
@bot.callback_query_handler(func=lambda call: True)
def callback_manager(call):
    try:
        uid = call.from_user.id
        chat_id = call.message.chat.id
        mid = call.message.message_id
        
        if call.data == "activate_prime":
            msg = bot.edit_message_text("""
🔑 **ACTIVATE PRIME PASS**
━━━━━━━━━━━━━━━━━━━━
Enter your activation key:
Format: `PRIME-XXXXXX`
━━━━━━━━━━━━━━━━━━━━
""", chat_id, mid, parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_key_step, mid)
            
        elif call.data == "upload":
            prime_status, _ = is_prime(uid)
            if not prime_status:
                bot.answer_callback_query(call.id, "⚠️ Premium feature! Activate Prime first.")
                return
            msg = bot.edit_message_text("""
📤 **UPLOAD BOT FILE**
━━━━━━━━━━━━━━━━━━━━
Send your Python (.py) or ZIP file:
• Max size: 5.5MB
━━━━━━━━━━━━━━━━━━━━
""", chat_id, mid, parse_mode="Markdown")
            bot.register_next_step_handler(msg, upload_file_step, mid)
            
        elif call.data == "my_bots":
            show_my_bots(call)
            
        elif call.data == "dashboard":
            show_dashboard(call)
            
        elif call.data == "deploy_new":
            prime_status, _ = is_prime(uid)
            if not prime_status:
                bot.answer_callback_query(call.id, "⚠️ Premium feature!")
                return
            show_available_files(call)
            
        elif call.data.startswith("bot_"):
            bot_id = call.data.split("_")[1]
            show_bot_details(call, bot_id)
            
        elif call.data.startswith("deploy_"):
            filename = call.data.split("_")[1]
            start_deployment(call, filename)
            
        elif call.data.startswith("stop_"):
            bot_id = call.data.split("_")[1]
            stop_bot(call, bot_id)
            
        elif call.data.startswith("start_"):
            bot_id = call.data.split("_")[1]
            start_bot_process(call, bot_id)
            
        elif call.data.startswith("delete_"):
            bot_id = call.data.split("_")[1]
            start_delete_process(call, bot_id)
            
        elif call.data.startswith("confirm_delete_"):
            parts = call.data.split("_")
            bot_id = parts[2]
            confirm_delete_bot(call, bot_id)
            
        elif call.data.startswith("cancel_delete_"):
            bot_id = call.data.split("_")[2]
            cancel_delete_bot(call, bot_id)
            
        elif call.data.startswith("export_"):
            bot_id = call.data.split("_")[1]
            export_bot(call, bot_id)
            
        elif call.data == "admin_panel":
            if uid == Config.ADMIN_ID:
                admin_panel_callback(call)
            else:
                bot.answer_callback_query(call.id, "⛔ Access Denied!")
                
        elif call.data == "gen_key" and uid == Config.ADMIN_ID:
            gen_key_step1(call)
            
        elif call.data == "all_users" and uid == Config.ADMIN_ID:
            show_all_users(call)
            
        elif call.data == "all_bots" and uid == Config.ADMIN_ID:
            show_all_bots_admin(call)
            
        elif call.data == "stats" and uid == Config.ADMIN_ID:
            show_admin_stats(call)
            
        elif call.data == "view_database" and uid == Config.ADMIN_ID:
            view_database_page(call, 1)
            
        elif call.data == "backup_db" and uid == Config.ADMIN_ID:
            backup_database(call)
            
        elif call.data == "maintenance" and uid == Config.ADMIN_ID:
            toggle_maintenance(call)
            
        elif call.data == "back_main":
            bot.edit_message_text("🏠 **Main Menu**", chat_id, mid, reply_markup=main_menu(uid))
            
        elif call.data == "settings":
            show_settings(call)
            
        elif call.data.startswith("page_"):
            page_num = int(call.data.split("_")[1])
            view_database_page(call, page_num)
            
    except Exception as e:
        print(f"Callback error: {e}")
        bot.answer_callback_query(call.id, "⚠️ Error occurred!")

# Prime key processing
def process_key_step(message, old_mid):
    uid = message.from_user.id
    chat_id = message.chat.id
    key_input = message.text.strip().upper()
    
    bot.delete_message(chat_id, message.message_id)
    
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    res = c.execute("SELECT * FROM keys WHERE key=? AND used=0", (key_input,)).fetchone()
    
    if res:
        days, limit = res[1], res[2]
        expiry_date = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        
        c.execute("UPDATE users SET expiry=?, file_limit=?, is_prime=1 WHERE id=?", 
                 (expiry_date, limit, uid))
        c.execute("UPDATE keys SET used=1 WHERE key=?", (key_input,))
        conn.commit()
        conn.close()
        
        text = f"""
✅ **PRIME ACTIVATED!**
━━━━━━━━━━━━━━━━━━━━
🎉 You are now a Prime member!
━━━━━━━━━━━━━━━━━━━━
📅 **Expiry:** {expiry_date}
📦 **File Limit:** {limit} files
━━━━━━━━━━━━━━━━━━━━
Enjoy premium features!
"""
        
        bot.edit_message_text(text, chat_id, old_mid, reply_markup=main_menu(uid), parse_mode="Markdown")
    else:
        conn.close()
        text = """
❌ **INVALID KEY**
━━━━━━━━━━━━━━━━━━━━
Key is invalid, expired or already used.
━━━━━━━━━━━━━━━━━━━━
Contact """ + Config.ADMIN_USERNAME + """ for support.
"""
        bot.edit_message_text(text, chat_id, old_mid, reply_markup=main_menu(uid), parse_mode="Markdown")

# File upload with ZIP support
def upload_file_step(message, old_mid):
    uid = message.from_user.id
    chat_id = message.chat.id
    
    prime_status, _ = is_prime(uid)
    if not prime_status:
        bot.edit_message_text("⚠️ **Premium Required**\n\nActivate Prime to upload files.", 
                             chat_id, old_mid, reply_markup=main_menu(uid))
        return
    
    if message.content_type == 'document':
        try:
            file_name = message.document.file_name.lower()
            
            if not (file_name.endswith('.py') or file_name.endswith('.zip')):
                bot.edit_message_text("❌ **Invalid File Type!**", chat_id, old_mid)
                return
            
            bot.edit_message_text("📥 **Downloading...**", chat_id, old_mid)
            
            file_info = bot.get_file(message.document.file_id)
            downloaded = bot.download_file(file_info.file_path)
            original_name = message.document.file_name
            
            # Handle ZIP file
            if file_name.endswith('.zip'):
                temp_zip = project_path / f"temp_{uid}_{int(time.time())}.zip"
                temp_zip.write_bytes(downloaded)
                
                extract_dir = project_path / f"extract_{uid}_{int(time.time())}"
                extract_dir.mkdir(exist_ok=True)
                
                try:
                    with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
                        zip_ref.extractall(extract_dir)
                    
                    # Find Python files
                    py_files = list(extract_dir.glob('**/*.py'))
                    if not py_files:
                        bot.edit_message_text("❌ **No Python file in ZIP!**", chat_id, old_mid)
                        temp_zip.unlink(missing_ok=True)
                        shutil.rmtree(extract_dir, ignore_errors=True)
                        return
                    
                    # Use first Python file
                    py_file = py_files[0]
                    safe_name = secure_filename(py_file.name)
                    target_path = project_path / safe_name
                    shutil.copy2(py_file, target_path)
                    
                    # Cleanup
                    temp_zip.unlink(missing_ok=True)
                    shutil.rmtree(extract_dir, ignore_errors=True)
                    
                    ask_bot_name(chat_id, safe_name, original_name, uid)
                    
                except Exception as e:
                    bot.edit_message_text(f"❌ **Extraction Error:** {e}", chat_id, old_mid)
                    return
            else:
                # Regular Python file
                safe_name = secure_filename(original_name)
                file_path = project_path / safe_name
                file_path.write_bytes(downloaded)
                
                ask_bot_name(chat_id, safe_name, original_name, uid)
                
        except Exception as e:
            bot.edit_message_text(f"❌ **Error:** {str(e)}", chat_id, old_mid)
    else:
        bot.edit_message_text("❌ **Please send a file!**", chat_id, old_mid)

def ask_bot_name(chat_id, safe_name, original_name, uid):
    msg = bot.send_message(chat_id, """
🤖 **BOT NAME SETUP**
━━━━━━━━━━━━━━━━━━━━
Enter a name for your bot:
━━━━━━━━━━━━━━━━━━━━
""", parse_mode="Markdown")
    bot.register_next_step_handler(msg, save_bot_name, safe_name, original_name, uid)

def save_bot_name(message, safe_name, original_name, uid):
    chat_id = message.chat.id
    bot_name = message.text.strip()
    
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO deployments (user_id, bot_name, filename, pid, start_time, status) VALUES (?, ?, ?, ?, ?, ?)",
             (uid, bot_name, safe_name, 0, None, "Uploaded"))
    conn.commit()
    conn.close()
    
    bot.delete_message(chat_id, message.message_id)
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📚 Install Libraries", callback_data="install_libs"))
    markup.add(types.InlineKeyboardButton("🤖 My Bots", callback_data="my_bots"))
    
    text = f"""
✅ **FILE UPLOADED**
━━━━━━━━━━━━━━━━━━━━
🤖 **Name:** {bot_name}
📁 **File:** `{original_name}`
━━━━━━━━━━━━━━━━━━━━
"""
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

def show_my_bots(call):
    uid = call.from_user.id
    bots = get_user_bots(uid)
    
    if not bots:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📤 Upload Bot", callback_data="upload"))
        markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="back_main"))
        
        bot.edit_message_text("🤖 **No bots found.**", call.message.chat.id, 
                            call.message.message_id, reply_markup=markup)
        return
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    for bot_id, bot_name, filename, pid, start_time, status in bots:
        status_icon = "🟢" if status == "Running" else "🔴"
        markup.add(types.InlineKeyboardButton(f"{status_icon} {bot_name}", callback_data=f"bot_{bot_id}"))
    
    markup.add(types.InlineKeyboardButton("📤 Upload New", callback_data="upload"))
    markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="back_main"))
    
    bot.edit_message_text("🤖 **MY BOTS**\nSelect a bot:", call.message.chat.id, 
                        call.message.message_id, reply_markup=markup)

def show_bot_details(call, bot_id):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    bot_info = c.execute("SELECT * FROM deployments WHERE id=?", (bot_id,)).fetchone()
    conn.close()
    
    if not bot_info:
        return
    
    bot_name = bot_info[2]
    filename = bot_info[3]
    pid = bot_info[4]
    start_time = bot_info[5]
    status = bot_info[6]
    
    is_running = get_process_stats(pid) if pid else False
    
    stats = get_system_stats()
    cpu_bar = create_progress_bar(stats['cpu_percent'])
    ram_bar = create_progress_bar(stats['ram_percent'])
    
    text = f"""
🤖 **BOT DETAILS**
━━━━━━━━━━━━━━━━━━━━
**Name:** {bot_name}
**File:** `{filename}`
**Status:** {'🟢 Running' if is_running else '🔴 Stopped'}
**Started:** {start_time if start_time else 'Not started'}
━━━━━━━━━━━━━━━━━━━━
📊 **System Stats:**
• CPU: {cpu_bar} {stats['cpu_percent']}%
• RAM: {ram_bar} {stats['ram_percent']}%
━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = bot_actions_menu(bot_id, is_running)
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, 
                         reply_markup=markup, parse_mode="Markdown")

def start_bot_process(call, bot_id):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    bot_info = c.execute("SELECT filename FROM deployments WHERE id=?", (bot_id,)).fetchone()
    
    if bot_info:
        filename = bot_info[0]
        file_path = project_path / filename
        
        try:
            proc = subprocess.Popen(['python', str(file_path)], 
                                   stdout=subprocess.PIPE, 
                                   stderr=subprocess.PIPE,
                                   preexec_fn=os.setsid)
            
            start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            c.execute("UPDATE deployments SET pid=?, start_time=?, status=? WHERE id=?", 
                     (proc.pid, start_time, "Running", bot_id))
            conn.commit()
            
            bot.answer_callback_query(call.id, "✅ Bot started!")
            show_bot_details(call, bot_id)
            
        except Exception as e:
            bot.answer_callback_query(call.id, f"❌ Error: {str(e)}")
    
    conn.close()

def stop_bot(call, bot_id):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    
    bot_info = c.execute("SELECT pid FROM deployments WHERE id=?", (bot_id,)).fetchone()
    if bot_info and bot_info[0]:
        try:
            os.killpg(os.getpgid(bot_info[0]), signal.SIGTERM)
        except:
            pass
    
    c.execute("UPDATE deployments SET status='Stopped', pid=0 WHERE id=?", (bot_id,))
    conn.commit()
    conn.close()
    
    bot.answer_callback_query(call.id, "✅ Bot stopped!")
    show_my_bots(call)

def start_delete_process(call, bot_id):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    bot_info = c.execute("SELECT bot_name FROM deployments WHERE id=?", (bot_id,)).fetchone()
    conn.close()
    
    if not bot_info:
        return
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Confirm Delete", callback_data=f"confirm_delete_{bot_id}"),
        types.InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_delete_{bot_id}")
    )
    
    text = f"""
⚠️ **DELETE CONFIRMATION**
━━━━━━━━━━━━━━━━━━━━
Delete bot: **{bot_info[0]}**
This action cannot be undone!
━━━━━━━━━━━━━━━━━━━━
"""
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=markup, parse_mode="Markdown")

def confirm_delete_bot(call, bot_id):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    
    # Stop bot if running
    bot_info = c.execute("SELECT pid, filename FROM deployments WHERE id=?", (bot_id,)).fetchone()
    if bot_info:
        pid, filename = bot_info
        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except:
                pass
        
        # Delete file
        file_path = project_path / filename
        if file_path.exists():
            file_path.unlink()
        
        # Delete from database
        c.execute("DELETE FROM deployments WHERE id=?", (bot_id,))
        conn.commit()
    
    conn.close()
    
    bot.answer_callback_query(call.id, "✅ Bot deleted!")
    show_my_bots(call)

def cancel_delete_bot(call, bot_id):
    bot.answer_callback_query(call.id, "❌ Delete cancelled.")
    show_bot_details(call, bot_id)

def export_bot(call, bot_id):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    bot_info = c.execute("SELECT bot_name, filename FROM deployments WHERE id=?", (bot_id,)).fetchone()
    conn.close()
    
    if not bot_info:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    
    bot_name, filename = bot_info
    
    # Create export directory
    export_dir = Path('exports')
    export_dir.mkdir(exist_ok=True)
    
    # Create ZIP file
    zip_filename = f"{bot_name}_{int(time.time())}.zip"
    zip_path = export_dir / zip_filename
    
    try:
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            # Add bot file
            bot_file = project_path / filename
            if bot_file.exists():
                zipf.write(bot_file, arcname=filename)
            
            # Add metadata
            metadata = {
                'bot_name': bot_name,
                'filename': filename,
                'export_date': datetime.now().isoformat()
            }
            zipf.writestr('metadata.json', json.dumps(metadata, indent=2))
        
        # Send file
        with open(zip_path, 'rb') as f:
            bot.send_document(call.message.chat.id, f, caption=f"📦 {bot_name}")
        
        # Cleanup
        time.sleep(1)
        zip_path.unlink(missing_ok=True)
        
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Export failed: {str(e)}")

def show_dashboard(call):
    uid = call.from_user.id
    user = get_user(uid)
    bots = get_user_bots(uid)
    
    prime_status, expiry_date = is_prime(uid)
    
    running_bots = sum(1 for b in bots if b[5] == "Running")
    total_bots = len(bots)
    
    stats = get_system_stats()
    
    text = f"""
📊 **USER DASHBOARD**
━━━━━━━━━━━━━━━━━━━━
👤 **Account:**
• Status: {'PRIME 👑' if prime_status else 'FREE 🆓'}
• Expiry: {expiry_date.strftime('%Y-%m-%d') if expiry_date else 'N/A'}
• Files: {user[3] if user else 0}
━━━━━━━━━━━━━━━━━━━━
🤖 **Bots:**
• Total: {total_bots}
• Running: {running_bots}
• Stopped: {total_bots - running_bots}
━━━━━━━━━━━━━━━━━━━━
🖥️ **System:**
• CPU: {create_progress_bar(stats['cpu_percent'])} {stats['cpu_percent']}%
• RAM: {create_progress_bar(stats['ram_percent'])} {stats['ram_percent']}%
━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🤖 My Bots", callback_data="my_bots"),
        types.InlineKeyboardButton("📤 Upload", callback_data="upload")
    )
    if not prime_status:
        markup.add(types.InlineKeyboardButton("💎 Get Prime", url=f"https://t.me/{Config.ADMIN_USERNAME.replace('@', '')}"))
    markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="back_main"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=markup, parse_mode="Markdown")

# Admin functions
def admin_panel_callback(call):
    bot.edit_message_text("👑 **ADMIN PANEL**", call.message.chat.id, 
                         call.message.message_id, reply_markup=admin_menu())

def gen_key_step1(call):
    msg = bot.edit_message_text("Enter duration (days):", call.message.chat.id, call.message.message_id)
    bot.register_next_step_handler(msg, gen_key_step2)

def gen_key_step2(message):
    try:
        days = int(message.text)
        msg = bot.send_message(message.chat.id, "Enter file limit:")
        bot.register_next_step_handler(msg, gen_key_step3, days)
    except:
        bot.send_message(message.chat.id, "❌ Invalid number!")

def gen_key_step3(message, days):
    try:
        limit = int(message.text)
        key = f"PRIME-{random.randint(100000, 999999)}"
        
        conn = sqlite3.connect(Config.DB_NAME)
        c = conn.cursor()
        c.execute("INSERT INTO keys (key, duration_days, file_limit, created_date) VALUES (?, ?, ?, ?)",
                 (key, days, limit, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        conn.close()
        
        bot.send_message(message.chat.id, f"✅ Key generated:\n`{key}`\n\nDays: {days}\nLimit: {limit}", parse_mode="Markdown")
    except:
        bot.send_message(message.chat.id, "❌ Invalid input!")

def view_database_page(call, page_num):
    items_per_page = 5
    offset = (page_num - 1) * items_per_page
    
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    
    # Get total count
    total_bots = c.execute("SELECT COUNT(*) FROM deployments").fetchone()[0]
    total_pages = (total_bots + items_per_page - 1) // items_per_page
    
    # Get bots with user info
    bots = c.execute("""
        SELECT d.id, d.bot_name, d.status, u.username 
        FROM deployments d 
        LEFT JOIN users u ON d.user_id = u.id 
        LIMIT ? OFFSET ?
    """, (items_per_page, offset)).fetchall()
    
    conn.close()
    
    text = f"""
🗄️ **DATABASE**
━━━━━━━━━━━━━━━━━━━━
Total Bots: {total_bots}
Page: {page_num}/{total_pages}
━━━━━━━━━━━━━━━━━━━━
"""
    
    for bot_id, bot_name, status, username in bots:
        text += f"\n• {bot_name} (@{username or 'Unknown'}) - {status}"
    
    markup = types.InlineKeyboardMarkup()
    row = []
    
    if page_num > 1:
        row.append(types.InlineKeyboardButton("⬅️", callback_data=f"page_{page_num-1}"))
    
    row.append(types.InlineKeyboardButton(f"{page_num}/{total_pages}", callback_data="none"))
    
    if page_num < total_pages:
        row.append(types.InlineKeyboardButton("➡️", callback_data=f"page_{page_num+1}"))
    
    if row:
        markup.row(*row)
    
    markup.add(types.InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=markup, parse_mode="Markdown")

def backup_database(call):
    try:
        backup_path = f"backup_{int(time.time())}.db"
        shutil.copy2(Config.DB_NAME, backup_path)
        
        with open(backup_path, 'rb') as f:
            bot.send_document(call.message.chat.id, f, caption="💾 Database Backup")
        
        os.remove(backup_path)
        
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Backup failed: {str(e)}")

def toggle_maintenance(call):
    Config.MAINTENANCE = not Config.MAINTENANCE
    status = "ENABLED" if Config.MAINTENANCE else "DISABLED"
    bot.answer_callback_query(call.id, f"✅ Maintenance {status}")

def show_settings(call):
    uid = call.from_user.id
    prime_status, expiry_date = is_prime(uid)
    
    text = f"""
⚙️ **SETTINGS**
━━━━━━━━━━━━━━━━━━━━
• User ID: `{uid}`
• Status: {'Prime 👑' if prime_status else 'Free 🆓'}
• Expiry: {expiry_date.strftime('%Y-%m-%d') if expiry_date else 'N/A'}
━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = types.InlineKeyboardMarkup()
    if not prime_status:
        markup.add(types.InlineKeyboardButton("💎 Get Prime", url=f"https://t.me/{Config.ADMIN_USERNAME.replace('@', '')}"))
    markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="back_main"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=markup, parse_mode="Markdown")

# Flask Routes
@app.route('/')
def home():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>🤖 ZEN X HOST BOT</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                margin: 0;
                padding: 20px;
                min-height: 100vh;
            }
            .container {
                max-width: 800px;
                margin: 0 auto;
                background: rgba(255, 255, 255, 0.1);
                padding: 30px;
                border-radius: 15px;
                backdrop-filter: blur(10px);
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
            }
            h1 {
                text-align: center;
                font-size: 2.5em;
                margin-bottom: 30px;
                color: #fff;
            }
            .status {
                background: rgba(255, 255, 255, 0.2);
                padding: 20px;
                border-radius: 10px;
                margin: 20px 0;
                border-left: 5px solid #4CAF50;
            }
            .btn {
                display: inline-block;
                background: linear-gradient(45deg, #FF416C, #FF4B2B);
                color: white;
                padding: 12px 30px;
                border-radius: 25px;
                text-decoration: none;
                font-weight: bold;
                margin: 10px 5px;
                transition: transform 0.3s;
            }
            .btn:hover {
                transform: translateY(-3px);
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 ZEN X HOST BOT v3.0.1</h1>
            <div class="status">
                <h2>✅ Server Status: ONLINE</h2>
                <p>Bot hosting service is running</p>
            </div>
            <div style="text-align: center;">
                <a href="https://t.me/zen_xbot" class="btn" target="_blank">Start on Telegram</a>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

@app.route('/health')
def health():
    return {"status": "healthy", "service": "ZEN X HOST BOT"}

# Improved bot polling with single instance control
def start_bot_polling():
    print("🤖 Starting bot polling...")
    
    # Add skip_pending parameter to avoid conflicts
    bot.polling(none_stop=True, interval=0, timeout=20, skip_pending=True)

# Create necessary directories
Path('exports').mkdir(exist_ok=True)
Path('backups').mkdir(exist_ok=True)

# Start bot in background thread
bot_thread = threading.Thread(target=start_bot_polling, daemon=True)
bot_thread.start()

if __name__ == '__main__':
    print(f"""
🤖 ZEN X HOST BOT v3.0.1
━━━━━━━━━━━━━━━━━━━━
🚀 Starting server...
• Port: {Config.PORT}
• Admin: {Config.ADMIN_USERNAME}
• Database: Ready
• Bot: Starting...
━━━━━━━━━━━━━━━━━━━━
""")
    
    # Start Flask app
    app.run(host='0.0.0.0', port=Config.PORT, debug=False, use_reloader=False)
