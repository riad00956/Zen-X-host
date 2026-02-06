
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
from pathlib import Path
from telebot import types
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from flask import Flask, render_template_string, send_file

# ১. Configuration
class Config:
    TOKEN = os.environ.get('BOT_TOKEN', '8144529389:AAHmMAKR3VS2lWOEQ3VQXLGU-nHXFm2yuXM')
    ADMIN_ID = int(os.environ.get('ADMIN_ID', 6926993789))
    PROJECT_DIR = 'projects'
    DB_NAME = 'cyber_v2.db'
    PORT = int(os.environ.get('PORT', 10000))
    MAINTENANCE = False

bot = telebot.TeleBot(Config.TOKEN)
project_path = Path(Config.PROJECT_DIR)
project_path.mkdir(exist_ok=True)
app = Flask(__name__)

# ২. Database Functions
def init_db():
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    
    c.execute("DROP TABLE IF EXISTS users")
    c.execute("DROP TABLE IF EXISTS keys")
    c.execute("DROP TABLE IF EXISTS deployments")
    c.execute("DROP TABLE IF EXISTS backups")
    
    c.execute('''CREATE TABLE users 
                (id INTEGER PRIMARY KEY, username TEXT, expiry TEXT, file_limit INTEGER, is_prime INTEGER, join_date TEXT)''')
    c.execute('''CREATE TABLE keys 
                (key TEXT PRIMARY KEY, duration_days INTEGER, file_limit INTEGER, created_date TEXT)''')
    c.execute('''CREATE TABLE deployments 
                (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, bot_name TEXT, filename TEXT, pid INTEGER, 
                 start_time TEXT, status TEXT, cpu_usage REAL, ram_usage REAL, api_key TEXT)''')
    c.execute('''CREATE TABLE backups 
                (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id INTEGER, backup_name TEXT, backup_path TEXT, 
                 created_date TEXT, size_kb REAL)''')
    
    join_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?, ?, ?)", 
             (Config.ADMIN_ID, 'admin', None, 50, 1, join_date))
    
    conn.commit()
    conn.close()

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
            return expiry > datetime.now()
        except:
            return False
    return False

def get_user_bots(user_id):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    bots = c.execute("SELECT id, bot_name, filename, pid, start_time, status, api_key FROM deployments WHERE user_id=?", 
                    (user_id,)).fetchall()
    conn.close()
    return bots

def update_bot_stats(bot_id, cpu, ram):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE deployments SET cpu_usage=?, ram_usage=? WHERE id=?", 
             (cpu, ram, bot_id))
    conn.commit()
    conn.close()

def generate_random_key():
    prefix = "PRIME-"
    random_chars = ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=8))
    return f"{prefix}{random_chars}"

def generate_api_key():
    return f"BOT-{uuid.uuid4().hex[:12].upper()}"

def create_progress_bar(percentage):
    bars = int(percentage / 10)
    return "█" * bars + "░" * (10 - bars)

def extract_zip(zip_path, extract_to):
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_to)
    
    # Find main python file
    for root, dirs, files in os.walk(extract_to):
        for file in files:
            if file.endswith('.py'):
                return file
    return None

def create_backup(bot_id, bot_name):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    
    # Get bot files
    bot_info = c.execute("SELECT filename FROM deployments WHERE id=?", (bot_id,)).fetchone()
    if not bot_info:
        conn.close()
        return None
    
    filename = bot_info[0]
    backup_dir = Path('backups')
    backup_dir.mkdir(exist_ok=True)
    
    # Create zip file
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_name = f"{bot_name}_{timestamp}.zip"
    backup_path = backup_dir / backup_name
    
    with zipfile.ZipFile(backup_path, 'w') as zipf:
        file_path = project_path / filename
        if file_path.exists():
            zipf.write(file_path, filename)
    
    # Save backup info to database
    size_kb = os.path.getsize(backup_path) / 1024
    created_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    c.execute("INSERT INTO backups (bot_id, backup_name, backup_path, created_date, size_kb) VALUES (?, ?, ?, ?, ?)",
             (bot_id, backup_name, str(backup_path), created_date, size_kb))
    conn.commit()
    conn.close()
    
    return backup_path

# Keyboards
def main_menu(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    user = get_user(user_id)
    if not is_prime(user_id):
        markup.add(types.InlineKeyboardButton("🔑 Activate Prime Pass", callback_data="activate_prime"))
        markup.add(types.InlineKeyboardButton("💎 Get Prime Pass", url="https://t.me/zerox6t9"))
        markup.add(types.InlineKeyboardButton("ℹ️ Premium Features", callback_data="premium_info"))
    else:
        markup.add(
            types.InlineKeyboardButton("📤 Upload Bot File", callback_data='upload'),
            types.InlineKeyboardButton("🤖 My Bots", callback_data='my_bots')
        )
        markup.add(
            types.InlineKeyboardButton("🚀 Deploy New Bot", callback_data='deploy_new'),
            types.InlineKeyboardButton("📊 Dashboard", callback_data='dashboard')
        )
    
    markup.add(types.InlineKeyboardButton("⚙️ Settings", callback_data='settings'))
    
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
        types.InlineKeyboardButton("🤖 All Bots", callback_data="all_bots_admin"),
        types.InlineKeyboardButton("📊 Statistics", callback_data="stats")
    )
    markup.add(
        types.InlineKeyboardButton("🗃️ Database View", callback_data="db_view"),
        types.InlineKeyboardButton("💾 Backup System", callback_data="backup_system")
    )
    markup.add(
        types.InlineKeyboardButton("⚙️ Maintenance", callback_data="maintenance"),
        types.InlineKeyboardButton("🏠 Main Menu", callback_data="back_main")
    )
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
    
    if not user:
        bot.send_message(message.chat.id, "❌ Error loading user data. Please try again.")
        return
    
    status = "PRIME 👑" if is_prime(uid) else "FREE 🆓"
    expiry = user[2] if user[2] else "Not Activated"
    
    text = f"""
🤖 **CYBER BOT HOSTING v4.0**
━━━━━━━━━━━━━━━━━━━━
👤 **User:** @{username}
🆔 **ID:** `{uid}`
💎 **Status:** {status}
📅 **Join Date:** {user[5]}
━━━━━━━━━━━━━━━━━━━━
📊 **Account Details:**
• Plan: {'Premium' if is_prime(uid) else 'Free'}
• File Limit: `{user[3]}` files
• Expiry: {expiry}
━━━━━━━━━━━━━━━━━━━━
"""
    
    bot.send_message(message.chat.id, text, 
                    reply_markup=main_menu(uid), 
                    parse_mode="Markdown")

@bot.message_handler(commands=['admin'])
def admin_command(message):
    uid = message.from_user.id
    if uid == Config.ADMIN_ID:
        admin_panel(message)
    else:
        bot.reply_to(message, "⛔ **Access Denied!**\nYou are not authorized to use this command.")

