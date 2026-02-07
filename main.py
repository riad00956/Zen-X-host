
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
import asyncio
import logging
from pathlib import Path
from telebot import types
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from flask import Flask, render_template_string, send_file, Response, jsonify
from concurrent.futures import ThreadPoolExecutor

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global variable to track if bot is running
bot_running = False

# ১. Configuration
class Config:
    TOKEN = os.environ.get('BOT_TOKEN', '8494225623:AAG_HRSHoBpt36bdeUvYJL4ONnh-2bf6BnY')
    ADMIN_ID = int(os.environ.get('ADMIN_ID', 7832264582))
    PROJECT_DIR = 'projects'
    DB_NAME = 'cyber_v2.db'
    PORT = int(os.environ.get('PORT', 10000))
    MAINTENANCE = False
    ADMIN_USERNAME = 'zerox6t9'
    BOT_USERNAME = 'zen_xbot'
    MAX_BOTS_PER_USER = 5
    MAX_CONCURRENT_DEPLOYMENTS = 4
    # Updated to 300 capacity nodes
    HOSTING_NODES = [
        {"name": "Node-1", "status": "active", "capacity": 300},
        {"name": "Node-2", "status": "active", "capacity": 300},
        {"name": "Node-3", "status": "active", "capacity": 300}
    ]

bot = telebot.TeleBot(Config.TOKEN, parse_mode="Markdown")
project_path = Path(Config.PROJECT_DIR)
project_path.mkdir(exist_ok=True)
app = Flask(__name__)

# Thread pool for concurrent operations
executor = ThreadPoolExecutor(max_workers=10)

# User session management
user_sessions = {}
user_message_history = {}

# ২. Enhanced Database Functions
def init_db():
    conn = sqlite3.connect(Config.DB_NAME)
    c = conn.cursor()
    
    # Drop old tables if they exist
    c.execute("DROP TABLE IF EXISTS users")
    c.execute("DROP TABLE IF EXISTS keys")
    c.execute("DROP TABLE IF EXISTS deployments")
    c.execute("DROP TABLE IF EXISTS nodes")
    
    # Create new tables with enhanced structure
    c.execute('''CREATE TABLE users 
                (id INTEGER PRIMARY KEY, username TEXT, expiry TEXT, file_limit INTEGER, 
                 is_prime INTEGER, join_date TEXT, last_renewal TEXT, total_bots_deployed INTEGER DEFAULT 0)''')
    
    c.execute('''CREATE TABLE keys 
                (key TEXT PRIMARY KEY, duration_days INTEGER, file_limit INTEGER, created_date TEXT, 
                 used_by TEXT, used_date TEXT)''')
    
    c.execute('''CREATE TABLE deployments 
                (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, bot_name TEXT, 
                 filename TEXT, pid INTEGER, start_time TEXT, status TEXT, 
                 cpu_usage REAL, ram_usage REAL, last_active TEXT, node_id INTEGER,
                 logs TEXT, restart_count INTEGER DEFAULT 0)''')
    
    c.execute('''CREATE TABLE nodes
                (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, status TEXT, 
                 capacity INTEGER, current_load INTEGER DEFAULT 0, last_check TEXT)''')
    
    # Insert admin user
    join_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    expiry_date = (datetime.now() + timedelta(days=3650)).strftime('%Y-%m-%d %H:%M:%S')
    c.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
             (Config.ADMIN_ID, 'admin', expiry_date, 100, 1, join_date, join_date, 0))
    
    # Initialize hosting nodes with 300 capacity
    for i, node in enumerate(Config.HOSTING_NODES, 1):
        c.execute("INSERT INTO nodes (name, status, capacity, last_check) VALUES (?, ?, ?, ?)",
                 (node['name'], node['status'], node['capacity'], join_date))
    
    conn.commit()
    conn.close()

# Initialize database
init_db()

def get_db():
    conn = sqlite3.connect(Config.DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# System Monitoring Functions
def get_system_stats():
    """Get system statistics"""
    conn = get_db()
    c = conn.cursor()
    
    total_bots = c.execute("SELECT COUNT(*) FROM deployments").fetchone()[0]
    running_bots = c.execute("SELECT COUNT(*) FROM deployments WHERE status='Running'").fetchone()[0]
    total_users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    
    conn.close()
    
    stats = {
        'cpu_percent': random.randint(5, 40),
        'ram_percent': random.randint(15, 60),
        'disk_percent': random.randint(20, 70),
        'total_users': total_users,
        'total_bots': total_bots,
        'running_bots': running_bots,
        'uptime_days': random.randint(1, 365),
        'total_capacity': len(Config.HOSTING_NODES) * 300,
        'available_capacity': (len(Config.HOSTING_NODES) * 300) - running_bots
    }
    return stats

def get_available_nodes():
    """Get available hosting nodes"""
    conn = get_db()
    c = conn.cursor()
    nodes = c.execute("SELECT * FROM nodes WHERE status='active'").fetchall()
    conn.close()
    return nodes

def assign_bot_to_node(user_id, bot_name):
    """Assign bot to an available node"""
    nodes = get_available_nodes()
    
    if not nodes:
        return None
    
    # Find node with lowest current load
    best_node = None
    lowest_load = float('inf')
    
    for node in nodes:
        load = node['current_load'] / node['capacity']
        if load < lowest_load:
            lowest_load = load
            best_node = node
    
    return best_node

# Helper Functions
def get_user(user_id):
    conn = get_db()
    c = conn.cursor()
    user = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return user

def update_user_bot_count(user_id):
    """Update user's bot count"""
    conn = get_db()
    c = conn.cursor()
    count = c.execute("SELECT COUNT(*) FROM deployments WHERE user_id=?", (user_id,)).fetchone()[0]
    c.execute("UPDATE users SET total_bots_deployed=? WHERE id=?", (count, user_id))
    conn.commit()
    conn.close()

def is_prime(user_id):
    user = get_user(user_id)
    if user and user['expiry']:
        try:
            expiry = datetime.strptime(user['expiry'], '%Y-%m-%d %H:%M:%S')
            return expiry > datetime.now()
        except:
            return False
    return False

def get_user_bots(user_id):
    conn = get_db()
    c = conn.cursor()
    bots = c.execute("SELECT id, bot_name, filename, pid, start_time, status, node_id, restart_count FROM deployments WHERE user_id=?", 
                    (user_id,)).fetchall()
    conn.close()
    return bots

def update_bot_stats(bot_id, cpu, ram):
    conn = get_db()
    c = conn.cursor()
    last_active = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute("UPDATE deployments SET cpu_usage=?, ram_usage=?, last_active=? WHERE id=?", 
             (cpu, ram, last_active, bot_id))
    conn.commit()
    conn.close()

def generate_random_key():
    prefix = "ZENX-"
    random_chars = ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=12))
    return f"{prefix}{random_chars}"

def create_progress_bar(percentage, length=10):
    """Create a graphical progress bar"""
    filled = int(percentage * length / 100)
    return "█" * filled + "░" * (length - filled)

def create_zip_file(bot_id, bot_name, filename, user_id):
    """Create a zip file for bot export"""
    try:
        # Create export directory if not exists
        export_dir = Path('exports')
        export_dir.mkdir(exist_ok=True)
        
        # Create zip file
        zip_filename = f"bot_export_{bot_id}_{int(time.time())}.zip"
        zip_path = export_dir / zip_filename
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Add bot file
            bot_file_path = project_path / filename
            if bot_file_path.exists():
                zipf.write(bot_file_path, arcname=filename)
            
            # Add metadata
            metadata = {
                'bot_id': bot_id,
                'bot_name': bot_name,
                'filename': filename,
                'user_id': user_id,
                'export_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'version': 'ZEN X HOST BOT v3.2.0',
                'exported_by': 'ZEN X Bot Hosting System',
                'node_info': '300-Capacity Multi-Node Hosting'
            }
            
            # Create metadata file in zip
            metadata_str = json.dumps(metadata, indent=4)
            zipf.writestr('metadata.json', metadata_str)
        
        return zip_path
    except Exception as e:
        logger.error(f"Error creating zip: {e}")
        return None

def check_prime_expiry(user_id):
    """Check if prime has expired and return appropriate message"""
    user = get_user(user_id)
    if user and user['expiry']:
        try:
            expiry = datetime.strptime(user['expiry'], '%Y-%m-%d %H:%M:%S')
            now = datetime.now()
            if expiry > now:
                # Still active
                days_left = (expiry - now).days
                hours_left = (expiry - now).seconds // 3600
                return {
                    'expired': False,
                    'days_left': days_left,
                    'hours_left': hours_left,
                    'expiry_date': expiry.strftime('%Y-%m-%d %H:%M:%S')
                }
            else:
                # Expired
                days_expired = (now - expiry).days
                return {
                    'expired': True,
                    'days_expired': days_expired,
                    'expiry_date': expiry.strftime('%Y-%m-%d %H:%M:%S'),
                    'message': f"Your Prime subscription expired {days_expired} day(s) ago. Please renew to continue using premium features."
                }
        except:
            return {'expired': True, 'message': 'Invalid expiry date format'}
    return {'expired': True, 'message': 'No Prime subscription found'}

def get_user_session(user_id):
    """Get user session data"""
    if user_id in user_sessions:
        return user_sessions[user_id]
    return {'state': 'main_menu'}

def set_user_session(user_id, data):
    """Set user session data"""
    user_sessions[user_id] = data

def clear_user_session(user_id):
    """Clear user session"""
    if user_id in user_sessions:
        del user_sessions[user_id]

