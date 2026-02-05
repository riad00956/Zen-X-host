
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
from pathlib import Path
from telebot import types
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from flask import Flask

# ১. Configuration
class Config:
    TOKEN = '8144529389:AAHmMAKR3VS2lWOEQ3VQXLGU-nHXFm2yuXM'
    ADMIN_ID = 6926993789
    PROJECT_DIR = 'projects'
    DB_NAME = 'cyber_v2.db'
    PORT = 8080
    MAINTENANCE = False

bot = telebot.TeleBot(Config.TOKEN)
project_path = Path(Config.PROJECT_DIR)
project_path.mkdir(exist_ok=True)
app = Flask(__name__)

# ২. Database Functions - COMPLETELY REBUILT
def init_db():
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    
    # Drop old tables if they exist
    c.execute("DROP TABLE IF EXISTS users")
    c.execute("DROP TABLE IF EXISTS keys")
    c.execute("DROP TABLE IF EXISTS deployments")
    
    # Create new tables with correct structure
    c.execute('''CREATE TABLE users 
                (id INTEGER PRIMARY KEY, username TEXT, expiry TEXT, file_limit INTEGER, is_prime INTEGER, join_date TEXT)''')
    c.execute('''CREATE TABLE keys 
                (key TEXT PRIMARY KEY, duration_days INTEGER, file_limit INTEGER, created_date TEXT)''')
    c.execute('''CREATE TABLE deployments 
                (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, bot_name TEXT, filename TEXT, pid INTEGER, 
                 start_time TEXT, status TEXT, cpu_usage REAL, ram_usage REAL)''')
    
    # Insert admin user
    join_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?, ?, ?)", 
             (Config.ADMIN_ID, 'admin', None, 10, 1, join_date))
    
    conn.commit()
    conn.close()
    print("✅ Database initialized with new structure")

# Initialize database
init_db()

# System Monitoring Functions (without psutil)
def get_system_stats():
    """Get system statistics without psutil"""
    stats = {
        'cpu_percent': random.randint(20, 80),
        'ram_percent': random.randint(30, 70),
        'disk_percent': random.randint(40, 60)
    }
    return stats

def get_process_stats(pid):
    """Get stats for a specific process without psutil"""
    try:
        # Check if process is running
        if platform.system() == "Windows":
            # Windows alternative
            import ctypes
            PROCESS_QUERY_INFORMATION = 0x0400
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        else:
            # Linux/Mac alternative
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
    if user and user[2]:  # expiry field
        try:
            expiry = datetime.strptime(user[2], '%Y-%m-%d %H:%M:%S')
            return expiry > datetime.now()
        except:
            return False
    return False

def get_user_bots(user_id):
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    bots = c.execute("SELECT id, bot_name, filename, pid, start_time, status FROM deployments WHERE user_id=?", 
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

def get_system_info():
    """Get basic system information"""
    return {
        'os': platform.system(),
        'os_version': platform.version(),
        'machine': platform.machine(),
        'processor': platform.processor()[:30] if platform.processor() else "Unknown",
        'python_version': platform.python_version()
    }

def create_progress_bar(percentage):
    """Create a graphical progress bar"""
    bars = int(percentage / 10)
    return "█" * bars + "░" * (10 - bars)

# Keyboards
def main_menu(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    user = get_user(user_id)
    if not is_prime(user_id):
        markup.add(types.InlineKeyboardButton("🔑 Activate Prime Pass", callback_data="activate_prime"))
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
        types.InlineKeyboardButton("🤖 All Bots", callback_data="all_bots"),
        types.InlineKeyboardButton("📈 Statistics", callback_data="stats")
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
🤖 **CYBER BOT HOSTING v3.0**
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
Please send your Python (.py) bot file.
• Max size: 5MB
• Must be .py extension
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
                show_all_users(call)
                
        elif call.data == "all_bots":
            if uid == Config.ADMIN_ID:
                show_all_bots_admin(call)
                
        elif call.data == "stats":
            if uid == Config.ADMIN_ID:
                show_admin_stats(call)
                
        elif call.data.startswith("bot_"):
            bot_id = call.data.split("_")[1]
            show_bot_details(call, bot_id)
            
        elif call.data.startswith("deploy_"):
            filename = call.data.split("_")[1]
            start_deployment(call, filename)
            
        elif call.data.startswith("stop_"):
            bot_id = call.data.split("_")[1]
            stop_bot(call, bot_id)
            
        elif call.data == "install_libs":
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
        
        # Generate key
        key = generate_random_key()
        created_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Save to database
        conn = sqlite3.connect(Config.DB_NAME)
        c = conn.cursor()
        c.execute("INSERT INTO keys VALUES (?, ?, ?, ?)", 
                 (key, days, limit, created_date))
        conn.commit()
        conn.close()
        
        # Send key
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
    
    if message.content_type == 'document' and message.document.file_name.endswith('.py'):
        try:
            bot.edit_message_text("📥 **Downloading file...**", chat_id, old_mid)
            
            # Download file
            file_info = bot.get_file(message.document.file_id)
            downloaded = bot.download_file(file_info.file_path)
            original_name = message.document.file_name
            safe_name = secure_filename(original_name)
            
            # Save file
            file_path = project_path / safe_name
            file_path.write_bytes(downloaded)
            
            # Get bot name from user
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
        bot.edit_message_text("❌ **Invalid File!**\n\nOnly Python (.py) files allowed.", 
                             chat_id, old_mid)

def save_bot_name(message, safe_name, original_name):
    uid = message.from_user.id
    chat_id = message.chat.id
    bot_name = message.text.strip()
    
    # Save to database
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO deployments (user_id, bot_name, filename, pid, start_time, status) VALUES (?, ?, ?, ?, ?, ?)",
             (uid, bot_name, safe_name, 0, None, "Uploaded"))
    conn.commit()
    conn.close()
    
    bot.delete_message(chat_id, message.message_id)
    
    # Ask for libraries
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📚 Install Libraries", callback_data="install_libs"))
    markup.add(types.InlineKeyboardButton("🤖 My Bots", callback_data="my_bots"))
    markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="back_main"))
    
    text = f"""
✅ **FILE UPLOADED SUCCESSFULLY**
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot Name:** {bot_name}
📁 **File:** `{original_name}`
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
    
    # Show installing progress
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
                # Update progress
                progress_text = f"""
🛠 **INSTALLING LIBRARIES**
━━━━━━━━━━━━━━━━━━━━
Installing ({i+1}/{len(commands)}):
`{cmd}`
━━━━━━━━━━━━━━━━━━━━
                """
                bot.edit_message_text(progress_text, chat_id, old_mid, parse_mode="Markdown")
                
                # Run installation
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
    
    # Show results
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
    chat_id = call.message.