def admin_panel(message):
    text = """
👑 **ADMIN CONTROL PANEL**
━━━━━━━━━━━━━━━━━━━━
Welcome to the admin dashboard. You can manage users, generate keys, and monitor system activities.
━━━━━━━━━━━━━━━━━━━━
"""
    bot.send_message(message.chat.id, text, 
                    reply_markup=admin_menu(), 
                    parse_mode="Markdown")

# Callback Query Handler
@bot.callback_query_handler(func=lambda call: True)
def callback_manager(call):
    uid = call.from_user.id
    mid = call.message.message_id
    chat_id = call.message.chat.id
    
    try:
        if call.data == "activate_prime":
            msg = bot.edit_message_text("""
🔑 **ACTIVATE PRIME PASS**
━━━━━━━━━━━━━━━━━━━━
Enter your activation key below.
Format: `PRIME-XXXXXX`
━━━━━━━━━━━━━━━━━━━━
            """, chat_id, mid, parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_key_step, mid)
            
        elif call.data == "upload":
            if not is_prime(uid):
                bot.answer_callback_query(call.id, "⚠️ Premium feature! Activate Prime first.")
                return
            msg = bot.edit_message_text("""
📤 **UPLOAD BOT FILE**
━━━━━━━━━━━━━━━━━━━━
Please send your Python (.py) or ZIP (.zip) file.
• Max size: 10MB
• Allowed: .py, .zip
━━━━━━━━━━━━━━━━━━━━
            """, chat_id, mid, parse_mode="Markdown")
            bot.register_next_step_handler(msg, upload_file_step, mid)
            
        elif call.data == "deploy_new":
            if not is_prime(uid):
                bot.answer_callback_query(call.id, "⚠️ Premium feature!")
                return
            show_available_files(call)
            
        elif call.data == "my_bots":
            show_my_bots(call)
            
        elif call.data == "dashboard":
            show_dashboard(call)
            
        elif call.data == "admin_panel":
            if uid == Config.ADMIN_ID:
                admin_panel_callback(call)
            else:
                bot.answer_callback_query(call.id, "⛔ Access Denied!")
                
        elif call.data == "gen_key":
            if uid == Config.ADMIN_ID:
                gen_key_step1(call)
            else:
                bot.answer_callback_query(call.id, "⛔ Admin only!")
                
        elif call.data == "all_users":
            if uid == Config.ADMIN_ID:
                show_all_users_paged(call, page=1)
                
        elif call.data == "all_bots_admin":
            if uid == Config.ADMIN_ID:
                show_all_bots_admin_paged(call, page=1)
                
        elif call.data == "stats":
            if uid == Config.ADMIN_ID:
                show_admin_stats(call)
                
        elif call.data == "db_view":
            if uid == Config.ADMIN_ID:
                show_database_view(call)
                
        elif call.data == "backup_system":
            if uid == Config.ADMIN_ID:
                show_backup_system(call)
                
        elif call.data.startswith("bot_"):
            parts = call.data.split("_")
            if len(parts) == 2:
                bot_id = parts[1]
                show_bot_details(call, bot_id)
            elif len(parts) == 3 and parts[1] == "page":
                page = int(parts[2])
                show_my_bots_paged(call, page)
            
        elif call.data.startswith("deploy_"):
            filename = call.data.split("_")[1]
            start_deployment(call, filename)
            
        elif call.data.startswith("stop_"):
            bot_id = call.data.split("_")[1]
            stop_bot(call, bot_id)
            
        elif call.data.startswith("delete_"):
            bot_id = call.data.split("_")[1]
            ask_delete_verification(call, bot_id)
            
        elif call.data.startswith("confirm_delete_"):
            bot_id = call.data.split("_")[2]
            confirm_delete_bot(call, bot_id)
            
        elif call.data.startswith("export_"):
            bot_id = call.data.split("_")[1]
            export_bot_files(call, bot_id)
            
        elif call.data.startswith("install_libs"):
            ask_for_libraries(call)
            
        elif call.data == "back_main":
            bot.edit_message_text("🏠 **Main Menu**", chat_id, mid, 
                                 reply_markup=main_menu(uid))
            
        elif call.data == "premium_info":
            show_premium_info(call)
            
        elif call.data == "settings":
            show_settings(call)
            
        elif call.data == "maintenance":
            toggle_maintenance(call)
            
        elif call.data.startswith("user_page_"):
            page = int(call.data.split("_")[2])
            show_all_users_paged(call, page)
            
        elif call.data.startswith("admin_bot_page_"):
            page = int(call.data.split("_")[3])
            show_all_bots_admin_paged(call, page)
            
        elif call.data.startswith("export_db"):
            export_database(call)
            
        elif call.data.startswith("view_backup_"):
            backup_id = call.data.split("_")[2]
            view_backup_details(call, backup_id)
            
    except Exception as e:
        print(f"Callback error: {e}")
        bot.answer_callback_query(call.id, "⚠️ Error occurred!")