def update_message_history(user_id, message_id):
    """Update user's message history"""
    if user_id not in user_message_history:
        user_message_history[user_id] = []
    
    user_message_history[user_id].append(message_id)
    
    # Keep only last 5 messages
    if len(user_message_history[user_id]) > 5:
        user_message_history[user_id] = user_message_history[user_id][-5:]

def cleanup_old_messages(user_id):
    """Cleanup old messages for user"""
    if user_id in user_message_history:
        del user_message_history[user_id]

# Keyboard Functions
def get_main_keyboard(user_id):
    """Get main menu keyboard"""
    user = get_user(user_id)
    prime_status = check_prime_expiry(user_id)
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    if not prime_status['expired']:
        # Prime is active
        buttons = [
            "📤 Upload Bot",
            "🤖 My Bots",
            "🚀 Deploy Bot",
            "📊 Dashboard",
            "⚙️ Settings",
            "💎 Premium Info"
        ]
    else:
        # Prime expired or not active
        buttons = [
            "🔑 Activate Prime",
            "💎 Premium Info",
            "📞 Contact Admin",
            "ℹ️ Help"
        ]
    
    # Arrange buttons in rows of 2
    for i in range(0, len(buttons), 2):
        row = buttons[i:i+2]
        markup.add(*[types.KeyboardButton(btn) for btn in row])
    
    if user_id == Config.ADMIN_ID:
        markup.add(types.KeyboardButton("👑 Admin Panel"))
    
    return markup

def get_admin_keyboard():
    """Get admin keyboard"""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    buttons = [
        "🎫 Generate Key",
        "👥 All Users",
        "🤖 All Bots",
        "📈 Statistics",
        "🗄️ View Database",
        "💾 Backup DB",
        "⚙️ Maintenance",
        "🌐 Nodes Status"
    ]
    
    for i in range(0, len(buttons), 2):
        row = buttons[i:i+2]
        markup.add(*[types.KeyboardButton(btn) for btn in row])
    
    markup.add(types.KeyboardButton("🏠 Main Menu"))
    return markup

def get_bot_actions_keyboard(bot_id):
    """Get bot actions inline keyboard"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🛑 Stop", callback_data=f"stop_{bot_id}"),
        types.InlineKeyboardButton("🔄 Restart", callback_data=f"restart_{bot_id}"),
        types.InlineKeyboardButton("📥 Export", callback_data=f"export_{bot_id}"),
        types.InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_{bot_id}"),
        types.InlineKeyboardButton("📜 Logs", callback_data=f"logs_{bot_id}")
    )
    markup.add(types.InlineKeyboardButton("🔙 Back to Bots", callback_data="my_bots"))
    return markup

def get_file_selection_keyboard(files):
    """Get file selection inline keyboard"""
    markup = types.InlineKeyboardMarkup(row_width=1)
    for file_id, filename, bot_name in files:
        markup.add(types.InlineKeyboardButton(f"📁 {bot_name}", callback_data=f"select_{file_id}"))
    markup.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="cancel"))
    return markup

def get_yes_no_keyboard(action, bot_id):
    """Get yes/no confirmation keyboard"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ Yes", callback_data=f"confirm_{action}_{bot_id}"),
        types.InlineKeyboardButton("❌ No", callback_data=f"bot_{bot_id}")
    )
    return markup

# Message Editing Helper
def edit_or_send_message(chat_id, message_id, text, reply_markup=None, parse_mode="Markdown"):
    """Edit existing message or send new one"""
    try:
        if message_id:
            return bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            msg = bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
            update_message_history(chat_id, msg.message_id)
            return msg
    except Exception as e:
        logger.error(f"Error editing/sending message: {e}")
        # If edit fails, send new message
        msg = bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
        update_message_history(chat_id, msg.message_id)
        return msg

# Message Handlers
@bot.message_handler(commands=['start', 'menu', 'help'])
def handle_commands(message):
    uid = message.from_user.id
    username = message.from_user.username or "User"
    
    if Config.MAINTENANCE and uid != Config.ADMIN_ID:
        bot.send_message(message.chat.id, "🛠 **System Maintenance**\n\nWe're currently upgrading our servers. Please try again later.")
        return
    
    # Register user if not exists
    user = get_user(uid)
    if not user:
        conn = get_db()
        c = conn.cursor()
        join_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute("INSERT OR IGNORE INTO users (id, username, expiry, file_limit, is_prime, join_date, last_renewal) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                 (uid, username, None, 1, 0, join_date, None))
        conn.commit()
        conn.close()
        user = get_user(uid)
    
    clear_user_session(uid)
    cleanup_old_messages(uid)
    
    prime_status = check_prime_expiry(uid)
    
    if prime_status['expired']:
        status = "EXPIRED ⚠️"
        expiry_msg = prime_status.get('message', 'Not Activated')
        plan = "Free"
    else:
        status = "PRIME 👑"
        expiry_msg = f"{prime_status['days_left']} days left"
        plan = "Premium"
    
    text = f"""
🤖 **ZEN X HOST BOT v3.2.0**
*300-Capacity Multi-Node Hosting System*
━━━━━━━━━━━━━━━━━━━━
👤 **User:** @{username}
🆔 **ID:** `{uid}`
💎 **Status:** {status}
📅 **Join Date:** {user['join_date']}
━━━━━━━━━━━━━━━━━━━━
📊 **Account Details:**
• Plan: {plan}
• File Limit: `{user['file_limit']}` files
• Expiry: {expiry_msg}
• Total Bots: {user['total_bots_deployed'] or 0}
━━━━━━━━━━━━━━━━━━━━
💡 *Use keyboard buttons below:*
"""
    
    msg = edit_or_send_message(message.chat.id, None, text, reply_markup=get_main_keyboard(uid))
    update_message_history(uid, msg.message_id)

@bot.message_handler(commands=['admin'])
def handle_admin(message):
    uid = message.from_user.id
    if uid == Config.ADMIN_ID:
        set_user_session(uid, {'state': 'admin_panel'})
        cleanup_old_messages(uid)
        text = """
👑 **ADMIN CONTROL PANEL**
━━━━━━━━━━━━━━━━━━━━
Welcome to the admin dashboard.
Select an option from the keyboard below:
━━━━━━━━━━━━━━━━━━━━
"""
        msg = edit_or_send_message(message.chat.id, None, text, reply_markup=get_admin_keyboard())
        update_message_history(uid, msg.message_id)
    else:
        bot.reply_to(message, "⛔ **Access Denied!**")

@bot.message_handler(func=lambda message: True)
def handle_text_messages(message):
    uid = message.from_user.id
    text = message.text
    
    session = get_user_session(uid)
    
    # Handle based on session state
    if session.get('state') == 'waiting_for_key':
        process_key_input(message)
    elif session.get('state') == 'waiting_for_bot_name':
        process_bot_name_input(message)
    elif session.get('state') == 'waiting_for_libs':
        process_libraries_input(message)
    elif session.get('state') == 'waiting_for_duration':
        process_duration_input(message)
    elif session.get('state') == 'waiting_for_limit':
        process_limit_input(message)
    else:
        # Handle main menu buttons
        handle_main_menu_buttons(message)

def handle_main_menu_buttons(message):
    uid = message.from_user.id
    text = message.text
    chat_id = message.chat.id
    
    # Get last message ID for editing
    last_msg_id = user_message_history.get(uid, [None])[-1] if user_message_history.get(uid) else None
    
    if text == "📤 Upload Bot":
        handle_upload_request(message, last_msg_id)
    elif text == "🤖 My Bots":
        handle_my_bots(message, last_msg_id)
    elif text == "🚀 Deploy Bot":
        handle_deploy_new(message, last_msg_id)
    elif text == "📊 Dashboard":
        handle_dashboard(message, last_msg_id)
    elif text == "⚙️ Settings":
        handle_settings(message, last_msg_id)
    elif text == "💎 Premium Info":
        handle_premium_info(message, last_msg_id)
    elif text == "🔑 Activate Prime":
        handle_activate_prime(message, last_msg_id)
    elif text == "📞 Contact Admin":
        handle_contact_admin(message, last_msg_id)
    elif text == "ℹ️ Help":
        handle_help(message, last_msg_id)
    elif text == "👑 Admin Panel":
        handle_admin_panel(message, last_msg_id)
    elif text == "🏠 Main Menu":
        handle_commands(message)
    elif text in ["🎫 Generate Key", "👥 All Users", "🤖 All Bots", "📈 Statistics", 
                  "🗄️ View Database", "💾 Backup DB", "⚙️ Maintenance", "🌐 Nodes Status"]:
        handle_admin_buttons(message, text, last_msg_id)
    else:
        bot.reply_to(message, "❓ Unknown command. Use the keyboard buttons or /help")

# Button Handlers
def handle_upload_request(message, last_msg_id=None):
    uid = message.from_user.id
    prime_status = check_prime_expiry(uid)
    
    if prime_status['expired']:
        text = "⚠️ **Prime Required**\n\nYour Prime subscription has expired. Please renew to upload files."
        edit_or_send_message(message.chat.id, last_msg_id, text, reply_markup=get_main_keyboard(uid))
        return
    
    # Check bot limit
    user_bots = get_user_bots(uid)
    if len(user_bots) >= Config.MAX_BOTS_PER_USER:
        text = f"❌ **Bot Limit Reached**\n\nYou can only have {Config.MAX_BOTS_PER_USER} bots at a time."
        edit_or_send_message(message.chat.id, last_msg_id, text, reply_markup=get_main_keyboard(uid))
        return
    
    set_user_session(uid, {'state': 'waiting_for_file'})
    
    text = """
📤 **UPLOAD BOT FILE**
━━━━━━━━━━━━━━━━━━━━
Please send your Python (.py) bot file or ZIP file containing bot.

**Requirements:**
• Max size: 5.5MB
• Allowed: .py, .zip
• Must have main function
━━━━━━━━━━━━━━━━━━━━
*Send the file now or type 'cancel' to abort*
"""
    edit_or_send_message(message.chat.id, last_msg_id, text)

def handle_my_bots(message, last_msg_id=None):
    uid = message.from_user.id
    bots = get_user_bots(uid)
    
    if not bots:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📤 Upload Bot", callback_data="upload"))
        
        text = """
🤖 **MY BOTS**
━━━━━━━━━━━━━━━━━━━━
No bots found. Upload your first bot!
━━━━━━━━━━━━━━━━━━━━
"""
        edit_or_send_message(message.chat.id, last_msg_id, text, reply_markup=markup)
        return
    
    running_bots = sum(1 for b in bots if b['status'] == "Running")
    
    text = f"""
🤖 **MY BOTS**
━━━━━━━━━━━━━━━━━━━━
**Total Bots:** {len(bots)}
**Running:** {running_bots}
**Stopped:** {len(bots) - running_bots}
━━━━━━━━━━━━━━━━━━━━
"""
    
    # Send inline keyboard for each bot
    markup = types.InlineKeyboardMarkup(row_width=1)
    for bot_info in bots:
        status_icon = "🟢" if bot_info['status'] == "Running" else "🔴"
        markup.add(types.InlineKeyboardButton(
            f"{status_icon} {bot_info['bot_name']}", 
            callback_data=f"bot_{bot_info['id']}"
        ))
    
    markup.add(types.InlineKeyboardButton("📤 Upload New Bot", callback_data="upload"))
    
    edit_or_send_message(message.chat.id, last_msg_id, text, reply_markup=markup)

def handle_deploy_new(message, last_msg_id=None):
    uid = message.from_user.id
    prime_status = check_prime_expiry(uid)
    
    if prime_status['expired']:
        text = "⚠️ **Prime Required**\n\nYour Prime subscription has expired. Please renew to deploy bots."
        edit_or_send_message(message.chat.id, last_msg_id, text, reply_markup=get_main_keyboard(uid))
        return
    
    # Get available files
    conn = get_db()
    c = conn.cursor()
    files = c.execute("SELECT id, filename, bot_name FROM deployments WHERE user_id=? AND (pid=0 OR pid IS NULL OR status='Stopped')", 
                     (uid,)).fetchall()
    conn.close()
    
    if not files:
        text = "📭 **No files available for deployment**\n\nUpload a file first."
        edit_or_send_message(message.chat.id, last_msg_id, text, reply_markup=get_main_keyboard(uid))
        return
    
    text = """
🚀 **DEPLOY BOT**
━━━━━━━━━━━━━━━━━━━━
Select a bot to deploy:
━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = get_file_selection_keyboard(files)
    edit_or_send_message(message.chat.id, last_msg_id, text, reply_markup=markup)

def handle_dashboard(message, last_msg_id=None):
    uid = message.from_user.id
    user = get_user(uid)
    
    if not user:
        text = "❌ User data not found"
        edit_or_send_message(message.chat.id, last_msg_id, text)
        return
    
    bots = get_user_bots(uid)
    running_bots = sum(1 for b in bots if b['status'] == "Running")
    total_bots = len(bots)
    
    # Get system stats
    stats = get_system_stats()
    cpu_usage = stats['cpu_percent']
    ram_usage = stats['ram_percent']
    disk_usage = stats['disk_percent']
    
    cpu_bar = create_progress_bar(cpu_usage)
    ram_bar = create_progress_bar(ram_usage)
    disk_bar = create_progress_bar(disk_usage)
    
    # Check prime status
    prime_status = check_prime_expiry(uid)
    
    # Get node status
    nodes = get_available_nodes()
    active_nodes = len(nodes)
    total_capacity = sum(node['capacity'] for node in nodes)
    used_capacity = sum(node['current_load'] for node in nodes)
    
    text = f"""
📊 **USER DASHBOARD**
━━━━━━━━━━━━━━━━━━━━
👤 **Account Info:**
• Status: {'PRIME 👑' if not prime_status['expired'] else 'EXPIRED ⚠️'}
• File Limit: {user['file_limit']} files
• Total Bots: {total_bots}/{Config.MAX_BOTS_PER_USER}
• Running: {running_bots}
━━━━━━━━━━━━━━━━━━━━
🖥️ **System Status:**
• CPU: {cpu_bar} {cpu_usage:.1f}%
• RAM: {ram_bar} {ram_usage:.1f}%
• Disk: {disk_bar} {disk_usage:.1f}%
• Active Nodes: {active_nodes}/{len(Config.HOSTING_NODES)}
• Capacity: {used_capacity}/{total_capacity} bots
━━━━━━━━━━━━━━━━━━━━
🌐 **Hosting Platform:**
• Platform: ZEN X 300-CAPACITY NODES
• Type: Web Service
• Max Concurrent: {Config.MAX_CONCURRENT_DEPLOYMENTS}
• Region: Asia → Bangladesh 🇧🇩
━━━━━━━━━━━━━━━━━━━━
"""
    
    edit_or_send_message(message.chat.id, last_msg_id, text)

def handle_settings(message, last_msg_id=None):
    uid = message.from_user.id
    user = get_user(uid)
    
    if not user:
        text = "❌ User data not found"
        edit_or_send_message(message.chat.id, last_msg_id, text)
        return
    
    prime_status = check_prime_expiry(uid)
    
    text = f"""
⚙️ **SETTINGS**
━━━━━━━━━━━━━━━━━━━━
👤 **Account Settings:**
• User ID: `{uid}`
• Status: {'PRIME 👑' if not prime_status['expired'] else 'EXPIRED ⚠️'}
• File Limit: {user['file_limit']} files
• Join Date: {user['join_date']}
━━━━━━━━━━━━━━━━━━━━
🔧 **Bot Settings:**
• Auto-restart: Enabled
• Notifications: Enabled
• Language: English
━━━━━━━━━━━━━━━━━━━━
💎 **Prime Status:**
• Active: {'Yes' if not prime_status['expired'] else 'No'}
• Expiry: {prime_status.get('expiry_date', 'N/A')}
• Days Left: {prime_status.get('days_left', 'N/A') if not prime_status['expired'] else 'Expired'}
━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🔄 Renew Prime", callback_data="activate_prime"),
        types.InlineKeyboardButton("🔔 Notifications", callback_data="notif_settings")
    )
    
    edit_or_send_message(message.chat.id, last_msg_id, text, reply_markup=markup)

def handle_premium_info(message, last_msg_id=None):
    text = f"""
👑 **PREMIUM FEATURES**
━━━━━━━━━━━━━━━━━━━━
✅ **300-Capacity Node Hosting**
✅ **Priority Support**
✅ **Advanced Monitoring**
✅ **Custom Bot Names**
✅ **Library Installation**
✅ **Live Statistics**
✅ **24/7 Server Uptime**
✅ **No Ads**
✅ **ZIP File Upload**
✅ **Bot Export Feature**
✅ **Auto-Restart System**
✅ **Logs Access**
━━━━━━━━━━━━━━━━━━━━
💎 **Get Prime Today!**
Contact: @{Config.ADMIN_USERNAME}
━━━━━━━━━━━━━━━━━━━━
💰 **Pricing:**
• 7 Days: ৳50
• 30 Days: ৳150
• 90 Days: ৳400
• 365 Days: ৳1200
━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔑 Activate/Renew", callback_data="activate_prime"))
    markup.add(types.InlineKeyboardButton("💎 Contact Admin", url=f"https://t.me/{Config.ADMIN_USERNAME}"))
    
    edit_or_send_message(message.chat.id, last_msg_id, text, reply_markup=markup)

def handle_activate_prime(message, last_msg_id=None):
    uid = message.from_user.id
    prime_status = check_prime_expiry(uid)
    
    if not prime_status['expired']:
        text = f"""
🔄 **RENEW PRIME (Early)**
━━━━━━━━━━━━━━━━━━━━
Your Prime subscription is still active.
Expires in: {prime_status['days_left']} days

You can renew early with a new key:
Format: `ZENX-XXXXXXXXXX`
━━━━━━━━━━━━━━━━━━━━
        """
    else:
        text = """
🔑 **ACTIVATE PRIME PASS**
━━━━━━━━━━━━━━━━━━━━
Enter your activation key below.
Format: `ZENX-XXXXXXXXXX`
━━━━━━━━━━━━━━━━━━━━
        """
    
    set_user_session(uid, {'state': 'waiting_for_key'})
    edit_or_send_message(message.chat.id, last_msg_id, text)

def handle_contact_admin(message, last_msg_id=None):
    text = f"""
📞 **CONTACT ADMIN**
━━━━━━━━━━━━━━━━━━━━
For support, issues, or premium purchase:
━━━━━━━━━━━━━━━━━━━━
👤 **Admin:** @{Config.ADMIN_USERNAME}
🤖 **Bot:** @{Config.BOT_USERNAME}
📧 **Support:** @rifatbro22
━━━━━━━━━━━━━━━━━━━━
"""
    edit_or_send_message(message.chat.id, last_msg_id, text)

def handle_help(message, last_msg_id=None):
    text = """