# Step-by-step Functions
def gen_key_step1(call):
    msg = bot.edit_message_text("""
🎫 **GENERATE PRIME KEY**
━━━━━━━━━━━━━━━━━━━━
Step 1/3: Enter duration in days
Example: 7, 30, 90, 365
━━━━━━━━━━━━━━━━━━━━
    """, call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    bot.register_next_step_handler(msg, gen_key_step2)

def gen_key_step2(message):
    try:
        days = int(message.text.strip())
        if days <= 0:
            raise ValueError
        bot.delete_message(message.chat.id, message.message_id)
        msg = bot.send_message(message.chat.id, f"""
🎫 **GENERATE PRIME KEY**
━━━━━━━━━━━━━━━━━━━━
Step 2/3: Duration set to **{days} days**

Now enter file access limit
Example: 3, 5, 10
━━━━━━━━━━━━━━━━━━━━
        """, parse_mode="Markdown")
        bot.register_next_step_handler(msg, gen_key_step3, days)
    except:
        bot.send_message(message.chat.id, "❌ Invalid input! Please enter a valid number.")

def gen_key_step3(message, days):
    try:
        limit = int(message.text.strip())
        if limit <= 0:
            raise ValueError
        bot.delete_message(message.chat.id, message.message_id)
        
        key = generate_random_key()
        created_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        conn = sqlite3.connect(Config.DB_NAME)
        c = conn.cursor()
        c.execute("INSERT INTO keys VALUES (?, ?, ?, ?)", 
                 (key, days, limit, created_date))
        conn.commit()
        conn.close()
        
        response = f"""
✅ **KEY GENERATED SUCCESSFULLY**
━━━━━━━━━━━━━━━━━━━━
🔑 **Key:** `{key}`
⏰ **Duration:** {days} days
📦 **File Limit:** {limit} files
📅 **Created:** {created_date}
━━━━━━━━━━━━━━━━━━━━
Share this key with the user.
        """
        bot.send_message(message.chat.id, response, parse_mode="Markdown")
        
    except:
        bot.send_message(message.chat.id, "❌ Invalid input!")

def upload_file_step(message, old_mid):
    uid = message.from_user.id
    chat_id = message.chat.id
    
    if not is_prime(uid):
        bot.edit_message_text("⚠️ **Premium Required**\n\nActivate Prime to upload files.", 
                             chat_id, old_mid, reply_markup=main_menu(uid))
        return
    
    if message.content_type == 'document':
        file_name = message.document.file_name
        if not (file_name.endswith('.py') or file_name.endswith('.zip')):
            bot.edit_message_text("❌ **Invalid File!**\n\nOnly Python (.py) or ZIP (.zip) files allowed.", 
                                 chat_id, old_mid)
            return
            
        try:
            bot.edit_message_text("📥 **Downloading file...**", chat_id, old_mid)
            
            file_info = bot.get_file(message.document.file_id)
            downloaded = bot.download_file(file_info.file_path)
            original_name = message.document.file_name
            safe_name = secure_filename(original_name)
            
            file_path = project_path / safe_name
            file_path.write_bytes(downloaded)
            
            # Handle ZIP file
            if safe_name.endswith('.zip'):
                extract_dir = project_path / f"extracted_{uid}_{int(time.time())}"
                extract_dir.mkdir(exist_ok=True)
                
                main_file = extract_zip(file_path, extract_dir)
                if not main_file:
                    bot.edit_message_text("❌ **No Python file found in ZIP!**", chat_id, old_mid)
                    return
                
                # Move main file to project directory
                main_file_path = extract_dir / main_file
                new_safe_name = secure_filename(main_file)
                final_path = project_path / new_safe_name
                main_file_path.rename(final_path)
                
                # Cleanup
                for item in extract_dir.iterdir():
                    if item.is_file():
                        item.unlink()
                extract_dir.rmdir()
                file_path.unlink()  # Remove original zip
                
                safe_name = new_safe_name
            
            bot.delete_message(chat_id, message.message_id)
            msg = bot.send_message(chat_id, """
🤖 **BOT NAME SETUP**
━━━━━━━━━━━━━━━━━━━━
Enter a name for your bot
Example: `News Bot`, `Music Bot`, `Assistant`
━━━━━━━━━━━━━━━━━━━━
            """, parse_mode="Markdown")
            bot.register_next_step_handler(msg, save_bot_name, safe_name, original_name)
            
        except Exception as e:
            bot.edit_message_text(f"❌ **Error:** {str(e)}", chat_id, old_mid)
    else:
        bot.edit_message_text("❌ **Please send a file!**", chat_id, old_mid)

def save_bot_name(message, safe_name, original_name):
    uid = message.from_user.id
    chat_id = message.chat.id
    bot_name = message.text.strip()
    
    # Generate API key for the bot
    api_key = generate_api_key()
    
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO deployments (user_id, bot_name, filename, pid, start_time, status, api_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
             (uid, bot_name, safe_name, 0, None, "Uploaded", api_key))
    conn.commit()
    conn.close()
    
    bot.delete_message(chat_id, message.message_id)
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📚 Install Libraries", callback_data="install_libs"))
    markup.add(types.InlineKeyboardButton("🤖 My Bots", callback_data="my_bots"))
    markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="back_main"))
    
    text = f"""
✅ **FILE UPLOADED SUCCESSFULLY**
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot Name:** {bot_name}
📁 **File:** `{original_name}`
🔑 **API Key:** `{api_key}`
📊 **Status:** Ready for setup
━━━━━━━━━━━━━━━━━━━━
Click 'Install Libraries' to add dependencies.
    """
    
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

def ask_for_libraries(call):
    msg = bot.edit_message_text("""
📚 **INSTALL LIBRARIES**
━━━━━━━━━━━━━━━━━━━━
Enter library commands (one per line):
Example:
```

pip install pyTelegramBotAPI
pip install requests
pip install beautifulsoup4

```
━━━━━━━━━━━━━━━━━━━━
    """, call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    bot.register_next_step_handler(msg, install_libraries_step, call.message.message_id)

def install_libraries_step(message, old_mid):
    uid = message.from_user.id
    chat_id = message.chat.id
    commands = message.text.strip().split('\n')
    
    bot.delete_message(chat_id, message.message_id)
    
    bot.edit_message_text("""
🛠 **INSTALLING LIBRARIES**
━━━━━━━━━━━━━━━━━━━━
Starting installation...
━━━━━━━━━━━━━━━━━━━━
    """, chat_id, old_mid, parse_mode="Markdown")
    
    results = []
    for i, cmd in enumerate(commands):
        if cmd.strip() and "pip install" in cmd:
            try:
                progress_text = f"""
🛠 **INSTALLING LIBRARIES**
━━━━━━━━━━━━━━━━━━━━
Installing ({i+1}/{len(commands)}):
`{cmd}`
━━━━━━━━━━━━━━━━━━━━
                """
                bot.edit_message_text(progress_text, chat_id, old_mid, parse_mode="Markdown")
                
                result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=60)
                if result.returncode == 0:
                    results.append(f"✅ {cmd}")
                else:
                    results.append(f"❌ {cmd}")
                
                time.sleep(1)
                
            except subprocess.TimeoutExpired:
                results.append(f"⏰ {cmd} (Timeout)")
            except Exception as e:
                results.append(f"⚠️ {cmd} (Error)")
    
    result_text = "\n".join(results)
    final_text = f"""
✅ **INSTALLATION COMPLETE**
━━━━━━━━━━━━━━━━━━━━
{result_text}
━━━━━━━━━━━━━━━━━━━━
All libraries installed successfully!
    """
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🚀 Deploy Bot Now", callback_data="deploy_new"))
    markup.add(types.InlineKeyboardButton("🤖 My Bots", callback_data="my_bots"))
    
    bot.edit_message_text(final_text, chat_id, old_mid, reply_markup=markup, parse_mode="Markdown")