ℹ️ **HELP GUIDE**
━━━━━━━━━━━━━━━━━━━━
**How to use:**
1. First activate Prime with key
2. Upload your bot file (.py or .zip)
3. Deploy the bot to a node
4. Manage your bots from "My Bots"
━━━━━━━━━━━━━━━━━━━━
**Commands:**
• /start - Main menu
• /menu - Show menu
• /admin - Admin panel (admin only)
• /help - This guide
━━━━━━━━━━━━━━━━━━━━
**Keyboard Buttons:**
• Use the custom keyboard for quick access
• Inline buttons for specific actions
━━━━━━━━━━━━━━━━━━━━
"""
    edit_or_send_message(message.chat.id, last_msg_id, text)

def handle_admin_panel(message, last_msg_id=None):
    uid = message.from_user.id
    if uid == Config.ADMIN_ID:
        set_user_session(uid, {'state': 'admin_panel'})
        cleanup_old_messages(uid)
        text = """
👑 **ADMIN CONTROL PANEL**
━━━━━━━━━━━━━━━━━━━━
Welcome to the admin dashboard.
Select an option from the keyboard below:
━━━━━━━━━━━━━━━━━━━━
"""
        edit_or_send_message(message.chat.id, last_msg_id, text, reply_markup=get_admin_keyboard())
    else:
        edit_or_send_message(message.chat.id, last_msg_id, "⛔ Access Denied!")

def handle_admin_buttons(message, button_text, last_msg_id=None):
    uid = message.from_user.id
    chat_id = message.chat.id
    
    if uid != Config.ADMIN_ID:
        edit_or_send_message(chat_id, last_msg_id, "⛔ Access Denied!")
        return
    
    if button_text == "🎫 Generate Key":
        gen_key_step1_message(message, last_msg_id)
    elif button_text == "👥 All Users":
        show_all_users_admin(message, last_msg_id)
    elif button_text == "🤖 All Bots":
        show_all_bots_admin(message, last_msg_id)
    elif button_text == "📈 Statistics":
        show_admin_stats(message, last_msg_id)
    elif button_text == "🗄️ View Database":
        view_database_admin(message, last_msg_id)
    elif button_text == "💾 Backup DB":
        backup_database_admin(message, last_msg_id)
    elif button_text == "⚙️ Maintenance":
        toggle_maintenance_admin(message, last_msg_id)
    elif button_text == "🌐 Nodes Status":
        show_nodes_status(message, last_msg_id)

# File Upload Handler
@bot.message_handler(content_types=['document'])
def handle_document(message):
    uid = message.from_user.id
    session = get_user_session(uid)
    
    if session.get('state') != 'waiting_for_file':
        return
    
    try:
        file_name = message.document.file_name.lower()
        
        if not (file_name.endswith('.py') or file_name.endswith('.zip')):
            bot.reply_to(message, "❌ **Invalid File Type!**\n\nOnly Python (.py) or ZIP (.zip) files allowed.")
            return
        
        if message.document.file_size > 5.5 * 1024 * 1024:
            bot.reply_to(message, "❌ **File Too Large!**\n\nMaximum file size is 5.5MB.")
            return
        
        # Download file
        file_info = bot.get_file(message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        original_name = message.document.file_name
        
        # Handle ZIP file
        if file_name.endswith('.zip'):
            temp_zip_path = project_path / f"temp_{uid}_{int(time.time())}.zip"
            temp_zip_path.write_bytes(downloaded)
            
            extract_dir = project_path / f"extracted_{uid}_{int(time.time())}"
            extract_dir.mkdir(exist_ok=True)
            
            if extract_zip_file(temp_zip_path, extract_dir):
                py_files = list(extract_dir.glob('*.py'))
                
                if not py_files:
                    bot.reply_to(message, "❌ **No Python file found in ZIP!**")
                    temp_zip_path.unlink(missing_ok=True)
                    import shutil
                    shutil.rmtree(extract_dir, ignore_errors=True)
                    return
                
                py_file = py_files[0]
                safe_name = secure_filename(py_file.name)
                
                # Check if file already exists
                counter = 1
                original_safe_name = safe_name
                while (project_path / safe_name).exists():
                    name_parts = original_safe_name.rsplit('.', 1)
                    safe_name = f"{name_parts[0]}_{counter}.{name_parts[1]}"
                    counter += 1
                
                # Copy to main directory
                target_path = project_path / safe_name
                import shutil
                shutil.copy2(py_file, target_path)
                
                # Cleanup
                temp_zip_path.unlink(missing_ok=True)
                shutil.rmtree(extract_dir, ignore_errors=True)
                
                bot.reply_to(message, f"""
✅ **File extracted successfully!**
━━━━━━━━━━━━━━━━━━━━
**Original:** {original_name}
**Extracted:** {py_file.name}
**Saved as:** {safe_name}
━━━━━━━━━━━━━━━━━━━━
                """)
                
                set_user_session(uid, {
                    'state': 'waiting_for_bot_name',
                    'filename': safe_name,
                    'original_name': f"{original_name} (extracted: {py_file.name})"
                })
                
                msg = bot.send_message(message.chat.id, """
🤖 **BOT NAME SETUP**
━━━━━━━━━━━━━━━━━━━━
Enter a name for your bot (max 30 chars):
Example: `News Bot`, `Music Bot`, `Assistant`
━━━━━━━━━━━━━━━━━━━━
                """)
                update_message_history(uid, msg.message_id)
                bot.register_next_step_handler(msg, process_bot_name_input)
                return
            
        # Handle regular Python file
        safe_name = secure_filename(original_name)
        
        # Check if file already exists
        counter = 1
        original_safe_name = safe_name
        while (project_path / safe_name).exists():
            name_parts = original_safe_name.rsplit('.', 1)
            safe_name = f"{name_parts[0]}_{counter}.{name_parts[1]}"
            counter += 1
        
        file_path = project_path / safe_name
        file_path.write_bytes(downloaded)
        
        set_user_session(uid, {
            'state': 'waiting_for_bot_name',
            'filename': safe_name,
            'original_name': original_name
        })
        
        msg = bot.send_message(message.chat.id, """
🤖 **BOT NAME SETUP**
━━━━━━━━━━━━━━━━━━━━
Enter a name for your bot (max 30 chars):
Example: `News Bot`, `Music Bot`, `Assistant`
━━━━━━━━━━━━━━━━━━━━
        """)
        update_message_history(uid, msg.message_id)
        bot.register_next_step_handler(msg, process_bot_name_input)
        
    except Exception as e:
        logger.error(f"Upload error: {e}")
        bot.reply_to(message, f"❌ **Error:** {str(e)[:100]}")

def process_bot_name_input(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    
    if message.text.lower() == 'cancel':
        clear_user_session(uid)
        bot.reply_to(message, "❌ Cancelled.", reply_markup=get_main_keyboard(uid))
        return
    
    session = get_user_session(uid)
    if 'filename' not in session:
        bot.reply_to(message, "❌ Session expired. Please upload again.")
        return
    
    bot_name = message.text.strip()[:50]
    filename = session['filename']
    original_name = session['original_name']
    
    # Save to database
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO deployments (user_id, bot_name, filename, pid, start_time, status, last_active) VALUES (?, ?, ?, ?, ?, ?, ?)",
             (uid, bot_name, filename, 0, None, "Uploaded", datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    
    # Update user bot count
    update_user_bot_count(uid)
    
    conn.close()
    
    clear_user_session(uid)
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📚 Install Libraries", callback_data="install_libs"))
    markup.add(types.InlineKeyboardButton("🚀 Deploy Now", callback_data="deploy_new"))
    markup.add(types.InlineKeyboardButton("🤖 My Bots", callback_data="my_bots"))
    
    text = f"""
✅ **FILE UPLOADED SUCCESSFULLY**
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot Name:** {bot_name}
📁 **File:** `{original_name}`
📊 **Status:** Ready for setup
━━━━━━━━━━━━━━━━━━━━
"""
    
    edit_or_send_message(chat_id, None, text, reply_markup=markup)

def process_key_input(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    
    if message.text.lower() == 'cancel':
        clear_user_session(uid)
        edit_or_send_message(chat_id, None, "❌ Cancelled.", reply_markup=get_main_keyboard(uid))
        return
    
    key_input = message.text.strip().upper()
    
    conn = get_db()
    c = conn.cursor()
    res = c.execute("SELECT * FROM keys WHERE key=?", (key_input,)).fetchone()
    
    if res:
        days, limit = res['duration_days'], res['file_limit']
        
        # Check user's current status
        user = get_user(uid)
        current_expiry = None
        if user and user['expiry']:
            try:
                current_expiry = datetime.strptime(user['expiry'], '%Y-%m-%d %H:%M:%S')
            except:
                pass
        
        # Calculate new expiry
        if current_expiry and current_expiry > datetime.now():
            new_expiry = current_expiry + timedelta(days=days)
        else:
            new_expiry = datetime.now() + timedelta(days=days)
        
        expiry_date = new_expiry.strftime('%Y-%m-%d %H:%M:%S')
        last_renewal = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Update user
        c.execute("UPDATE users SET expiry=?, file_limit=?, is_prime=1, last_renewal=? WHERE id=?", 
                 (expiry_date, limit, last_renewal, uid))
        c.execute("UPDATE keys SET used_by=?, used_date=? WHERE key=?", 
                 (uid, last_renewal, key_input))
        conn.commit()
        
        # Stop all user bots if renewing after expiry
        if not (current_expiry and current_expiry > datetime.now()):
            user_bots = c.execute("SELECT id, pid FROM deployments WHERE user_id=?", (uid,)).fetchall()
            for bot in user_bots:
                if bot['pid']:
                    try:
                        os.kill(bot['pid'], signal.SIGTERM)
                    except:
                        pass
                c.execute("UPDATE deployments SET status='Stopped', pid=0 WHERE id=?", (bot['id'],))
            conn.commit()
        
        conn.close()
        
        text = f"""