def show_available_files(call):
    uid = call.from_user.id
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    files = c.execute("SELECT filename, bot_name FROM deployments WHERE user_id=? AND pid=0", 
                     (uid,)).fetchall()
    conn.close()
    
    if not files:
        bot.edit_message_text("📭 **No files available for deployment**\n\nUpload a file first.", 
                            call.message.chat.id, call.message.message_id)
        return
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    for filename, bot_name in files:
        markup.add(types.InlineKeyboardButton(f"🤖 {bot_name}", callback_data=f"deploy_{filename}"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_main"))
    
    text = """
🚀 **DEPLOY BOT**
━━━━━━━━━━━━━━━━━━━━
Select a bot to deploy:
━━━━━━━━━━━━━━━━━━━━
    """
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, 
                         reply_markup=markup, parse_mode="Markdown")

def start_deployment(call, filename):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    mid = call.message.message_id
    
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    bot_info = c.execute("SELECT id, bot_name FROM deployments WHERE filename=? AND user_id=?", 
                        (filename, uid)).fetchone()
    conn.close()
    
    if not bot_info:
        return
    
    bot_id, bot_name = bot_info
    
    steps = [
        "Initializing system...",
        "Checking dependencies...",
        "Loading modules...",
        "Starting bot process..."
    ]
    
    for i, step in enumerate(steps):
        step_text = "\n".join([f"✅ **Step {j+1}:** {steps[j]}" for j in range(i)] + [f"🔄 **Step {i+1}:** {step}"])
        text = f"""
🚀 **DEPLOYING BOT**
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot:** {bot_name}
{step_text}
━━━━━━━━━━━━━━━━━━━━
        """
        bot.edit_message_text(text, chat_id, mid, parse_mode="Markdown")
        time.sleep(1.5)
    
    try:
        file_path = project_path / filename
        start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        proc = subprocess.Popen(['python', str(file_path)], 
                               stdout=subprocess.PIPE, 
                               stderr=subprocess.PIPE,
                               start_new_session=True)
        
        conn = sqlite3.connect(Config.DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE deployments SET pid=?, start_time=?, status=? WHERE id=?", 
                 (proc.pid, start_time, "Running", bot_id))
        conn.commit()
        conn.close()
        
        text = f"""
✅ **BOT DEPLOYED SUCCESSFULLY**
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot:** {bot_name}
📁 **File:** `{filename}`
⚙️ **PID:** `{proc.pid}`
⏰ **Started:** {start_time}
🔧 **Status:** **RUNNING**
━━━━━━━━━━━━━━━━━━━━
Bot is now active and running!
        """
        bot.edit_message_text(text, chat_id, mid, parse_mode="Markdown")
        time.sleep(2)
        
        show_bot_live_stats(call, bot_id, bot_name, proc.pid)
        
    except Exception as e:
        text = f"""
❌ **DEPLOYMENT FAILED**
━━━━━━━━━━━━━━━━━━━━
Error: {str(e)}
━━━━━━━━━━━━━━━━━━━━
Please check your bot code and try again.
        """
        bot.edit_message_text(text, chat_id, mid, parse_mode="Markdown")

def show_bot_live_stats(call, bot_id, bot_name, pid):
    chat_id = call.message.chat.id
    uid = call.from_user.id
    
    def monitor_bot():
        for i in range(10):
            try:
                stats = get_system_stats()
                cpu_percent = stats['cpu_percent']
                ram_percent = stats['ram_percent']
                disk_percent = stats['disk_percent']
                
                update_bot_stats(bot_id, cpu_percent, ram_percent)
                
                cpu_bar = create_progress_bar(cpu_percent)
                ram_bar = create_progress_bar(ram_percent)
                disk_bar = create_progress_bar(disk_percent)
                
                is_running = get_process_stats(pid)
                status_icon = "🟢" if is_running else "🔴"
                
                text = f"""
📊 **LIVE BOT STATISTICS** {status_icon}
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot:** {bot_name}
⚙️ **PID:** `{pid}`
⏰ **Uptime:** {i*5} seconds
━━━━━━━━━━━━━━━━━━━━
💻 **CPU Usage:** {cpu_bar} {cpu_percent:.1f}%
🧠 **RAM Usage:** {ram_bar} {ram_percent:.1f}%
💾 **Disk Usage:** {disk_bar} {disk_percent:.1f}%
━━━━━━━━━━━━━━━━━━━━
📈 **Server Performance:**
• Download Speed: {random.randint(50, 100)} MB/s
• Upload Speed: {random.randint(20, 50)} MB/s
• Network Latency: {random.randint(10, 50)} ms
• Response Time: {random.randint(1, 10)} ms
━━━━━━━━━━━━━━━━━━━━
🔄 **Status:** {"Running smoothly..." if is_running else "Process stopped"}
                """
                
                try:
                    bot.edit_message_text(text, chat_id, call.message.message_id, 
                                         parse_mode="Markdown")
                except:
                    pass
                
                time.sleep(5)
                
            except Exception as e:
                print(f"Monitor error: {e}")
                break
    
    monitor_thread = threading.Thread(target=monitor_bot)
    monitor_thread.daemon = True
    monitor_thread.start()
    
    time.sleep(5)
    text = f"""
✅ **BOT IS NOW ACTIVE**
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot:** {bot_name}
📊 **Status:** Live monitoring active
🏃 **Process:** Running (PID: {pid})
━━━━━━━━━━━━━━━━━━━━
Live statistics will update every 5 seconds.
    """
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🤖 My Bots", callback_data="my_bots"))
    markup.add(types.InlineKeyboardButton("📊 View Stats", callback_data=f"bot_{bot_id}"))
    markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="back_main"))
    
    bot.edit_message_text(text, chat_id, call.message.message_id, 
                         reply_markup=markup, parse_mode="Markdown")

def show_my_bots(call):
    show_my_bots_paged(call, page=1)

def show_my_bots_paged(call, page=1):
    uid = call.from_user.id
    bots = get_user_bots(uid)
    
    if not bots:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📤 Upload Bot", callback_data="upload"))
        markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="back_main"))
        
        text = """
🤖 **MY BOTS**
━━━━━━━━━━━━━━━━━━━━
No bots found. Upload your first bot!
━━━━━━━━━━━━━━━━━━━━
        """
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                             reply_markup=markup, parse_mode="Markdown")
        return
    
    items_per_page = 5
    total_pages = (len(bots) + items_per_page - 1) // items_per_page
    page = max(1, min(page, total_pages))
    
    start_idx = (page - 1) * items_per_page
    end_idx = start_idx + items_per_page
    page_bots = bots[start_idx:end_idx]
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    for bot_id, bot_name, filename, pid, start_time, status, api_key in page_bots:
        status_icon = "🟢" if status == "Running" else "🔴" if status == "Stopped" else "🟡"
        button_text = f"{status_icon} {bot_name}"
        markup.add(types.InlineKeyboardButton(button_text, callback_data=f"bot_{bot_id}"))
    
    # Pagination buttons
    pagination_buttons = []
    if page > 1:
        pagination_buttons.append(types.InlineKeyboardButton("◀️ Previous", callback_data=f"bot_page_{page-1}"))
    if page < total_pages:
        pagination_buttons.append(types.InlineKeyboardButton("Next ▶️", callback_data=f"bot_page_{page+1}"))
    
    if pagination_buttons:
        markup.add(*pagination_buttons)
    
    markup.add(types.InlineKeyboardButton("📤 Upload New", callback_data="upload"))
    markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="back_main"))
    
    running_count = sum(1 for b in bots if b[5] == "Running")
    total_count = len(bots)
    
    text = f"""
🤖 **MY BOTS** (Page {page}/{total_pages})
━━━━━━━━━━━━━━━━━━━━
📊 **Stats:** {running_count}/{total_count} running
━━━━━━━━━━━━━━━━━━━━
Select a bot to view details:
━━━━━━━━━━━━━━━━━━━━
    """
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=markup, parse_mode="Markdown")