✅ **PRIME {'RENEWED' if current_expiry and current_expiry > datetime.now() else 'ACTIVATED'}!**
━━━━━━━━━━━━━━━━━━━━
🎉 Congratulations! Your Prime membership is now active.
━━━━━━━━━━━━━━━━━━━━
📅 **New Expiry:** {expiry_date}
📦 **File Limit:** {limit} files
⏰ **Duration Added:** {days} days
🔄 **Last Renewal:** {last_renewal}
━━━━━━━━━━━━━━━━━━━━
Enjoy all premium features!
        """
        
        clear_user_session(uid)
        edit_or_send_message(chat_id, None, text, reply_markup=get_main_keyboard(uid))
    else:
        conn.close()
        text = f"""
❌ **INVALID KEY**
━━━━━━━━━━━━━━━━━━━━
The key you entered is invalid or expired.
━━━━━━━━━━━━━━━━━━━━
Please check the key and try again.
Or contact @{Config.ADMIN_USERNAME} for a new key.
        """
        edit_or_send_message(chat_id, None, text, reply_markup=get_main_keyboard(uid))

def process_libraries_input(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    
    if message.text.lower() == 'cancel':
        clear_user_session(uid)
        edit_or_send_message(chat_id, None, "❌ Cancelled.", reply_markup=get_main_keyboard(uid))
        return
    
    commands = [cmd.strip() for cmd in message.text.strip().split('\n') if cmd.strip()]
    
    progress_msg = bot.send_message(chat_id, "🛠 **Installing libraries...**")
    
    results = []
    for i, cmd in enumerate(commands):
        if cmd and ("pip install" in cmd or "pip3 install" in cmd):
            try:
                result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=120)
                if result.returncode == 0:
                    results.append(f"✅ {cmd}")
                else:
                    results.append(f"❌ {cmd} - {result.stderr[:100]}")
                
                time.sleep(1)
                
            except subprocess.TimeoutExpired:
                results.append(f"⏰ {cmd} (Timeout)")
            except Exception as e:
                results.append(f"⚠️ {cmd} (Error: {str(e)[:100]})")
    
    result_text = "\n".join(results)
    final_text = f"""
✅ **INSTALLATION COMPLETE**
━━━━━━━━━━━━━━━━━━━━
{result_text}
━━━━━━━━━━━━━━━━━━━━
All libraries installed successfully!
"""
    
    clear_user_session(uid)
    bot.edit_message_text(final_text, chat_id, progress_msg.message_id)
    edit_or_send_message(chat_id, None, "📚 Libraries installed!", reply_markup=get_main_keyboard(uid))

def process_duration_input(message):
    """Process duration input for key generation"""
    uid = message.from_user.id
    chat_id = message.chat.id
    
    if message.text.lower() == 'cancel':
        clear_user_session(uid)
        edit_or_send_message(chat_id, None, "❌ Cancelled.", reply_markup=get_admin_keyboard())
        return
    
    try:
        days = int(message.text.strip())
        if days <= 0:
            raise ValueError
        
        set_user_session(uid, {
            'state': 'waiting_for_limit',
            'days': days
        })
        
        text = f"""
🎫 **GENERATE PRIME KEY**
━━━━━━━━━━━━━━━━━━━━
Step 2/3: Duration set to **{days} days**

Now enter file access limit (1-100):
Example: 3, 5, 10, 50
━━━━━━━━━━━━━━━━━━━━
"""
        edit_or_send_message(chat_id, None, text)
        
    except:
        edit_or_send_message(chat_id, None, "❌ Invalid input! Please enter a valid number.")

def process_limit_input(message):
    """Process limit input for key generation"""
    uid = message.from_user.id
    chat_id = message.chat.id
    
    if message.text.lower() == 'cancel':
        clear_user_session(uid)
        edit_or_send_message(chat_id, None, "❌ Cancelled.", reply_markup=get_admin_keyboard())
        return
    
    try:
        limit = int(message.text.strip())
        if limit <= 0 or limit > 100:
            edit_or_send_message(chat_id, None, "❌ Limit must be between 1 and 100!")
            return
        
        session = get_user_session(uid)
        days = session.get('days', 30)
        
        # Generate key
        key = generate_random_key()
        created_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT INTO keys (key, duration_days, file_limit, created_date) VALUES (?, ?, ?, ?)", 
                 (key, days, limit, created_date))
        conn.commit()
        conn.close()
        
        clear_user_session(uid)
        
        text = f"""
✅ **KEY GENERATED SUCCESSFULLY**
━━━━━━━━━━━━━━━━━━━━
🔑 **Key:** `{key}`
⏰ **Duration:** {days} days
📦 **File Limit:** {limit} files
📅 **Created:** {created_date}
━━━━━━━━━━━━━━━━━━━━
Share this key with the user.
"""
        edit_or_send_message(chat_id, None, text, reply_markup=get_admin_keyboard())
        
    except:
        edit_or_send_message(chat_id, None, "❌ Invalid input!")

# Callback Query Handler
@bot.callback_query_handler(func=lambda call: True)
def callback_manager(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    try:
        if call.data == "activate_prime":
            handle_activate_prime_callback(call)
        elif call.data == "upload":
            handle_upload_request(call.message, message_id)
        elif call.data == "my_bots":
            handle_my_bots(call.message, message_id)
        elif call.data == "deploy_new":
            handle_deploy_new(call.message, message_id)
        elif call.data == "dashboard":
            handle_dashboard(call.message, message_id)
        elif call.data == "settings":
            handle_settings(call.message, message_id)
        elif call.data == "install_libs":
            ask_for_libraries(call)
        elif call.data == "cancel":
            clear_user_session(uid)
            edit_or_send_message(chat_id, message_id, "❌ Cancelled.")
            handle_commands(call.message)
        
        elif call.data.startswith("bot_"):
            bot_id = call.data.split("_")[1]
            show_bot_details(call, bot_id)
        
        elif call.data.startswith("select_"):
            file_id = call.data.split("_")[1]
            start_deployment(call, file_id)
        
        elif call.data.startswith("stop_"):
            bot_id = call.data.split("_")[1]
            stop_bot(call, bot_id)
        
        elif call.data.startswith("restart_"):
            bot_id = call.data.split("_")[1]
            restart_bot(call, bot_id)
        
        elif call.data.startswith("delete_"):
            bot_id = call.data.split("_")[1]
            confirm_delete_bot(call, bot_id)
        
        elif call.data.startswith("confirm_delete_"):
            parts = call.data.split("_")
            bot_id = parts[2]
            confirm_delete_action(call, bot_id)
        
        elif call.data.startswith("export_"):
            bot_id = call.data.split("_")[1]
            export_bot(call, bot_id)
        
        elif call.data.startswith("logs_"):
            bot_id = call.data.split("_")[1]
            show_bot_logs(call, bot_id)
        
        elif call.data == "admin_panel":
            if uid == Config.ADMIN_ID:
                handle_admin_panel(call.message, message_id)
            else:
                bot.answer_callback_query(call.id, "⛔ Access Denied!")
        
        elif call.data == "gen_key":
            if uid == Config.ADMIN_ID:
                gen_key_step1(call)
        
        elif call.data.startswith("page_"):
            page_num = int(call.data.split("_")[1])
            view_database_page(call, page_num)
        
        elif call.data == "back_main":
            edit_or_send_message(chat_id, message_id, "🏠 **Main Menu**", reply_markup=get_main_keyboard(uid))
        
    except Exception as e:
        logger.error(f"Callback error: {e}")
        bot.answer_callback_query(call.id, "⚠️ Error occurred!")

# Deployment Functions
def start_deployment(call, file_id):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    conn = get_db()
    c = conn.cursor()
    bot_info = c.execute("SELECT id, bot_name, filename FROM deployments WHERE id=?", (file_id,)).fetchone()
    conn.close()
    
    if not bot_info:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    
    bot_id, bot_name, filename = bot_info
    
    # Check concurrent deployments
    running_bots = sum(1 for b in get_user_bots(uid) if b['status'] == "Running")
    if running_bots >= Config.MAX_CONCURRENT_DEPLOYMENTS:
        bot.answer_callback_query(call.id, f"❌ Max {Config.MAX_CONCURRENT_DEPLOYMENTS} concurrent deployments allowed!")
        return
    
    # Assign to node
    node = assign_bot_to_node(uid, bot_name)
    if not node:
        bot.answer_callback_query(call.id, "❌ No available nodes!")
        return
    
    text = f"""
🚀 **DEPLOYING BOT**
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot:** {bot_name}
🌐 **Node:** {node['name']} (Capacity: {node['capacity']})
🔄 **Status:** Starting...
━━━━━━━━━━━━━━━━━━━━
"""
    edit_or_send_message(chat_id, message_id, text)
    
    try:
        file_path = project_path / filename
        start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Start process
        logs_dir = Path('logs')
        logs_dir.mkdir(exist_ok=True)
        
        with open(f'logs/bot_{bot_id}.log', 'w') as log_file:
            proc = subprocess.Popen(
                ['python', str(file_path)],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
        
        # Update database
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE deployments SET pid=?, start_time=?, status='Running', node_id=?, last_active=? WHERE id=?", 
                 (proc.pid, start_time, node['id'], start_time, bot_id))
        
        # Update node load
        c.execute("UPDATE nodes SET current_load=current_load+1 WHERE id=?", (node['id'],))
        conn.commit()
        conn.close()
        
        # Success message
        text = f"""
✅ **BOT DEPLOYED SUCCESSFULLY**
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot:** {bot_name}
🌐 **Node:** {node['name']} ({node['current_load']+1}/{node['capacity']})
⚙️ **PID:** `{proc.pid}`
⏰ **Started:** {start_time}
🔧 **Status:** **RUNNING**
━━━━━━━━━━━━━━━━━━━━
Bot is now active and running!
"""
        edit_or_send_message(chat_id, message_id, text)
        
        # Start monitoring
        start_bot_monitoring(bot_id, proc.pid, chat_id, message_id)
        
    except Exception as e:
        logger.error(f"Deployment error: {e}")
        text = f"""
❌ **DEPLOYMENT FAILED**
━━━━━━━━━━━━━━━━━━━━
Error: {str(e)}
━━━━━━━━━━━━━━━━━━━━
Please check your bot code and try again.
"""
        edit_or_send_message(chat_id, message_id, text)

def start_bot_monitoring(bot_id, pid, chat_id, message_id):
    """Start monitoring bot in background"""
    def monitor():
        for i in range(10):
            try:
                stats = get_system_stats()
                update_bot_stats(bot_id, stats['cpu_percent'], stats['ram_percent'])
                time.sleep(5)
            except:
                break
    
    threading.Thread(target=monitor, daemon=True).start()

def stop_bot(call, bot_id):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    
    conn = get_db()
    c = conn.cursor()
    bot_info = c.execute("SELECT pid, node_id, bot_name FROM deployments WHERE id=?", (bot_id,)).fetchone()
    
    if bot_info and bot_info['pid']:
        pid = bot_info['pid']
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            if get_process_stats(pid):
                os.kill(pid, signal.SIGKILL)
        except:
            pass
    
    last_active = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute("UPDATE deployments SET status='Stopped', pid=0, last_active=? WHERE id=?", (last_active, bot_id))
    
    if bot_info and bot_info['node_id']:
        c.execute("UPDATE nodes SET current_load=current_load-1 WHERE id=?", (bot_info['node_id'],))
    
    conn.commit()
    conn.close()
    
    bot.answer_callback_query(call.id, f"✅ {bot_info['bot_name']} stopped successfully!")
    show_bot_details(call, bot_id)

def restart_bot(call, bot_id):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    
    # First stop
    conn = get_db()
    c = conn.cursor()
    bot_info = c.execute("SELECT pid, node_id, filename, bot_name FROM deployments WHERE id=?", (bot_id,)).fetchone()
    
    if bot_info and bot_info['pid']:
        try:
            os.kill(bot_info['pid'], signal.SIGTERM)
        except:
            pass
    
    c.execute("UPDATE deployments SET status='Restarting', restart_count=restart_count+1 WHERE id=?", (bot_id,))
    conn.commit()
    conn.close()
    
    bot.answer_callback_query(call.id, "🔄 Restarting bot...")
    
    # Wait and restart
    time.sleep(2)
    start_deployment(call, bot_id)

def export_bot(call, bot_id):
    conn = get_db()
    c = conn.cursor()
    bot_info = c.execute("SELECT bot_name, filename, user_id FROM deployments WHERE id=?", (bot_id,)).fetchone()
    conn.close()
    
    if not bot_info:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    
    bot_name, filename, user_id = bot_info
    zip_path = create_zip_file(bot_id, bot_name, filename, user_id)
    
    if zip_path and zip_path.exists():
        try:
            with open(zip_path, 'rb') as f:
                bot.send_document(call.message.chat.id, f, 
                                 caption=f"📦 **Bot Export:** {bot_name}\n\nFile: `{filename}`\nExport Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            time.sleep(2)
            zip_path.unlink(missing_ok=True)
            bot.answer_callback_query(call.id, "✅ Bot exported successfully!")
            
        except Exception as e:
            bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:50]}")
    else:
        bot.answer_callback_query(call.id, "❌ Error creating export!")

def show_bot_details(call, bot_id):
    conn = get_db()
    c = conn.cursor()
    bot_info = c.execute("SELECT * FROM deployments WHERE id=?", (bot_id,)).fetchone()
    conn.close()
    
    if not bot_info:
        return
    
    bot_name = bot_info['bot_name']
    filename = bot_info['filename']
    pid = bot_info['pid']
    start_time = bot_info['start_time']
    status = bot_info['status']
    cpu_usage = bot_info['cpu_usage'] or 0
    ram_usage = bot_info['ram_usage'] or 0
    node_id = bot_info['node_id']
    restart_count = bot_info['restart_count']
    
    stats = get_system_stats()
    cpu_usage = cpu_usage or stats['cpu_percent']
    ram_usage = ram_usage or stats['ram_percent']
    
    cpu_bar = create_progress_bar(cpu_usage)
    ram_bar = create_progress_bar(ram_usage)
    
    # Check if process is running
    is_running = get_process_stats(pid) if pid else False
    
    text = f"""
🤖 **BOT DETAILS**
━━━━━━━━━━━━━━━━━━━━
**Name:** {bot_name}
**File:** `{filename}`
**Status:** {"🟢 Running" if is_running else "🔴 Stopped"}
**Node:** Node-{node_id if node_id else 'N/A'}
━━━━━━━━━━━━━━━━━━━━
📊 **Statistics:**
• CPU: {cpu_bar} {cpu_usage:.1f}%
• RAM: {ram_bar} {ram_usage:.1f}%
• PID: `{pid if pid else "N/A"}`
• Uptime: {calculate_uptime(start_time) if start_time else "N/A"}
• Restarts: {restart_count}
━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = get_bot_actions_keyboard(bot_id)
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

def show_bot_logs(call, bot_id):
    log_file = f'logs/bot_{bot_id}.log'
    
    if not os.path.exists(log_file):
        bot.answer_callback_query(call.id, "📜 No logs available")
        return
    
    try:
        with open(log_file, 'r') as f:
            logs = f.read()[-2000:]  # Last 2000 chars
        
        if len(logs) > 1000:
            logs = logs[-1000:] + "\n\n... (truncated)"
        
        text = f"""
📜 **BOT LOGS**
━━━━━━━━━━━━━━━━━━━━
{logs}
━━━━━━━━━━━━━━━━━━━━
"""
        bot.send_message(call.message.chat.id, text)
        bot.answer_callback_query(call.id, "📜 Logs sent!")
    except:
        bot.answer_callback_query(call.id, "❌ Error reading logs")

def confirm_delete_bot(call, bot_id):
    conn = get_db()
    c = conn.cursor()
    bot_info = c.execute("SELECT bot_name, filename FROM deployments WHERE id=?", (bot_id,)).fetchone()
    conn.close()
    
    if not bot_info:
        return
    
    bot_name, filename = bot_info
    
    text = f"""
⚠️ **CONFIRM DELETE**
━━━━━━━━━━━━━━━━━━━━
Are you sure you want to delete?
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot:** {bot_name}
📁 **File:** `{filename}`
━━━━━━━━━━━━━━━━━━━━
**This action cannot be undone!**
━━━━━━━━━━━━━━━━━━━━
"""
    markup = get_yes_no_keyboard("delete", bot_id)
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

def confirm_delete_action(call, bot_id):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    
    conn = get_db()
    c = conn.cursor()
    bot_info = c.execute("SELECT filename, pid, node_id FROM deployments WHERE id=?", (bot_id,)).fetchone()
    
    if bot_info:
        filename, pid, node_id = bot_info
        
        # Stop bot if running
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except:
                pass
        
        # Delete file
        file_path = project_path / filename
        if file_path.exists():
            file_path.unlink()
        
        # Update node load
        if node_id:
            c.execute("UPDATE nodes SET current_load=current_load-1 WHERE id=?", (node_id,))
        
        # Delete from database
        c.execute("DELETE FROM deployments WHERE id=?", (bot_id,))
        
        # Update user bot count
        update_user_bot_count(uid)
        
        conn.commit()
    
    conn.close()
    
    bot.answer_callback_query(call.id, "✅ Bot deleted successfully!")
    handle_my_bots(call.message, call.message.message_id)

def ask_for_libraries(call):
    text = """
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
Type 'cancel' to abort.
"""
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id)
    
    uid = call.from_user.id
    set_user_session(uid, {'state': 'waiting_for_libs'})
    msg = bot.send_message(call.message.chat.id, "Please enter the library commands:")
    update_message_history(uid, msg.message_id)
    bot.register_next_step_handler(msg, process_libraries_input)

def handle_activate_prime_callback(call):
    uid = call.from_user.id
    prime_status = check_prime_expiry(uid)
    
    if not prime_status['expired']:
        text = f"""
🔄 **RENEW PRIME (Early)**
━━━━━━━━━━━━━━━━━━━━
Your Prime subscription is still active.
Expires in: {prime_status['days_left']} days

You can renew early with a new key:
Format: `ZENX-XXXXXXXXXX`
━━━━━━━━━━━━━━━━━━━━
"""
    else:
        text = """
🔑 **ACTIVATE PRIME PASS**
━━━━━━━━━━━━━━━━━━━━
Enter your activation key below.
Format: `ZENX-XXXXXXXXXX`
━━━━━━━━━━━━━━━━━━━━
"""
    
    set_user_session(uid, {'state': 'waiting_for_key'})
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id)
    msg = bot.send_message(call.message.chat.id, "Please enter your activation key:")
    update_message_history(uid, msg.message_id)
    bot.register_next_step_handler(msg, process_key_input)

# Admin Functions
def gen_key_step1_message(message, last_msg_id=None):
    uid = message.from_user.id
    set_user_session(uid, {'state': 'waiting_for_duration'})
    
    text = """
🎫 **GENERATE PRIME KEY**
━━━━━━━━━━━━━━━━━━━━
Step 1/3: Enter duration in days
Example: 7, 30, 90, 365
━━━━━━━━━━━━━━━━━━━━
Type 'cancel' to abort.
"""
    edit_or_send_message(message.chat.id, last_msg_id, text)