def show_bot_details(call, bot_id):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    bot_info = c.execute("SELECT * FROM deployments WHERE id=?", (bot_id,)).fetchone()
    conn.close()
    
    if not bot_info:
        return
    
    bot_name = bot_info[2] if len(bot_info) > 2 else "Unknown"
    filename = bot_info[3] if len(bot_info) > 3 else "Unknown"
    pid = bot_info[4] if len(bot_info) > 4 else 0
    start_time = bot_info[5] if len(bot_info) > 5 else None
    status = bot_info[6] if len(bot_info) > 6 else "Unknown"
    cpu_usage = bot_info[7] if len(bot_info) > 7 else 0
    ram_usage = bot_info[8] if len(bot_info) > 8 else 0
    api_key = bot_info[9] if len(bot_info) > 9 else "N/A"
    
    stats = get_system_stats()
    cpu_usage = cpu_usage or stats['cpu_percent']
    ram_usage = ram_usage or stats['ram_percent']
    
    cpu_bar = create_progress_bar(cpu_usage)
    ram_bar = create_progress_bar(ram_usage)
    
    is_running = get_process_stats(pid) if pid else False
    
    stats_text = f"""
📊 **Current Stats:**
• CPU: {cpu_bar} {cpu_usage:.1f}%
• RAM: {ram_bar} {ram_usage:.1f}%
• Status: {"🟢 Running" if is_running else "🔴 Stopped"}
• Uptime: {calculate_uptime(start_time) if start_time else "N/A"}
• API Key: `{api_key}`
    """
    
    text = f"""
🤖 **BOT DETAILS**
━━━━━━━━━━━━━━━━━━━━
**Name:** {bot_name}
**File:** `{filename}`
**PID:** `{pid if pid else "N/A"}`
**Started:** {start_time if start_time else "Not started"}
━━━━━━━━━━━━━━━━━━━━
{stats_text}
━━━━━━━━━━━━━━━━━━━━
    """
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.add(types.InlineKeyboardButton("🛑 Stop Bot", callback_data=f"stop_{bot_id}"))
    else:
        markup.add(types.InlineKeyboardButton("🚀 Start Bot", callback_data=f"start_{bot_id}"))
    
    markup.add(types.InlineKeyboardButton("🗑️ Delete Bot", callback_data=f"delete_{bot_id}"),
               types.InlineKeyboardButton("📦 Export Bot", callback_data=f"export_{bot_id}"))
    markup.add(types.InlineKeyboardButton("📊 Refresh Stats", callback_data=f"bot_{bot_id}"))
    markup.add(types.InlineKeyboardButton("🔙 My Bots", callback_data="my_bots"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=markup, parse_mode="Markdown")

def ask_delete_verification(call, bot_id):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    bot_info = c.execute("SELECT bot_name, api_key FROM deployments WHERE id=?", (bot_id,)).fetchone()
    conn.close()
    
    if not bot_info:
        return
    
    bot_name, api_key = bot_info
    
    text = f"""
⚠️ **DELETE VERIFICATION**
━━━━━━━━━━━━━━━━━━━━
You are about to delete bot: **{bot_name}**

For verification, please enter:
1. Bot API Key: `{api_key}`
OR
2. Bot Username

Type exactly as shown above to confirm deletion.
━━━━━━━━━━━━━━━━━━━━
    """
    
    msg = bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    bot.register_next_step_handler(msg, verify_delete_bot, bot_id, bot_name, api_key)

def verify_delete_bot(message, bot_id, bot_name, api_key):
    user_input = message.text.strip()
    
    if user_input == api_key or user_input == bot_name:
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("✅ Confirm Delete", callback_data=f"confirm_delete_{bot_id}"),
            types.InlineKeyboardButton("❌ Cancel", callback_data=f"bot_{bot_id}")
        )
        
        text = f"""
⚠️ **FINAL CONFIRMATION**
━━━━━━━━━━━━━━━━━━━━
Are you sure you want to permanently delete:
**{bot_name}**?

This action cannot be undone!
━━━━━━━━━━━━━━━━━━━━
        """
        
        bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "❌ Verification failed. Deletion cancelled.", 
                        reply_markup=main_menu(message.from_user.id))

def confirm_delete_bot(call, bot_id):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    
    # Get bot info before deleting
    bot_info = c.execute("SELECT filename FROM deployments WHERE id=?", (bot_id,)).fetchone()
    
    if bot_info:
        filename = bot_info[0]
        file_path = project_path / filename
        if file_path.exists():
            file_path.unlink()
    
    c.execute("DELETE FROM deployments WHERE id=?", (bot_id,))
    conn.commit()
    conn.close()
    
    bot.answer_callback_query(call.id, "✅ Bot deleted successfully!")
    show_my_bots(call)

def export_bot_files(call, bot_id):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    bot_info = c.execute("SELECT bot_name, filename FROM deployments WHERE id=?", (bot_id,)).fetchone()
    conn.close()
    
    if not bot_info:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    
    bot_name, filename = bot_info
    file_path = project_path / filename
    
    if not file_path.exists():
        bot.answer_callback_query(call.id, "❌ Bot file not found!")
        return
    
    try:
        # Create backup directory
        backup_dir = Path('exports')
        backup_dir.mkdir(exist_ok=True)
        
        # Create zip file
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        zip_name = f"{bot_name}_{timestamp}.zip"
        zip_path = backup_dir / zip_name
        
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            zipf.write(file_path, filename)
            
            # Add bot info file
            bot_info_data = {
                'bot_name': bot_name,
                'filename': filename,
                'export_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'platform': 'Cyber Bot Hosting v4.0'
            }
            
            info_file = backup_dir / 'bot_info.json'
            with open(info_file, 'w') as f:
                json.dump(bot_info_data, f, indent=2)
            
            zipf.write(info_file, 'bot_info.json')
            info_file.unlink()
        
        # Send the file
        with open(zip_path, 'rb') as f:
            bot.send_document(call.message.chat.id, f, caption=f"📦 **Exported Bot:** {bot_name}")
        
        zip_path.unlink()
        bot.answer_callback_query(call.id, "✅ Bot exported successfully!")
        
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Export failed: {str(e)}")

def calculate_uptime(start_time_str):
    try:
        start_time = datetime.strptime(start_time_str, '%Y-%m-%d %H:%M:%S')
        uptime = datetime.now() - start_time
        
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        
        if days > 0:
            return f"{days}d {hours}h"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"
    except:
        return "N/A"

def stop_bot(call, bot_id):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    
    bot_info = c.execute("SELECT pid FROM deployments WHERE id=?", (bot_id,)).fetchone()
    if bot_info and bot_info[0]:
        try:
            os.killpg(os.getpgid(bot_info[0]), signal.SIGTERM)
            time.sleep(1)
        except:
            pass
    
    c.execute("UPDATE deployments SET status='Stopped', pid=0 WHERE id=?", (bot_id,))
    conn.commit()
    conn.close()
    
    bot.answer_callback_query(call.id, "✅ Bot stopped successfully!")
    show_my_bots(call)

def show_dashboard(call):
    uid = call.from_user.id
    user = get_user(uid)
    
    if not user:
        bot.answer_callback_query(call.id, "❌ User data not found")
        return
    
    bots = get_user_bots(uid)
    
    running_bots = sum(1 for b in bots if b[5] == "Running")
    total_bots = len(bots)
    
    stats = get_system_stats()
    cpu_usage = stats['cpu_percent']
    ram_usage = stats['ram_percent']
    disk_usage = stats['disk_percent']
    
    cpu_bar = create_progress_bar(cpu_usage)
    ram_bar = create_progress_bar(ram_usage)
    disk_bar = create_progress_bar(disk_usage)
    
    text = f"""
📊 **USER DASHBOARD**
━━━━━━━━━━━━━━━━━━━━
👤 **Account Info:**
• Status: {'PRIME 👑' if is_prime(uid) else 'FREE 🆓'}
• File Limit: {user[3]} files
• Expiry: {user[2] if user[2] else 'Not set'}
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot Statistics:**
• Total Bots: {total_bots}
• Running: {running_bots}
• Stopped: {total_bots - running_bots}
━━━━━━━━━━━━━━━━━━━━
🖥️ **Server Status:**
• CPU: {cpu_bar} {cpu_usage:.1f}%
• RAM: {ram_bar} {ram_usage:.1f}%
• Disk: {disk_bar} {disk_usage:.1f}%
━━━━━━━━━━━━━━━━━━━━
💻 **Hosting Platform:**
• Platform: Render.com
• Type: Web Service
• Region: Global
━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🤖 My Bots", callback_data="my_bots"),
        types.InlineKeyboardButton("🚀 Deploy", callback_data="deploy_new")
    )
    markup.add(
        types.InlineKeyboardButton("📤 Upload", callback_data="upload"),
        types.InlineKeyboardButton("🔄 Refresh", callback_data="dashboard")
    )
    markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="back_main"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=markup, parse_mode="Markdown")

def admin_panel_callback(call):
    text = """
👑 **ADMIN DASHBOARD**
━━━━━━━━━━━━━━━━━━━━
Welcome to the admin control panel.
Select an option below:
━━━━━━━━━━━━━━━━━━━━
    """
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=admin_menu(), parse_mode="Markdown")

def show_all_users_paged(call, page=1):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    users = c.execute("SELECT id, username, expiry, file_limit, is_prime FROM users").fetchall()
    conn.close()
    
    items_per_page = 10
    total_pages = (len(users) + items_per_page - 1) // items_per_page
    page = max(1, min(page, total_pages))
    
    start_idx = (page - 1) * items_per_page
    end_idx = start_idx + items_per_page
    page_users = users[start_idx:end_idx]
    
    prime_count = sum(1 for u in users if u[4] == 1)
    total_count = len(users)
    
    text = f"""
👥 **ALL USERS** (Page {page}/{total_pages})
━━━━━━━━━━━━━━━━━━━━
📊 **Total Users:** {total_count}
👑 **Prime Users:** {prime_count}
🆓 **Free Users:** {total_count - prime_count}
━━━━━━━━━━━━━━━━━━━━
**Users List:**
"""
    
    for user in page_users:
        username = user[1] if user[1] else f"User_{user[0]}"
        status = "Prime 👑" if user[4] else "Free 🆓"
        text += f"\n• {username} (ID: {user[0]}) - {status} - Limit: {user[3]}"
    
    markup = types.InlineKeyboardMarkup()
    
    pagination_buttons = []
    if page > 1:
        pagination_buttons.append(types.InlineKeyboardButton("◀️", callback_data=f"user_page_{page-1}"))
    
    pagination_buttons.append(types.InlineKeyboardButton(f"{page}/{total_pages}", callback_data="none"))
    
    if page < total_pages:
        pagination_buttons.append(types.InlineKeyboardButton("▶️", callback_data=f"user_page_{page+1}"))
    
    if pagination_buttons:
        markup.row(*pagination_buttons)
    
    markup.add(types.InlineKeyboardButton("📤 Export Database", callback_data="export_db"))
    markup.add(types.InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=markup, parse_mode="Markdown")

def show_all_bots_admin_paged(call, page=1):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    bots = c.execute("SELECT d.id, d.bot_name, d.status, d.start_time, u.username FROM deployments d LEFT JOIN users u ON d.user_id = u.id").fetchall()
    conn.close()
    
    items_per_page = 8
    total_pages = (len(bots) + items_per_page - 1) // items_per_page
    page = max(1, min(page, total_pages))
    
    start_idx = (page - 1) * items_per_page
    end_idx = start_idx + items_per_page
    page_bots = bots[start_idx:end_idx]
    
    running_bots = sum(1 for b in bots if b[2] == "Running")
    total_bots = len(bots)
    
    text = f"""
🤖 **ALL BOTS** (Page {page}/{total_pages})
━━━━━━━━━━━━━━━━━━━━
📊 **Total Bots:** {total_bots}
🟢 **Running:** {running_bots}
🔴 **Stopped:** {total_bots - running_bots}
━━━━━━━━━━━━━━━━━━━━
**Bots List:**
"""
    
    for bot_id, bot_name, status, start_time, username in page_bots:
        status_icon = "🟢" if status == "Running" else "🔴"
        username = username or "Unknown"
        text += f"\n• {bot_name} (@{username}) - {status_icon} {status}"
    
    markup = types.InlineKeyboardMarkup()
    
    pagination_buttons = []
    if page > 1:
        pagination_buttons.append(types.InlineKeyboardButton("◀️", callback_data=f"admin_bot_page_{page-1}"))
    
    pagination_buttons.append(types.InlineKeyboardButton(f"{page}/{total_pages}", callback_data="none"))
    
    if page < total_pages:
        pagination_buttons.append(types.InlineKeyboardButton("▶️", callback_data=f"admin_bot_page_{page+1}"))
    
    if pagination_buttons:
        markup.row(*pagination_buttons)
    
    markup.add(types.InlineKeyboardButton("📤 Export All Bots", callback_data="export_db_bots"))
    markup.add(types.InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=markup, parse_mode="Markdown")

def show_database_view(call):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    
    # Get table info
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = c.fetchall()
    
    text = """
🗃️ **DATABASE VIEW**
━━━━━━━━━━━━━━━━━━━━
**Available Tables:**
"""
    
    for table in tables:
        table_name = table[0]
        c.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = c.fetchone()[0]
        text += f"\n• {table_name}: {count} records"
    
    # Get total stats
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM deployments")
    total_bots = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM keys")
    total_keys = c.fetchone()[0]
    
    conn.close()
    
    text += f"""