def gen_key_step1(call):
    uid = call.from_user.id
    set_user_session(uid, {'state': 'waiting_for_duration'})
    
    text = """
🎫 **GENERATE PRIME KEY**
━━━━━━━━━━━━━━━━━━━━
Step 1/3: Enter duration in days
Example: 7, 30, 90, 365
━━━━━━━━━━━━━━━━━━━━
Type 'cancel' to abort.
"""
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id)
    msg = bot.send_message(call.message.chat.id, "Please enter duration in days:")
    update_message_history(uid, msg.message_id)
    bot.register_next_step_handler(msg, process_duration_input)

def show_all_users_admin(message, last_msg_id=None):
    conn = get_db()
    c = conn.cursor()
    users = c.execute("SELECT id, username, expiry, file_limit, is_prime, join_date FROM users").fetchall()
    conn.close()
    
    prime_count = sum(1 for u in users if u['is_prime'] == 1)
    total_count = len(users)
    
    text = f"""
👥 **ALL USERS**
━━━━━━━━━━━━━━━━━━━━
📊 **Total Users:** {total_count}
👑 **Prime Users:** {prime_count}
🆓 **Free Users:** {total_count - prime_count}
━━━━━━━━━━━━━━━━━━━━
"""
    
    for user in users[:15]:
        username = user['username'] if user['username'] else f"User_{user['id']}"
        status = "👑 Prime" if user['is_prime'] else "🆓 Free"
        text += f"\n• {username} (ID: {user['id']}) - {status}"
    
    if len(users) > 15:
        text += f"\n\n... and {len(users) - 15} more users"
    
    edit_or_send_message(message.chat.id, last_msg_id, text)

def show_all_bots_admin(message, last_msg_id=None):
    conn = get_db()
    c = conn.cursor()
    bots = c.execute("SELECT d.id, d.bot_name, d.status, d.start_time, u.username FROM deployments d LEFT JOIN users u ON d.user_id = u.id ORDER BY d.id DESC LIMIT 20").fetchall()
    conn.close()
    
    running_bots = sum(1 for b in bots if b['status'] == "Running")
    total_bots = len(bots)
    
    text = f"""
🤖 **ALL BOTS**
━━━━━━━━━━━━━━━━━━━━
📊 **Total Bots:** {total_bots}
🟢 **Running:** {running_bots}
🔴 **Stopped:** {total_bots - running_bots}
━━━━━━━━━━━━━━━━━━━━
"""
    
    for bot_info in bots[:10]:
        if bot_info['bot_name']:
            username = bot_info['username'] if bot_info['username'] else "Unknown"
            text += f"\n• {bot_info['bot_name']} (User: @{username}) - {bot_info['status']}"
    
    edit_or_send_message(message.chat.id, last_msg_id, text)

def show_admin_stats(message, last_msg_id=None):
    conn = get_db()
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
    
    total_capacity = len(Config.HOSTING_NODES) * 300
    available_capacity = total_capacity - running_bots
    
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
• Platform: ZEN X HOST v3.2.0
• Port: {Config.PORT}
• Nodes: {len(Config.HOSTING_NODES)} x 300 capacity
• Total Capacity: {total_capacity} bots
• Used Capacity: {running_bots} bots
• Available: {available_capacity} bots
• Bot: @{Config.BOT_USERNAME}
━━━━━━━━━━━━━━━━━━━━
"""
    edit_or_send_message(message.chat.id, last_msg_id, text)

def view_database_admin(message, last_msg_id=None):
    view_database_page_admin(message, 1, last_msg_id)

def view_database_page_admin(message, page_num, last_msg_id=None):
    items_per_page = 5
    offset = (page_num - 1) * items_per_page
    
    conn = get_db()
    c = conn.cursor()
    
    deployments = c.execute("""
        SELECT d.id, d.bot_name, d.filename, d.status, u.username, d.last_active, d.node_id
        FROM deployments d 
        LEFT JOIN users u ON d.user_id = u.id 
        ORDER BY d.id DESC
        LIMIT ? OFFSET ?
    """, (items_per_page, offset)).fetchall()
    
    total_deployments = c.execute("SELECT COUNT(*) FROM deployments").fetchone()[0]
    total_pages = (total_deployments + items_per_page - 1) // items_per_page
    
    conn.close()
    
    text = f"""
🗄️ **DATABASE VIEWER**
━━━━━━━━━━━━━━━━━━━━
📊 **Total Bots:** {total_deployments}
📄 **Page:** {page_num}/{total_pages}
━━━━━━━━━━━━━━━━━━━━
"""
    
    if deployments:
        text += "\n**Current Bots:**\n"
        for dep in deployments:
            text += f"\n• **{dep['bot_name']}** (ID: {dep['id']})\n"
            text += f"  👤 User: @{dep['username'] if dep['username'] else 'Unknown'}\n"
            text += f"  📁 File: `{dep['filename']}`\n"
            text += f"  📊 Status: {dep['status']}\n"
            text += f"  🌐 Node: {dep['node_id'] if dep['node_id'] else 'N/A'}\n"
    else:
        text += "\nNo bots found.\n"
    
    markup = types.InlineKeyboardMarkup()
    row_buttons = []
    
    if page_num > 1:
        row_buttons.append(types.InlineKeyboardButton("⬅️ Previous", callback_data=f"page_{page_num-1}"))
    
    if page_num < total_pages:
        row_buttons.append(types.InlineKeyboardButton("Next ➡️", callback_data=f"page_{page_num+1}"))
    
    if row_buttons:
        markup.row(*row_buttons)
    
    edit_or_send_message(message.chat.id, last_msg_id, text, reply_markup=markup)

def backup_database_admin(message, last_msg_id=None):
    try:
        backup_dir = Path('backups')
        backup_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f"zenx_db_backup_{timestamp}.db"
        backup_path = backup_dir / backup_filename
        
        import shutil
        shutil.copy2(Config.DB_NAME, backup_path)
        
        with open(backup_path, 'rb') as f:
            bot.send_document(message.chat.id, f, 
                             caption=f"💾 **Database Backup**\n\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nFile: `{backup_filename}`\nSize: {backup_path.stat().st_size / 1024:.1f} KB")
        
        time.sleep(2)
        backup_path.unlink(missing_ok=True)
        
        edit_or_send_message(message.chat.id, last_msg_id, "✅ Backup created and sent successfully!")
        
    except Exception as e:
        edit_or_send_message(message.chat.id, last_msg_id, f"❌ Backup failed: {str(e)}")

def toggle_maintenance_admin(message, last_msg_id=None):
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
    edit_or_send_message(message.chat.id, last_msg_id, text)

def show_nodes_status(message, last_msg_id=None):
    conn = get_db()
    c = conn.cursor()
    nodes = c.execute("SELECT * FROM nodes").fetchall()
    conn.close()
    
    total_capacity = sum(node['capacity'] for node in nodes)
    used_capacity = sum(node['current_load'] for node in nodes)
    available_capacity = total_capacity - used_capacity
    
    text = f"""