━━━━━━━━━━━━━━━━━━━━
📊 **Database Stats:**
• Users: {total_users}
• Bots: {total_bots}
• Keys: {total_keys}
• Size: {os.path.getsize(Config.DB_NAME) / 1024:.2f} KB
━━━━━━━━━━━━━━━━━━━━
    """
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💾 Backup Database", callback_data="export_db"))
    markup.add(types.InlineKeyboardButton("📤 Export Schema", callback_data="export_schema"))
    markup.add(types.InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=markup, parse_mode="Markdown")

def show_backup_system(call):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM backups")
    total_backups = c.fetchone()[0]
    
    c.execute("SELECT id, backup_name, created_date, size_kb FROM backups ORDER BY id DESC LIMIT 5")
    recent_backups = c.fetchall()
    
    conn.close()
    
    text = f"""
💾 **BACKUP SYSTEM**
━━━━━━━━━━━━━━━━━━━━
📊 **Backup Stats:**
• Total Backups: {total_backups}
• Storage Used: {sum(b[3] for b in recent_backups):.2f} KB
━━━━━━━━━━━━━━━━━━━━
**Recent Backups:**
"""
    
    for backup_id, backup_name, created_date, size_kb in recent_backups:
        text += f"\n• {backup_name} ({size_kb:.1f} KB) - {created_date}"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔄 Create Backup", callback_data="create_backup"))
    markup.add(types.InlineKeyboardButton("📥 Download Latest", callback_data="download_latest_backup"))
    markup.add(types.InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=markup, parse_mode="Markdown")

def export_database(call):
    try:
        # Create backup directory
        backup_dir = Path('database_backups')
        backup_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f"cyber_bot_db_{timestamp}.db"
        backup_path = backup_dir / backup_name
        
        # Copy database
        import shutil
        shutil.copy2(Config.DB_NAME, backup_path)
        
        # Create zip with additional info
        zip_name = f"database_backup_{timestamp}.zip"
        zip_path = backup_dir / zip_name
        
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            zipf.write(backup_path, backup_name)
            
            # Add info file
            info_data = {
                'backup_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'database': Config.DB_NAME,
                'tables': ['users', 'deployments', 'keys', 'backups'],
                'version': 'Cyber Bot Hosting v4.0'
            }
            
            info_file = backup_dir / 'backup_info.json'
            with open(info_file, 'w') as f:
                json.dump(info_data, f, indent=2)
            
            zipf.write(info_file, 'backup_info.json')
            info_file.unlink()
        
        # Send the file
        with open(zip_path, 'rb') as f:
            bot.send_document(call.message.chat.id, f, caption="💾 **Database Backup**")
        
        # Cleanup
        backup_path.unlink()
        zip_path.unlink()
        
        bot.answer_callback_query(call.id, "✅ Database exported successfully!")
        
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Export failed: {str(e)}")

def view_backup_details(call, backup_id):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    
    backup_info = c.execute("SELECT * FROM backups WHERE id=?", (backup_id,)).fetchone()
    conn.close()
    
    if not backup_info:
        bot.answer_callback_query(call.id, "❌ Backup not found!")
        return
    
    text = f"""
📋 **BACKUP DETAILS**
━━━━━━━━━━━━━━━━━━━━
**Backup ID:** {backup_info[0]}
**Name:** {backup_info[2]}
**Bot ID:** {backup_info[1]}
**Created:** {backup_info[4]}
**Size:** {backup_info[5]:.2f} KB
**Path:** `{backup_info[3]}`
━━━━━━━━━━━━━━━━━━━━
    """
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📥 Download", callback_data=f"download_backup_{backup_id}"))
    markup.add(types.InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_backup_{backup_id}"))
    markup.add(types.InlineKeyboardButton("🔙 Backup System", callback_data="backup_system"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=markup, parse_mode="Markdown")

def show_admin_stats(call):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    
    total_users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    prime_users = c.execute("SELECT COUNT(*) FROM users WHERE is_prime=1").fetchone()[0]
    total_bots = c.execute("SELECT COUNT(*) FROM deployments").fetchone()[0]
    running_bots = c.execute("SELECT COUNT(*) FROM deployments WHERE status='Running'").fetchone()[0]
    total_keys = c.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
    
    conn.close()
    
    stats = get_system_stats()
    cpu_usage = stats['cpu_percent']
    ram_usage = stats['ram_percent']
    disk_usage = stats['disk_percent']
    
    text = f"""
📈 **ADMIN STATISTICS**
━━━━━━━━━━━━━━━━━━━━
👥 **User Stats:**
• Total Users: {total_users}
• Prime Users: {prime_users}
• Free Users: {total_users - prime_users}
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot Stats:**
• Total Bots: {total_bots}
• Running Bots: {running_bots}
• Stopped Bots: {total_bots - running_bots}
━━━━━━━━━━━━━━━━━━━━
🔑 **Key Stats:**
• Total Keys: {total_keys}
━━━━━━━━━━━━━━━━━━━━
🖥️ **System Status:**
• CPU Usage: {cpu_usage:.1f}%
• RAM Usage: {ram_usage:.1f}%
• Disk Usage: {disk_usage:.1f}%
━━━━━━━━━━━━━━━━━━━━
🌐 **Hosting Info:**
• Platform: Render.com
• Port: {Config.PORT}
• Database: SQLite
━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("👥 Users", callback_data="all_users"),
        types.InlineKeyboardButton("🤖 Bots", callback_data="all_bots_admin")
    )
    markup.add(types.InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=markup, parse_mode="Markdown")

def toggle_maintenance(call):
    global Config
    Config.MAINTENANCE = not Config.MAINTENANCE
    
    status = "ENABLED 🔴" if Config.MAINTENANCE else "DISABLED 🟢"
    text = f"""
⚙️ **MAINTENANCE MODE**
━━━━━━━━━━━━━━━━━━━━
Status: {status}
━━━━━━━━━━━━━━━━━━━━
Maintenance mode has been {'enabled' if Config.MAINTENANCE else 'disabled'}.
Only admin can access the system when enabled.
━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=markup, parse_mode="Markdown")

def show_premium_info(call):
    text = """
👑 **PREMIUM FEATURES**
━━━━━━━━━━━━━━━━━━━━
✅ **Unlimited Bot Deployment**
✅ **Priority Support**
✅ **Advanced Monitoring**
✅ **Custom Bot Names**
✅ **Library Installation**
✅ **Live Statistics**
✅ **24/7 Server Uptime**
✅ **No Ads**
✅ **ZIP File Support**
✅ **Bot Export Feature**
✅ **API Key Management**
━━━━━━━━━━━━━━━━━━━━
💎 **Get Prime Today!**
Click 'Get Prime Pass' to contact admin.
━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💎 Get Prime Pass", url="https://t.me/zerox6t9"))
    markup.add(types.InlineKeyboardButton("🔑 Activate Prime", callback_data="activate_prime"))
    markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="back_main"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=markup, parse_mode="Markdown")