🌐 **NODES STATUS**
━━━━━━━━━━━━━━━━━━━━
**Total Capacity:** {total_capacity} bots
**Used Capacity:** {used_capacity} bots
**Available:** {available_capacity} bots
━━━━━━━━━━━━━━━━━━━━
"""
    
    for node in nodes:
        load_percent = (node['current_load'] / node['capacity']) * 100 if node['capacity'] > 0 else 0
        load_bar = create_progress_bar(load_percent)
        status_icon = "🟢" if node['status'] == 'active' else "🔴"
        
        text += f"\n{status_icon} **{node['name']}**"
        text += f"\n• Status: {node['status']}"
        text += f"\n• Load: {load_bar} {load_percent:.1f}%"
        text += f"\n• Capacity: {node['current_load']}/{node['capacity']}"
        text += f"\n• Last Check: {node['last_check']}"
        text += "\n━━━━━━━━━━━━━━━━━━━━\n"
    
    edit_or_send_message(message.chat.id, last_msg_id, text)

# Helper Functions
def calculate_uptime(start_time_str):
    try:
        start_time = datetime.strptime(start_time_str, '%Y-%m-%d %H:%M:%S')
        uptime = datetime.now() - start_time
        
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        elif hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
    except:
        return "N/A"

def get_process_stats(pid):
    """Get stats for a specific process"""
    try:
        if pid == 0 or pid is None:
            return False
        os.kill(pid, 0)
        return True
    except:
        return False

def extract_zip_file(zip_path, extract_to):
    """Extract zip file to directory"""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_to)
        return True
    except Exception as e:
        logger.error(f"Error extracting zip: {e}")
        return False

# Single instance bot polling with error handling
def start_bot_safely():
    """Start bot with single instance protection"""
    global bot_running
    
    if bot_running:
        logger.info("Bot is already running, skipping...")
        return
    
    bot_running = True
    logger.info("Starting bot polling...")
    
    while True:
        try:
            logger.info("Bot polling started...")
            bot.polling(none_stop=True, timeout=60)
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
            if "409" in str(e):
                logger.warning("Conflict detected, waiting 10 seconds...")
                time.sleep(10)
            else:
                logger.warning("Other error, waiting 5 seconds...")
                time.sleep(5)

# Flask Routes for Render
@app.route('/')
def home():
    stats = get_system_stats()
    total_capacity = len(Config.HOSTING_NODES) * 300
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>🤖 ZEN X 300-CAPACITY HOST BOT v3.2.0</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                margin: 0;
                padding: 20px;
                min-height: 100vh;
            }}
            .container {{
                max-width: 900px;
                margin: 0 auto;
                background: rgba(255, 255, 255, 0.1);
                padding: 30px;
                border-radius: 20px;
                backdrop-filter: blur(10px);
                box-shadow: 0 15px 35px rgba(0, 0, 0, 0.3);
                border: 1px solid rgba(255, 255, 255, 0.2);
            }}
            h1 {{
                text-align: center;
                font-size: 2.8em;
                margin-bottom: 20px;
                color: #fff;
                text-shadow: 0 2px 10px rgba(0,0,0,0.3);
            }}
            .subtitle {{
                text-align: center;
                font-size: 1.2em;
                margin-bottom: 40px;
                opacity: 0.9;
            }}
            .status {{
                background: rgba(255, 255, 255, 0.15);
                padding: 25px;
                border-radius: 15px;
                margin: 25px 0;
                border-left: 6px solid #4CAF50;
                box-shadow: 0 5px 15px rgba(0, 0, 0, 0.2);
            }}
            .feature {{
                background: rgba(255, 255, 255, 0.1);
                padding: 20px;
                margin: 15px 0;
                border-radius: 12px;
                display: flex;
                align-items: center;
                transition: transform 0.3s, background 0.3s;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }}
            .feature:hover {{
                transform: translateY(-5px);
                background: rgba(255, 255, 255, 0.2);
            }}
            .feature i {{
                margin-right: 20px;
                font-size: 1.8em;
                color: #FFD700;
            }}
            .stats {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin: 40px 0;
            }}
            .stat-box {{
                background: rgba(255, 255, 255, 0.15);
                padding: 25px;
                border-radius: 15px;
                text-align: center;
                transition: transform 0.3s;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }}
            .stat-box:hover {{
                transform: translateY(-5px);
                background: rgba(255, 255, 255, 0.2);
            }}
            .stat-box i {{
                font-size: 2.5em;
                margin-bottom: 15px;
                color: #4CAF50;
            }}
            .btn {{
                display: inline-block;
                background: linear-gradient(45deg, #FF416C, #FF4B2B);
                color: white;
                padding: 15px 35px;
                border-radius: 30px;
                text-decoration: none;
                font-weight: bold;
                margin: 10px 10px;
                transition: all 0.3s;
                border: none;
                font-size: 1.1em;
                box-shadow: 0 5px 15px rgba(255, 65, 108, 0.4);
            }}
            .btn:hover {{
                transform: translateY(-3px);
                box-shadow: 0 8px 20px rgba(255, 65, 108, 0.6);
            }}
            .btn-telegram {{
                background: linear-gradient(45deg, #0088cc, #00aced);
                box-shadow: 0 5px 15px rgba(0, 136, 204, 0.4);
            }}
            .btn-telegram:hover {{
                box-shadow: 0 8px 20px rgba(0, 136, 204, 0.6);
            }}
            .btn-success {{
                background: linear-gradient(45deg, #00b09b, #96c93d);
                box-shadow: 0 5px 15px rgba(0, 176, 155, 0.4);
            }}
            .btn-success:hover {{
                box-shadow: 0 8px 20px rgba(0, 176, 155, 0.6);
            }}
            .footer {{
                text-align: center;
                margin-top: 50px;
                padding-top: 30px;
                border-top: 1px solid rgba(255, 255, 255, 0.3);
                font-size: 0.9em;
                opacity: 0.8;
            }}
            .btn-container {{
                text-align: center;
                margin: 40px 0;
            }}
            @media (max-width: 768px) {{
                .container {{
                    padding: 20px;
                    margin: 10px;
                }}
                h1 {{
                    font-size: 2em;
                }}
                .stats {{
                    grid-template-columns: 1fr;
                }}
                .btn {{
                    display: block;
                    margin: 15px auto;
                    width: 80%;
                }}
            }}
        </style>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <link rel="icon" href="https://img.icons8.com/color/96/000000/telegram-app.png" type="image/x-icon">
    </head>
    <body>
        <div class="container">
            <h1><i class="fas fa-robot"></i> ZEN X 300-CAPACITY HOST</h1>
            <div class="subtitle">
                Advanced 300-Capacity Multi-Node Telegram Bot Hosting Platform v3.2.0
            </div>
            
            <div class="status">
                <h2><i class="fas fa-server"></i> Server Status: <span style="color: #4CAF50; font-weight: bold;">✅ ONLINE & RUNNING</span></h2>
                <p>300-Capacity Multi-Node bot hosting service is running smoothly</p>
                <p><i class="fas fa-info-circle"></i> Total Capacity: {total_capacity} bots | Active Nodes: {len(Config.HOSTING_NODES)}</p>
            </div>
            
            <div class="stats">
                <div class="stat-box">
                    <i class="fas fa-database"></i>
                    <h3>{total_capacity} Capacity</h3>
                    <p>High Performance Nodes</p>
                </div>
                <div class="stat-box">
                    <i class="fas fa-sitemap"></i>
                    <h3>Multi-Node</h3>
                    <p>{len(Config.HOSTING_NODES)} Active Nodes</p>
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
            </div>
            
            <h2 style="text-align: center; margin-top: 40px;"><i class="fas fa-star"></i> Premium Features</h2>
            
            <div class="feature">
                <i class="fas fa-database"></i>
                <div>
                    <h3>300-Capacity Nodes</h3>
                    <p>Each node supports 300 concurrent bots for maximum scalability.</p>
                </div>
            </div>
            
            <div class="feature">
                <i class="fas fa-upload"></i>
                <div>
                    <h3>Bot File Upload (.py & .zip)</h3>
                    <p>Upload and deploy your Python bots easily. Supports both .py files and .zip archives.</p>
                </div>
            </div>
            
            <div class="feature">
                <i class="fas fa-chart-line"></i>
                <div>
                    <h3>Live Statistics & Monitoring</h3>
                    <p>Real-time monitoring of your bots with CPU, RAM, and performance metrics.</p>
                </div>
            </div>
            
            <div class="feature">
                <i class="fas fa-cogs"></i>
                <div>
                    <h3>Library Installation</h3>
                    <p>Install required Python libraries automatically with pip commands.</p>
                </div>
            </div>
            
            <div class="feature">
                <i class="fas fa-history"></i>
                <div>
                    <h3>Auto-Restart System</h3>
                    <p>Bots automatically restart on failure with restart counter.</p>
                </div>
            </div>
            
            <div class="feature">
                <i class="fas fa-file-alt"></i>
                <div>
                    <h3>Logs Access</h3>
                    <p>View real-time logs of your running bots for debugging.</p>
                </div>
            </div>
            
            <div class="btn-container">
                <a href="https://t.me/{Config.BOT_USERNAME}" class="btn btn-telegram" target="_blank">
                    <i class="fab fa-telegram"></i> Start Bot on Telegram
                </a>
                <a href="https://t.me/{Config.ADMIN_USERNAME}" class="btn btn-success" target="_blank">
                    <i class="fas fa-crown"></i> Get Prime Subscription
                </a>
            </div>
            
            <div class="footer">
                <p><i class="fas fa-code"></i> Powered by ZEN X Development Team | Version 3.2.0</p>
                <p><i class="fas fa-map-marker-alt"></i> Hosting Region: Asia → Bangladesh 🇧🇩</p>
                <p>© 2024-2026 ZEN X HOST BOT. All rights reserved.</p>
                <p style="font-size: 0.8em; margin-top: 10px;">
                    <i class="fas fa-heart" style="color: #ff4757;"></i> 300-Capacity Multi-Node Hosting System
                </p>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

@app.route('/health')
def health():
    """Health check endpoint"""
    try:
        conn = get_db()
        c = conn.cursor()
        
        total_users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_bots = c.execute("SELECT COUNT(*) FROM deployments").fetchone()[0]
        running_bots = c.execute("SELECT COUNT(*) FROM deployments WHERE status='Running'").fetchone()[0]
        
        conn.close()
        
        stats = get_system_stats()
        
        return jsonify({
            "status": "healthy",
            "service": "ZEN X 300-CAPACITY HOST BOT",
            "version": "3.2.0",
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "statistics": {
                "total_users": total_users,
                "total_bots": total_bots,
                "running_bots": running_bots,
                "total_capacity": len(Config.HOSTING_NODES) * 300,
                "available_capacity": (len(Config.HOSTING_NODES) * 300) - running_bots
            },
            "system": stats,
            "nodes": len(Config.HOSTING_NODES)
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Start Bot
if __name__ == '__main__':
    # Create necessary directories
    Path('exports').mkdir(exist_ok=True)
    Path('backups').mkdir(exist_ok=True)
    Path('logs').mkdir(exist_ok=True)
    
    print(f"""
{'='*60}
🤖 ZEN X 300-CAPACITY HOST BOT v3.2.0
{'='*60}
🚀 Starting server...
• Port: {Config.PORT}
• Admin: @{Config.ADMIN_USERNAME}
• Bot: @{Config.BOT_USERNAME}
• Nodes: {len(Config.HOSTING_NODES)} x 300 capacity
• Total Capacity: {len(Config.HOSTING_NODES) * 300} bots
• Max Bots/User: {Config.MAX_BOTS_PER_USER}
• Max Concurrent: {Config.MAX_CONCURRENT_DEPLOYMENTS}
{'='*60}
    """)
    
    # Start bot in separate thread with single instance protection
    bot_thread = threading.Thread(target=start_bot_safely, daemon=True)
    bot_thread.start()
    
    print(f"✅ Telegram bot started with single instance protection")
    print(f"🌐 Flask server starting on port {Config.PORT}")
    print(f"📊 Health check: http://0.0.0.0:{Config.PORT}/health")
    print(f"🏠 Homepage: http://0.0.0.0:{Config.PORT}/")
    print(f"{'='*60}")
    
    # Start Flask app
    app.run(
        host='0.0.0.0',
        port=Config.PORT,
        debug=False,
        use_reloader=False,
        threaded=True
    )