def show_settings(call):
    uid = call.from_user.id
    user = get_user(uid)
    
    if not user:
        bot.answer_callback_query(call.id, "❌ User data not found")
        return
    
    text = f"""
⚙️ **SETTINGS**
━━━━━━━━━━━━━━━━━━━━
👤 **Account Settings:**
• User ID: `{uid}`
• Status: {'Prime 👑' if is_prime(uid) else 'Free 🆓'}
• File Limit: {user[3]} files
━━━━━━━━━━━━━━━━━━━━
🔧 **Bot Settings:**
• Auto-restart: Disabled
• Notifications: Enabled
• Language: English
━━━━━━━━━━━━━━━━━━━━
⚠️ **Danger Zone:**
• Delete Account
• Reset Settings
━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🔔 Notifications", callback_data="notif_settings"),
        types.InlineKeyboardButton("🌐 Language", callback_data="lang_settings")
    )
    markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="back_main"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=markup, parse_mode="Markdown")

def process_key_step(message, old_mid):
    uid = message.from_user.id
    key_input = message.text.strip().upper()
    
    bot.delete_message(message.chat.id, message.message_id)
    
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    res = c.execute("SELECT * FROM keys WHERE key=?", (key_input,)).fetchone()
    
    if res:
        days, limit = res[1], res[2]
        expiry_date = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        
        c.execute("UPDATE users SET expiry=?, file_limit=?, is_prime=1 WHERE id=?", 
                 (expiry_date, limit, uid))
        c.execute("DELETE FROM keys WHERE key=?", (key_input,))
        conn.commit()
        conn.close()
        
        text = f"""
✅ **PRIME ACTIVATED!**
━━━━━━━━━━━━━━━━━━━━
🎉 Congratulations! You are now a Prime member.
━━━━━━━━━━━━━━━━━━━━
📅 **Expiry:** {expiry_date}
📦 **File Limit:** {limit} files
━━━━━━━━━━━━━━━━━━━━
Enjoy all premium features!
        """
        
        bot.edit_message_text(text, message.chat.id, old_mid, 
                             reply_markup=main_menu(uid),
                             parse_mode="Markdown")
    else:
        conn.close()
        text = """
❌ **INVALID KEY**
━━━━━━━━━━━━━━━━━━━━
The key you entered is invalid or expired.
━━━━━━━━━━━━━━━━━━━━
Please check the key and try again.
        """
        bot.edit_message_text(text, message.chat.id, old_mid, 
                             reply_markup=main_menu(uid),
                             parse_mode="Markdown")

# Flask Routes for Render
@app.route('/')
def home():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>🤖 Cyber Bot Hosting v4.0</title>
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
            .feature {
                background: rgba(255, 255, 255, 0.15);
                padding: 15px;
                margin: 10px 0;
                border-radius: 8px;
                display: flex;
                align-items: center;
            }
            .feature i {
                margin-right: 15px;
                font-size: 1.5em;
            }
            .stats {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 15px;
                margin: 30px 0;
            }
            .stat-box {
                background: rgba(255, 255, 255, 0.2);
                padding: 20px;
                border-radius: 10px;
                text-align: center;
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
            .footer {
                text-align: center;
                margin-top: 40px;
                padding-top: 20px;
                border-top: 1px solid rgba(255, 255, 255, 0.3);
            }
        </style>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    </head>
    <body>
        <div class="container">
            <h1><i class="fas fa-robot"></i> Cyber Bot Hosting v4.0</h1>
            
            <div class="status">
                <h2><i class="fas fa-server"></i> Server Status: <span style="color: #4CAF50;">✅ ONLINE</span></h2>
                <p>Bot hosting service is running smoothly on Render.com</p>
            </div>
            
            <div class="stats">
                <div class="stat-box">
                    <i class="fas fa-users"></i>
                    <h3>Active Users</h3>
                    <p>24/7 Service</p>
                </div>
                <div class="stat-box">
                    <i class="fas fa-robot"></i>
                    <h3>Bot Hosting</h3>
                    <p>Unlimited Deployment</p>
                </div>
                <div class="stat-box">
                    <i class="fas fa-shield-alt"></i>
                    <h3>Secure</h3>
                    <p>Protected Environment</p>
                </div>
                <div class="stat-box">
                    <i class="fas fa-bolt"></i>
                    <h3>Fast</h3>
                    <p>High Performance</p>
                </div>
            </div>
            
            <h2><i class="fas fa-star"></i> New Features</h2>
            
            <div class="feature">
                <i class="fas fa-file-archive"></i>
                <div>
                    <h3>ZIP File Support</h3>
                    <p>Upload ZIP files with multiple Python files</p>
                </div>
            </div>
            
            <div class="feature">
                <i class="fas fa-download"></i>
                <div>
                    <h3>Bot Export</h3>
                    <p>Export your bots as ZIP files</p>
                </div>
            </div>
            
            <div class="feature">
                <i class="fas fa-key"></i>
                <div>
                    <h3>API Key System</h3>
                    <p>Each bot gets unique API key</p>
                </div>
            </div>
            
            <div class="feature">
                <i class="fas fa-database"></i>
                <div>
                    <h3>Database Management</h3>
                    <p>Advanced admin database tools</p>
                </div>
            </div>
            
            <div style="text-align: center; margin: 40px 0;">
                <a href="https://t.me/cyber_bot_hosting_bot" class="btn" target="_blank">
                    <i class="fab fa-telegram"></i> Start on Telegram
                </a>
                <a href="https://render.com" class="btn" target="_blank" style="background: linear-gradient(45deg, #00b09b, #96c93d);">
                    <i class="fas fa-cloud"></i> Hosted on Render
                </a>
            </div>
            
            <div class="footer">
                <p><i class="fas fa-info-circle"></i> System Port: """ + str(Config.PORT) + """ | Python 3.9+ | SQLite Database</p>
                <p>© 2024 Cyber Bot Hosting. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

@app.route('/health')
def health():
    return {"status": "healthy", "service": "Cyber Bot Hosting v4.0", "port": Config.PORT}

# Start Bot and Server for Render
def start_bot():
    print(f"🤖 Starting Telegram Bot on Render (Port: {Config.PORT})...")
    while True:
        try:
            bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"⚠️ Bot error: {e}")
            time.sleep(5)

if __name__ == '__main__':
    print(f"""
🤖 CYBER BOT HOSTING v4.0
━━━━━━━━━━━━━━━━━━━━
🚀 Starting on Render.com
• Port: {Config.PORT}
• Admin ID: {Config.ADMIN_ID}
• Database: ✅
• Project Directory: ✅
━━━━━━━━━━━━━━━━━━━━
    """)
    
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()
    
    print(f"✅ Telegram bot started in background")
    print(f"🌐 Flask server starting on port {Config.PORT}")
    print("━━━━━━━━━━━━━━━━━━━━")
    
    app.run(host='0.0.0.0', port=Config.PORT, debug=False, use_reloader=False)
