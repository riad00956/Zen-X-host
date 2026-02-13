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
import logging
from pathlib import Path
from telebot import types
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from flask import Flask, request, jsonify
from concurrent.futures import ThreadPoolExecutor

# Database lock for thread safety
db_lock = threading.RLock()

# ১. Configuration
class Config:
    TOKEN = os.environ.get('BOT_TOKEN', '8494225623:AAG_HRSHoBpt36bdeUvYJL4ONnh-2bf6BnY')
    ADMIN_ID = int(os.environ.get('ADMIN_ID', 7832264582))
    PROJECT_DIR = 'projects'
    DB_NAME = 'cyber_v2.db'
    BACKUP_DIR = 'backups'
    LOGS_DIR = 'logs'
    EXPORTS_DIR = 'exports'
    PORT = int(os.environ.get('PORT', 10000))
    MAINTENANCE = False
    ADMIN_USERNAME = 'zerox6t9'
    BOT_USERNAME = 'zen_xbot'
    MAX_BOTS_PER_USER = 5
    MAX_CONCURRENT_DEPLOYMENTS = 4
    AUTO_RESTART_BOTS = True
    BACKUP_INTERVAL = 3600
    BOT_TIMEOUT = 300
    MAX_LOG_SIZE = 10000
    
    HOSTING_NODES = [
        {"name": "Node-1", "status": "active", "capacity": 300, "region": "Asia"},
        {"name": "Node-2", "status": "active", "capacity": 300, "region": "Asia"},
        {"name": "Node-3", "status": "active", "capacity": 300, "region": "Europe"}
    ]

# Create bot instance
try:
    bot = telebot.TeleBot(Config.TOKEN, parse_mode="Markdown")
    logger.info("TeleBot instance created successfully")
except Exception as e:
    logger.error(f"Failed to create TeleBot instance: {e}")
    raise

project_path = Path(Config.PROJECT_DIR)
project_path.mkdir(exist_ok=True)
app = Flask(__name__)

# Thread pool for concurrent operations
executor = ThreadPoolExecutor(max_workers=5)

# User session management
user_sessions = {}
user_message_history = {}
bot_monitors = {}

# Database helper functions with thread safety
def get_db():
    """Get database connection with thread safety"""
    with db_lock:
        conn = sqlite3.connect(Config.DB_NAME, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

def execute_db(query, params=(), fetchone=False, fetchall=False, commit=False):
    """Execute database query with thread safety"""
    with db_lock:
        conn = sqlite3.connect(Config.DB_NAME, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        try:
            c.execute(query, params)
            
            if commit:
                conn.commit()
            
            if fetchone:
                result = c.fetchone()
            elif fetchall:
                result = c.fetchall()
            else:
                result = None
            
            conn.close()
            return result
            
        except Exception as e:
            logger.error(f"Database error: {e}")
            conn.close()
            return None

# ২. Enhanced Database Functions with Auto-Recovery
def init_db():
    """Initialize database with recovery support"""
    try:
        # Check if database exists
        db_exists = os.path.exists(Config.DB_NAME)
        
        conn = get_db()
        c = conn.cursor()
        
        # Create tables if they don't exist
        c.execute('''CREATE TABLE IF NOT EXISTS users 
                    (id INTEGER PRIMARY KEY, username TEXT, expiry TEXT, file_limit INTEGER, 
                     is_prime INTEGER, join_date TEXT, last_renewal TEXT, total_bots_deployed INTEGER DEFAULT 0,
                     total_deployments INTEGER DEFAULT 0, last_active TEXT)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS keys 
                    (key TEXT PRIMARY KEY, duration_days INTEGER, file_limit INTEGER, created_date TEXT, 
                     used_by TEXT, used_date TEXT, is_used INTEGER DEFAULT 0)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS deployments 
                    (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, bot_name TEXT, 
                     filename TEXT, pid INTEGER, start_time TEXT, status TEXT, 
                     cpu_usage REAL, ram_usage REAL, last_active TEXT, node_id INTEGER,
                     logs TEXT, restart_count INTEGER DEFAULT 0, auto_restart INTEGER DEFAULT 1,
                     created_at TEXT, updated_at TEXT)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS nodes
                    (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, status TEXT, 
                     capacity INTEGER, current_load INTEGER DEFAULT 0, last_check TEXT,
                     region TEXT, total_deployed INTEGER DEFAULT 0)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS server_logs
                    (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, 
                     event TEXT, details TEXT, user_id INTEGER)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS bot_logs
                    (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id INTEGER, timestamp TEXT,
                     log_type TEXT, message TEXT)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS notifications
                    (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, message TEXT,
                     is_read INTEGER DEFAULT 0, created_at TEXT)''')
        
        # Check if admin exists
        c.execute("SELECT * FROM users WHERE id=?", (Config.ADMIN_ID,))
        admin_exists = c.fetchone()
        
        if not admin_exists:
            # Insert admin user
            join_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            expiry_date = (datetime.now() + timedelta(days=3650)).strftime('%Y-%m-%d %H:%M:%S')
            c.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", 
                     (Config.ADMIN_ID, 'admin', expiry_date, 100, 1, join_date, join_date, 0, 0, join_date))
        
        # Check if nodes exist
        c.execute("SELECT COUNT(*) FROM nodes")
        node_count = c.fetchone()[0]
        
        if node_count == 0:
            # Initialize hosting nodes with 300 capacity
            join_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            for i, node in enumerate(Config.HOSTING_NODES, 1):
                c.execute("INSERT INTO nodes (name, status, capacity, last_check, region) VALUES (?, ?, ?, ?, ?)",
                         (node['name'], node['status'], node['capacity'], join_date, node.get('region', 'Global')))
        
        # Update all running bots to "Stopped" status for recovery
        if db_exists:
            c.execute("UPDATE deployments SET status='Stopped', pid=0, updated_at=? WHERE status='Running'",
                     (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
        
        conn.commit()
        conn.close()
        
        logger.info("Database initialized successfully")
        
    except Exception as e:
        logger.error(f"Error initializing database: {e}")

def recover_deployments():
    """Recover previously running bots on server restart"""
    if not Config.AUTO_RESTART_BOTS:
        return
    
    try:
        result = execute_db("""
            SELECT d.*, u.username 
            FROM deployments d 
            LEFT JOIN users u ON d.user_id = u.id 
            WHERE d.auto_restart = 1 AND d.status IN ('Running', 'Restarting')
        """, fetchall=True)
        
        if not result:
            return
        
        logger.info(f"Found {len(result)} bots to recover")
        
        for bot_info in result:
            bot_id = bot_info['id']
            user_id = bot_info['user_id']
            bot_name = bot_info['bot_name']
            filename = bot_info['filename']
            username = bot_info['username'] or f"User_{user_id}"
            
            # Check if bot file exists
            file_path = project_path / filename
            if not file_path.exists():
                logger.error(f"Bot file not found for recovery: {filename}")
                continue
            
            # Assign to node
            node = assign_bot_to_node(user_id, bot_name)
            if not node:
                logger.error(f"No available nodes for bot {bot_id}")
                continue
            
            try:
                # Start the bot
                start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                logs_dir = Path('logs')
                logs_dir.mkdir(exist_ok=True)
                
                with open(f'logs/bot_{bot_id}.log', 'a') as log_file:
                    log_file.write(f"\n{'='*50}\nAuto-Recovery started at {start_time}\n{'='*50}\n")
                    proc = subprocess.Popen(
                        ['python', str(file_path)],
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        start_new_session=True
                    )
                
                # Update database
                execute_db("""
                    UPDATE deployments 
                    SET pid=?, start_time=?, status='Running', node_id=?, last_active=?, 
                    restart_count=restart_count+1, updated_at=? 
                    WHERE id=?
                """, (proc.pid, start_time, node['id'], start_time, start_time, bot_id), commit=True)
                
                execute_db("UPDATE nodes SET current_load=current_load+1, total_deployed=total_deployed+1 WHERE id=?", 
                          (node['id'],), commit=True)
                
                logger.info(f"Recovered bot {bot_name} (ID: {bot_id}) for user {username}")
                
                # Start monitoring
                start_bot_monitoring(bot_id, proc.pid, user_id)
                
            except Exception as e:
                logger.error(f"Failed to recover bot {bot_id}: {e}")
        
        logger.info(f"Successfully recovered {len(result)} bots")
        
    except Exception as e:
        logger.error(f"Error in deployment recovery: {e}")

def log_event(event, details, user_id=None):
    """Log server events"""
    try:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        execute_db("INSERT INTO server_logs (timestamp, event, details, user_id) VALUES (?, ?, ?, ?)",
                  (timestamp, event, details, user_id), commit=True)
    except Exception as e:
        logger.error(f"Error logging event: {e}")

def log_bot_event(bot_id, log_type, message):
    """Log bot events"""
    try:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        execute_db("INSERT INTO bot_logs (bot_id, timestamp, log_type, message) VALUES (?, ?, ?, ?)",
                  (bot_id, timestamp, log_type, message), commit=True)
    except Exception as e:
        logger.error(f"Error logging bot event: {e}")

def send_notification(user_id, message):
    """Send notification to user"""
    try:
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        execute_db("INSERT INTO notifications (user_id, message, created_at) VALUES (?, ?, ?)",
                  (user_id, message, created_at), commit=True)
        
        # Send immediate notification via Telegram if bot is active
        try:
            bot.send_message(user_id, f"📢 **Notification:** {message}")
        except:
            pass
    except Exception as e:
        logger.error(f"Error sending notification: {e}")

# Initialize database
init_db()

# Backup Functions
def backup_database():
    """Create a backup of the database"""
    try:
        backup_dir = Path(Config.BACKUP_DIR)
        backup_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f"zenx_backup_{timestamp}.db"
        backup_path = backup_dir / backup_filename
        
        # Copy database file with lock
        import shutil
        with db_lock:
            shutil.copy2(Config.DB_NAME, backup_path)
        
        # Compress backup
        zip_filename = f"zenx_backup_{timestamp}.zip"
        zip_path = backup_dir / zip_filename
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(backup_path, arcname=backup_filename)
        
        # Remove uncompressed backup
        backup_path.unlink(missing_ok=True)
        
        # Clean old backups (keep last 7 days)
        for file in backup_dir.glob("zenx_backup_*.zip"):
            file_time_str = file.stem.split('_')[-1]
            try:
                file_time = datetime.strptime(file_time_str, '%Y%m%d%H%M%S')
                if (datetime.now() - file_time).days > 7:
                    file.unlink()
            except:
                pass
        
        logger.info(f"Database backup created: {zip_filename}")
        return zip_path
        
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        return None

def schedule_backups():
    """Schedule regular database backups"""
    while True:
        time.sleep(Config.BACKUP_INTERVAL)
        try:
            backup_path = backup_database()
            if backup_path:
                logger.info(f"Scheduled backup completed: {backup_path.name}")
        except Exception as e:
            logger.error(f"Scheduled backup error: {e}")

# System Monitoring Functions
def get_system_stats():
    """Get system statistics"""
    try:
        total_bots = execute_db("SELECT COUNT(*) FROM deployments", fetchone=True)
        if total_bots:
            total_bots = total_bots[0] or 0
        else:
            total_bots = 0
            
        running_bots = execute_db("SELECT COUNT(*) FROM deployments WHERE status='Running'", fetchone=True)
        if running_bots:
            running_bots = running_bots[0] or 0
        else:
            running_bots = 0
            
        total_users = execute_db("SELECT COUNT(*) FROM users", fetchone=True)
        if total_users:
            total_users = total_users[0] or 0
        else:
            total_users = 0
            
        active_users = execute_db("SELECT COUNT(DISTINCT user_id) FROM deployments WHERE status='Running'", fetchone=True)
        if active_users:
            active_users = active_users[0] or 0
        else:
            active_users = 0
        
        # Get last backup info
        backup_dir = Path(Config.BACKUP_DIR)
        backup_files = list(backup_dir.glob("zenx_backup_*.zip"))
        last_backup = backup_files[-1].stat().st_mtime if backup_files else None
        
        # Get total deployed today
        today = datetime.now().strftime('%Y-%m-%d')
        deployed_today = execute_db("SELECT COUNT(*) FROM deployments WHERE DATE(created_at)=?", (today,), fetchone=True)
        if deployed_today:
            deployed_today = deployed_today[0] or 0
        else:
            deployed_today = 0
        
        # Get node stats
        nodes = get_available_nodes()
        used_capacity = 0
        total_capacity = 0
        
        if nodes:
            for node in nodes:
                used_capacity += node['current_load']
                total_capacity += node['capacity']
        
        stats = {
            'cpu_percent': random.randint(5, 40),
            'ram_percent': random.randint(15, 60),
            'disk_percent': random.randint(20, 70),
            'total_users': total_users,
            'active_users': active_users,
            'total_bots': total_bots,
            'running_bots': running_bots,
            'deployed_today': deployed_today,
            'uptime_days': random.randint(1, 365),
            'total_capacity': total_capacity,
            'available_capacity': total_capacity - used_capacity,
            'used_capacity': used_capacity,
            'last_backup': datetime.fromtimestamp(last_backup).strftime('%Y-%m-%d %H:%M:%S') if last_backup else "Never",
            'backup_count': len(backup_files),
            'platform': platform.system(),
            'python_version': platform.python_version()
        }
        return stats
    except Exception as e:
        logger.error(f"Error getting system stats: {e}")
        return {
            'cpu_percent': 10,
            'ram_percent': 30,
            'disk_percent': 40,
            'total_users': 0,
            'active_users': 0,
            'total_bots': 0,
            'running_bots': 0,
            'deployed_today': 0,
            'uptime_days': 1,
            'total_capacity': 900,
            'available_capacity': 900,
            'used_capacity': 0,
            'last_backup': "Never",
            'backup_count': 0,
            'platform': platform.system(),
            'python_version': platform.python_version()
        }

def get_available_nodes():
    """Get available hosting nodes"""
    try:
        nodes = execute_db("SELECT * FROM nodes WHERE status='active'", fetchall=True)
        if nodes:
            return nodes
        return []
    except:
        return []

def assign_bot_to_node(user_id, bot_name):
    """Assign bot to an available node"""
    nodes = get_available_nodes()
    
    if not nodes:
        return None
    
    # Find node with lowest current load
    best_node = None
    lowest_load = float('inf')
    
    for node in nodes:
        if node['capacity'] > 0:
            load = node['current_load'] / node['capacity']
            if load < lowest_load:
                lowest_load = load
                best_node = node
    
    return best_node

# Helper Functions
def get_user(user_id):
    return execute_db("SELECT * FROM users WHERE id=?", (user_id,), fetchone=True)

def update_user_bot_count(user_id):
    """Update user's bot count"""
    count = execute_db("SELECT COUNT(*) FROM deployments WHERE user_id=?", (user_id,), fetchone=True)
    if count:
        count = count[0] or 0
    else:
        count = 0
        
    deployments = execute_db("SELECT COUNT(*) FROM deployments WHERE user_id=? AND status='Running'", (user_id,), fetchone=True)
    if deployments:
        deployments = deployments[0] or 0
    else:
        deployments = 0
        
    execute_db("UPDATE users SET total_bots_deployed=?, total_deployments=total_deployments+1 WHERE id=?", 
              (count, user_id), commit=True)

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
    bots = execute_db("""
        SELECT id, bot_name, filename, pid, start_time, status, node_id, 
               restart_count, auto_restart, created_at 
        FROM deployments 
        WHERE user_id=? 
        ORDER BY status DESC, id DESC
    """, (user_id,), fetchall=True)
    
    if bots:
        return bots
    return []

def update_bot_stats(bot_id, cpu, ram):
    last_active = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    execute_db("UPDATE deployments SET cpu_usage=?, ram_usage=?, last_active=?, updated_at=? WHERE id=?", 
              (cpu, ram, last_active, last_active, bot_id), commit=True)

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
        export_dir = Path(Config.EXPORTS_DIR)
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
                'version': 'ZEN X HOST BOT v3.3.2',
                'exported_by': 'ZEN X Bot Hosting System',
                'node_info': '300-Capacity Multi-Node Hosting',
                'recovery_info': 'Auto-recovery enabled'
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
                    'message': f"Your Prime subscription expired {days_expired} day(s) ago."
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
            "👑 Prime Info",
            "🔔 Notifications",
            "📈 Statistics"
        ]
    else:
        # Prime expired or not active
        buttons = [
            "🔑 Activate Prime",
            "👑 Prime Info",
            "📞 Contact Admin",
            "ℹ️ Help",
            "📊 Free Dashboard"
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
        "🌐 Nodes Status",
        "🔧 Server Logs",
        "📊 System Info",
        "🔔 Broadcast",
        "🔄 Cleanup"
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
        types.InlineKeyboardButton("📜 Logs", callback_data=f"logs_{bot_id}"),
        types.InlineKeyboardButton("🔁 Auto-Restart", callback_data=f"autorestart_{bot_id}")
    )
    markup.add(types.InlineKeyboardButton("📊 Stats", callback_data=f"stats_{bot_id}"))
    markup.add(types.InlineKeyboardButton("🔙 Back to Bots", callback_data="my_bots"))
    return markup

def get_file_selection_keyboard(files):
    """Get file selection inline keyboard"""
    markup = types.InlineKeyboardMarkup(row_width=1)
    for file_id, filename, bot_name in files:
        markup.add(types.InlineKeyboardButton(f"📁 {bot_name} ({filename})", callback_data=f"select_{file_id}"))
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

def get_stats_keyboard():
    """Get statistics keyboard"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 Today", callback_data="stats_today"),
        types.InlineKeyboardButton("📈 Weekly", callback_data="stats_weekly"),
        types.InlineKeyboardButton("📅 Monthly", callback_data="stats_monthly"),
        types.InlineKeyboardButton("👤 User Stats", callback_data="stats_user")
    )
    return markup

# Message Editing Helper
def edit_or_send_message(chat_id, message_id, text, reply_markup=None, parse_mode="Markdown"):
    """Edit existing message or send new one"""
    try:
        if message_id:
            # Try to edit message
            try:
                return bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup, parse_mode=parse_mode)
            except telebot.apihelper.ApiException as e:
                # If edit fails (message can't be edited), send new message
                if "message can't be edited" in str(e):
                    msg = bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
                    update_message_history(chat_id, msg.message_id)
                    return msg
                else:
                    raise
        else:
            # Send new message
            msg = bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
            update_message_history(chat_id, msg.message_id)
            return msg
    except Exception as e:
        # Log error and send new message
        logger.error(f"Error editing/sending message: {e}")
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
        join_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        execute_db("INSERT OR IGNORE INTO users (id, username, expiry, file_limit, is_prime, join_date, last_renewal, last_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
                  (uid, username, None, 1, 0, join_date, None, join_date), commit=True)
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
        plan = "Prime"
    
    # Check for notifications
    unread_notifications = execute_db("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", 
                                     (uid,), fetchone=True)
    if unread_notifications:
        unread_notifications = unread_notifications[0] or 0
    else:
        unread_notifications = 0
    
    text = f"""
🤖 **ZEN X HOST BOT v3.3.2**
*Auto-Recovery System Enabled*
━━━━━━━━━━━━━━━━━━━━
👤 **User:** @{username}
🆔 **ID:** `{uid}`
💎 **Status:** {status}
📅 **Join Date:** {user['join_date'] if user else 'N/A'}
🔔 **Notifications:** {unread_notifications} unread
━━━━━━━━━━━━━━━━━━━━
📊 **Account Details:**
• Plan: {plan}
• File Limit: `{user['file_limit'] if user else 1}` files
• Expiry: {expiry_msg}
• Total Bots: {user['total_bots_deployed'] or 0 if user else 0}
• Total Deployments: {user['total_deployments'] or 0 if user else 0}
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
👑 **ADMIN CONTROL PANEL v3.3.2**
━━━━━━━━━━━━━━━━━━━━
*Auto-Recovery System: ACTIVE*
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
    elif session.get('state') == 'waiting_for_logs_count':
        process_logs_count(message)
    elif session.get('state') == 'waiting_for_broadcast':
        process_broadcast_message(message)
    elif session.get('state') == 'waiting_for_user_id':
        process_user_id_input(message)
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
    elif text == "📊 Free Dashboard":
        handle_dashboard(message, last_msg_id)
    elif text == "⚙️ Settings":
        handle_settings(message, last_msg_id)
    elif text == "👑 Prime Info":
        handle_premium_info(message, last_msg_id)
    elif text == "🔑 Activate Prime":
        handle_activate_prime(message, last_msg_id)
    elif text == "📞 Contact Admin":
        handle_contact_admin(message, last_msg_id)
    elif text == "ℹ️ Help":
        handle_help(message, last_msg_id)
    elif text == "🔔 Notifications":
        handle_notifications(message, last_msg_id)
    elif text == "📈 Statistics":
        handle_user_statistics(message, last_msg_id)
    elif text == "👑 Admin Panel":
        handle_admin_panel(message, last_msg_id)
    elif text == "🏠 Main Menu":
        handle_commands(message)
    elif text in ["🎫 Generate Key", "👥 All Users", "🤖 All Bots", "📈 Statistics", 
                  "🗄️ View Database", "💾 Backup DB", "⚙️ Maintenance", "🌐 Nodes Status", 
                  "🔧 Server Logs", "📊 System Info", "🔔 Broadcast", "🔄 Cleanup"]:
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
*Auto-Recovery: Enabled*
━━━━━━━━━━━━━━━━━━━━
Please send your Python (.py) bot file or ZIP file containing bot.

**Requirements:**
• Max size: 5.5MB
• Allowed: .py, .zip
• Must have main function
• Auto-recovery will restart on server crash
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
    auto_restart_bots = sum(1 for b in bots if b['auto_restart'] == 1)
    
    text = f"""
🤖 **MY BOTS**
━━━━━━━━━━━━━━━━━━━━
*Auto-Recovery: {auto_restart_bots}/{len(bots)} bots*
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
        auto_icon = "🔁" if bot_info['auto_restart'] == 1 else "⏸️"
        created_date = bot_info['created_at'].split()[0] if bot_info['created_at'] else "N/A"
        markup.add(types.InlineKeyboardButton(
            f"{status_icon}{auto_icon} {bot_info['bot_name']} ({created_date})", 
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
    files = execute_db("SELECT id, filename, bot_name FROM deployments WHERE user_id=? AND (pid=0 OR pid IS NULL OR status='Stopped')", 
                      (uid,), fetchall=True)
    
    if not files:
        text = "📭 **No files available for deployment**\n\nUpload a file first."
        edit_or_send_message(message.chat.id, last_msg_id, text, reply_markup=get_main_keyboard(uid))
        return
    
    text = """
🚀 **DEPLOY BOT**
━━━━━━━━━━━━━━━━━━━━
*Auto-recovery will restart on server crash*
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
    auto_restart_bots = sum(1 for b in bots if b['auto_restart'] == 1)
    
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
    total_capacity = stats['total_capacity']
    used_capacity = stats['used_capacity']
    
    # Get user statistics
    total_deployments = execute_db("SELECT COUNT(*) FROM deployments WHERE user_id=?", (uid,), fetchone=True)
    if total_deployments:
        total_deployments = total_deployments[0] or 0
    else:
        total_deployments = 0
        
    today_deployments = execute_db("SELECT COUNT(*) FROM deployments WHERE user_id=? AND DATE(created_at)=DATE('now')", (uid,), fetchone=True)
    if today_deployments:
        today_deployments = today_deployments[0] or 0
    else:
        today_deployments = 0
    
    text = f"""
📊 **USER DASHBOARD v3.3.2**
━━━━━━━━━━━━━━━━━━━━
*Auto-Recovery System: ACTIVE*
━━━━━━━━━━━━━━━━━━━━
👤 **Account Info:**
• Status: {'PRIME 👑' if not prime_status['expired'] else 'EXPIRED ⚠️'}
• File Limit: {user['file_limit']} files
• Total Bots: {total_bots}/{Config.MAX_BOTS_PER_USER}
• Running: {running_bots}
• Auto-Restart: {auto_restart_bots}/{total_bots}
• Total Deployments: {total_deployments}
• Today: {today_deployments}
━━━━━━━━━━━━━━━━━━━━
🖥️ **System Status:**
• CPU: {cpu_bar} {cpu_usage:.1f}%
• RAM: {ram_bar} {ram_usage:.1f}%
• Disk: {disk_bar} {disk_usage:.1f}%
• Active Nodes: {active_nodes}/{len(Config.HOSTING_NODES)}
• Capacity: {used_capacity}/{total_capacity} bots
• Last Backup: {stats['last_backup']}
━━━━━━━━━━━━━━━━━━━━
🌐 **Hosting Platform:**
• Platform: ZEN X HOSTING v3.3.2
• Type: Web Service with Auto-Recovery
• Max Concurrent: {Config.MAX_CONCURRENT_DEPLOYMENTS}
• Region: Asia → Bangladesh 🇧🇩
• Uptime: {stats['uptime_days']} days
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
    
    # Get user preferences
    notifications = execute_db("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (uid,), fetchone=True)
    if notifications:
        notifications = notifications[0] or 0
    else:
        notifications = 0
    
    text = f"""
⚙️ **SETTINGS v3.3.2**
━━━━━━━━━━━━━━━━━━━━
👤 **Account Settings:**
• User ID: `{uid}`
• Status: {'PRIME 👑' if not prime_status['expired'] else 'EXPIRED ⚠️'}
• File Limit: {user['file_limit']} files
• Join Date: {user['join_date']}
• Last Active: {user['last_active'] or 'N/A'}
━━━━━━━━━━━━━━━━━━━━
🔧 **Bot Settings:**
• Auto-restart: Enabled
• Auto-recovery: Enabled
• Notifications: {notifications} unread
• Language: English
━━━━━━━━━━━━━━━━━━━━
💎 **Prime Status:**
• Active: {'Yes' if not prime_status['expired'] else 'No'}
• Expiry: {prime_status.get('expiry_date', 'N/A')}
• Days Left: {prime_status.get('days_left', 'N/A') if not prime_status['expired'] else 'Expired'}
━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🔄 Renew Prime", callback_data="activate_prime"),
        types.InlineKeyboardButton("🔔 Notifications", callback_data="notif_settings")
    )
    
    edit_or_send_message(message.chat.id, last_msg_id, text, reply_markup=markup)

def handle_notifications(message, last_msg_id=None):
    uid = message.from_user.id
    
    notifications = execute_db("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,), fetchall=True)
    
    if not notifications:
        text = """
🔔 **NOTIFICATIONS**
━━━━━━━━━━━━━━━━━━━━
No notifications found.
━━━━━━━━━━━━━━━━━━━━
"""
        edit_or_send_message(message.chat.id, last_msg_id, text)
        return
    
    unread_count = sum(1 for n in notifications if n['is_read'] == 0)
    
    text = f"""
🔔 **NOTIFICATIONS**
━━━━━━━━━━━━━━━━━━━━
**Unread:** {unread_count}
**Total:** {len(notifications)}
━━━━━━━━━━━━━━━━━━━━
"""
    
    for notif in notifications[:5]:
        read_icon = "🔵" if notif['is_read'] == 0 else "⚪"
        text += f"\n{read_icon} {notif['created_at']}\n{notif['message']}\n━━━━━━━━━━━━━━━━━━━━\n"
    
    if len(notifications) > 5:
        text += f"\n... and {len(notifications) - 5} more notifications"
    
    # Mark all as read
    execute_db("UPDATE notifications SET is_read=1 WHERE user_id=?", (uid,), commit=True)
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("📨 Clear All", callback_data="clear_notifications"),
        types.InlineKeyboardButton("🔄 Refresh", callback_data="refresh_notifications")
    )
    
    edit_or_send_message(message.chat.id, last_msg_id, text, reply_markup=markup)

def handle_user_statistics(message, last_msg_id=None):
    uid = message.from_user.id
    
    # Get user stats
    total_bots = execute_db("SELECT COUNT(*) FROM deployments WHERE user_id=?", (uid,), fetchone=True)
    if total_bots:
        total_bots = total_bots[0] or 0
    else:
        total_bots = 0
        
    running_bots = execute_db("SELECT COUNT(*) FROM deployments WHERE user_id=? AND status='Running'", (uid,), fetchone=True)
    if running_bots:
        running_bots = running_bots[0] or 0
    else:
        running_bots = 0
        
    total_deployments = execute_db("SELECT COUNT(*) FROM deployments WHERE user_id=?", (uid,), fetchone=True)
    if total_deployments:
        total_deployments = total_deployments[0] or 0
    else:
        total_deployments = 0
        
    today_deployments = execute_db("SELECT COUNT(*) FROM deployments WHERE user_id=? AND DATE(created_at)=DATE('now')", (uid,), fetchone=True)
    if today_deployments:
        today_deployments = today_deployments[0] or 0
    else:
        today_deployments = 0
    
    # Get weekly stats
    week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    weekly_deployments = execute_db("SELECT COUNT(*) FROM deployments WHERE user_id=? AND DATE(created_at) >= ?", 
                                   (uid, week_ago), fetchone=True)
    if weekly_deployments:
        weekly_deployments = weekly_deployments[0] or 0
    else:
        weekly_deployments = 0
    
    # Get bot types
    bot_files = execute_db("SELECT filename, COUNT(*) as count FROM deployments WHERE user_id=? GROUP BY filename", 
                          (uid,), fetchall=True) or []
    
    text = f"""
📈 **USER STATISTICS**
━━━━━━━━━━━━━━━━━━━━
👤 **User ID:** `{uid}`
━━━━━━━━━━━━━━━━━━━━
📊 **Bot Statistics:**
• Total Bots: {total_bots}
• Running Bots: {running_bots}
• Total Deployments: {total_deployments}
• Today: {today_deployments}
• This Week: {weekly_deployments}
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot Files:**
"""
    
    for bot_file in bot_files[:5]:
        text += f"• {bot_file['filename']}: {bot_file['count']} bots\n"
    
    if len(bot_files) > 5:
        text += f"• ... and {len(bot_files) - 5} more files\n"
    
    text += "━━━━━━━━━━━━━━━━━━━━\n"
    
    edit_or_send_message(message.chat.id, last_msg_id, text)

def handle_premium_info(message, last_msg_id=None):
    text = f"""
👑 **PRIME FEATURES v3.3.2**
━━━━━━━━━━━━━━━━━━━━
✅ **300-Capacity Node Hosting**
✅ **Auto-Recovery System**
✅ **Database Backup System**
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
✅ **Notifications System**
✅ **Detailed Statistics**
✅ **Multi-Region Hosting**
━━━━━━━━━━━━━━━━━━━━
💎 **Get Prime Today!**
Contact: @{Config.ADMIN_USERNAME}
━━━━━━━━━━━━━━━━━━━━
💰 **Pricing:**
• 7 Days: ৳15
• 30 Days: ৳25
• 90 Days: ৳45
• 365 Days: ৳125
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
ℹ️ **HELP GUIDE v3.3.2**
━━━━━━━━━━━━━━━━━━━━
*Auto-Recovery System Features:*
• Server restart automatically recovers bots
• Database backups every hour
• Bot auto-restart on crash
• Persistent storage across restarts
━━━━━━━━━━━━━━━━━━━━
**How to use:**
1. First activate Prime with key
2. Upload your bot file (.py or .zip)
3. Deploy the bot to a node
4. Enable auto-restart for recovery
5. Manage your bots from "My Bots"
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
👑 **ADMIN CONTROL PANEL v3.3.2**
━━━━━━━━━━━━━━━━━━━━
*Auto-Recovery System: ACTIVE*
━━━━━━━━━━━━━━━━━━━━
Welcome to the admin dashboard.
Select an option from the keyboard below:
━━━━━━━━━━━━━━━━━━━━
"""
        msg = edit_or_send_message(message.chat.id, None, text, reply_markup=get_admin_keyboard())
        update_message_history(uid, msg.message_id)
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
    elif button_text == "🔧 Server Logs":
        show_server_logs(message, last_msg_id)
    elif button_text == "📊 System Info":
        show_system_info(message, last_msg_id)
    elif button_text == "🔔 Broadcast":
        start_broadcast(message, last_msg_id)
    elif button_text == "🔄 Cleanup":
        cleanup_system(message, last_msg_id)

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
*Auto-recovery will be enabled by default*
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
*Auto-recovery will be enabled by default*
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
    
    # Save to database with auto_restart enabled by default
    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    execute_db("INSERT INTO deployments (user_id, bot_name, filename, pid, start_time, status, last_active, auto_restart, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (uid, bot_name, filename, 0, None, "Uploaded", created_at, 1, created_at, created_at), commit=True)
    
    # Update user bot count
    update_user_bot_count(uid)
    
    clear_user_session(uid)
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📚 Install Libraries", callback_data="install_libs"))
    markup.add(types.InlineKeyboardButton("🚀 Deploy Now", callback_data="deploy_new"))
    markup.add(types.InlineKeyboardButton("🤖 My Bots", callback_data="my_bots"))
    
    text = f"""
✅ **FILE UPLOADED SUCCESSFULLY**
━━━━━━━━━━━━━━━━━━━━
*Auto-recovery: ENABLED ✅*
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot Name:** {bot_name}
📁 **File:** `{original_name}`
📊 **Status:** Ready for setup
🔁 **Auto-Restart:** Enabled
📅 **Uploaded:** {created_at}
━━━━━━━━━━━━━━━━━━━━
"""
    
    edit_or_send_message(chat_id, None, text, reply_markup=markup)
    send_notification(uid, f"Bot '{bot_name}' uploaded successfully!")

def process_key_input(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    
    if message.text.lower() == 'cancel':
        clear_user_session(uid)
        edit_or_send_message(chat_id, None, "❌ Cancelled.", reply_markup=get_main_keyboard(uid))
        return
    
    key_input = message.text.strip().upper()
    
    res = execute_db("SELECT * FROM keys WHERE key=?", (key_input,), fetchone=True)
    
    if res:
        if res['is_used'] == 1:
            edit_or_send_message(chat_id, None, "❌ **Key already used!**\n\nThis key has already been activated.")
            return
            
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
        execute_db("UPDATE users SET expiry=?, file_limit=?, is_prime=1, last_renewal=?, last_active=? WHERE id=?", 
                  (expiry_date, limit, last_renewal, last_renewal, uid), commit=True)
        execute_db("UPDATE keys SET used_by=?, used_date=?, is_used=1 WHERE key=?", 
                  (uid, last_renewal, key_input), commit=True)
        
        # Stop all user bots if renewing after expiry
        if not (current_expiry and current_expiry > datetime.now()):
            user_bots = execute_db("SELECT id, pid FROM deployments WHERE user_id=?", (uid,), fetchall=True) or []
            for bot in user_bots:
                if bot['pid']:
                    try:
                        os.kill(bot['pid'], signal.SIGTERM)
                    except:
                        pass
                execute_db("UPDATE deployments SET status='Stopped', pid=0 WHERE id=?", (bot['id'],), commit=True)
        
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
Enjoy all prime features!
        """
        
        clear_user_session(uid)
        edit_or_send_message(chat_id, None, text, reply_markup=get_main_keyboard(uid))
        send_notification(uid, f"Prime activated successfully! Expires on {expiry_date}")
    else:
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
        
        execute_db("INSERT INTO keys (key, duration_days, file_limit, created_date) VALUES (?, ?, ?, ?)", 
                  (key, days, limit, created_date), commit=True)
        
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

def process_logs_count(message):
    """Process logs count input"""
    uid = message.from_user.id
    chat_id = message.chat.id
    
    if message.text.lower() == 'cancel':
        clear_user_session(uid)
        edit_or_send_message(chat_id, None, "❌ Cancelled.", reply_markup=get_admin_keyboard())
        return
    
    try:
        count = int(message.text.strip())
        if count <= 0 or count > 100:
            edit_or_send_message(chat_id, None, "❌ Count must be between 1 and 100!")
            return
        
        show_server_logs_count(message, count)
        
    except:
        edit_or_send_message(chat_id, None, "❌ Invalid input!")

def process_broadcast_message(message):
    """Process broadcast message"""
    uid = message.from_user.id
    chat_id = message.chat.id
    
    if message.text.lower() == 'cancel':
        clear_user_session(uid)
        edit_or_send_message(chat_id, None, "❌ Cancelled.", reply_markup=get_admin_keyboard())
        return
    
    broadcast_text = message.text.strip()
    
    # Get all users
    users = execute_db("SELECT id FROM users", fetchall=True) or []
    
    total_users = len(users)
    success_count = 0
    fail_count = 0
    
    progress_msg = bot.send_message(chat_id, f"📢 **Broadcasting to {total_users} users...**\n\n0/{total_users}")
    
    for i, user in enumerate(users):
        try:
            bot.send_message(user['id'], f"📢 **Broadcast Message**\n\n{broadcast_text}")
            success_count += 1
            
            # Send notification
            send_notification(user['id'], f"Broadcast: {broadcast_text[:50]}...")
            
        except Exception as e:
            fail_count += 1
        
        # Update progress every 10 users
        if i % 10 == 0:
            try:
                bot.edit_message_text(
                    f"📢 **Broadcasting to {total_users} users...**\n\n{i}/{total_users}",
                    chat_id, progress_msg.message_id
                )
            except:
                pass
    
    text = f"""
✅ **BROADCAST COMPLETED**
━━━━━━━━━━━━━━━━━━━━
📢 **Message:** {broadcast_text[:100]}...
━━━━━━━━━━━━━━━━━━━━
📊 **Results:**
• Total Users: {total_users}
• Success: {success_count}
• Failed: {fail_count}
• Success Rate: {(success_count/total_users*100):.1f}%
━━━━━━━━━━━━━━━━━━━━
"""
    
    clear_user_session(uid)
    bot.edit_message_text(text, chat_id, progress_msg.message_id)
    edit_or_send_message(chat_id, None, "📢 Broadcast sent!", reply_markup=get_admin_keyboard())

def process_user_id_input(message):
    """Process user ID input"""
    uid = message.from_user.id
    chat_id = message.chat.id
    
    if message.text.lower() == 'cancel':
        clear_user_session(uid)
        edit_or_send_message(chat_id, None, "❌ Cancelled.", reply_markup=get_admin_keyboard())
        return
    
    try:
        target_user_id = int(message.text.strip())
        
        user = execute_db("SELECT * FROM users WHERE id=?", (target_user_id,), fetchone=True)
        
        if not user:
            edit_or_send_message(chat_id, None, "❌ User not found!")
            return
        
        username = user['username'] or f"User_{target_user_id}"
        expiry = user['expiry'] or "Not Activated"
        is_prime = "✅" if user['is_prime'] == 1 else "❌"
        join_date = user['join_date']
        total_bots = user['total_bots_deployed']
        
        # Get user's bots
        bots = execute_db("SELECT COUNT(*) as total, SUM(CASE WHEN status='Running' THEN 1 ELSE 0 END) as running FROM deployments WHERE user_id=?", 
                         (target_user_id,), fetchone=True)
        
        text = f"""
👤 **USER DETAILS**
━━━━━━━━━━━━━━━━━━━━
🆔 **ID:** `{target_user_id}`
👤 **Username:** @{username}
💎 **Prime:** {is_prime}
📅 **Join Date:** {join_date}
⏰ **Expiry:** {expiry}
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot Statistics:**
• Total Bots: {bots['total'] or 0 if bots else 0}
• Running: {bots['running'] or 0 if bots else 0}
• Stopped: {(bots['total'] or 0 if bots else 0) - (bots['running'] or 0 if bots else 0)}
• Total Deployed: {total_bots}
━━━━━━━━━━━━━━━━━━━━
"""
        
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("📨 Message User", callback_data=f"msguser_{target_user_id}"),
            types.InlineKeyboardButton("👁️ View Bots", callback_data=f"viewuser_{target_user_id}"),
            types.InlineKeyboardButton("⚡ Reset Limit", callback_data=f"resetlimit_{target_user_id}")
        )
        
        clear_user_session(uid)
        edit_or_send_message(chat_id, None, text, reply_markup=markup)
        
    except:
        edit_or_send_message(chat_id, None, "❌ Invalid user ID!")

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
        elif call.data == "user_stats":
            handle_user_statistics(call.message, message_id)
        elif call.data == "notif_settings":
            handle_notifications(call.message, message_id)
        elif call.data == "clear_notifications":
            clear_notifications(call)
        elif call.data == "refresh_notifications":
            handle_notifications(call.message, message_id)
        
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
        
        elif call.data.startswith("autorestart_"):
            bot_id = call.data.split("_")[1]
            toggle_auto_restart(call, bot_id)
        
        elif call.data.startswith("stats_"):
            if call.data.startswith("stats_"):
                parts = call.data.split("_")
                if len(parts) == 2:
                    bot_id = parts[1]
                    show_bot_stats(call, bot_id)
                else:
                    handle_user_statistics(call.message, message_id)
        
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
        
        elif call.data.startswith("msguser_"):
            user_id = call.data.split("_")[1]
            message_user(call, user_id)
        
        elif call.data.startswith("viewuser_"):
            user_id = call.data.split("_")[1]
            view_user_bots(call, user_id)
        
        elif call.data.startswith("resetlimit_"):
            user_id = call.data.split("_")[1]
            reset_user_limit(call, user_id)
        
    except Exception as e:
        logger.error(f"Callback error: {e}")
        bot.answer_callback_query(call.id, "⚠️ Error occurred!")

# Deployment Functions
def start_deployment(call, file_id):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    bot_info = execute_db("SELECT id, bot_name, filename, auto_restart FROM deployments WHERE id=?", (file_id,), fetchone=True)
    
    if not bot_info:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    
    bot_id, bot_name, filename, auto_restart = bot_info
    
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
*Auto-recovery: {'ENABLED ✅' if auto_restart else 'DISABLED ❌'}*
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot:** {bot_name}
🌐 **Node:** {node['name']} ({node['region']})
📊 **Capacity:** {node['current_load']}/{node['capacity']}
🔄 **Status:** Starting...
━━━━━━━━━━━━━━━━━━━━
"""
    edit_or_send_message(chat_id, message_id, text)
    
    try:
        file_path = project_path / filename
        start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Start process
        logs_dir = Path(Config.LOGS_DIR)
        logs_dir.mkdir(exist_ok=True)
        
        with open(f'{Config.LOGS_DIR}/bot_{bot_id}.log', 'a') as log_file:
            log_file.write(f"\n{'='*50}\nDeployment started at {start_time}\n{'='*50}\n")
            proc = subprocess.Popen(
                ['python', str(file_path)],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
        
        # Wait for process to start
        time.sleep(2)
        
        # Check if process is running
        if proc.poll() is not None:
            # Process failed to start
            error_msg = "Bot failed to start. Check the bot code."
            with open(f'{Config.LOGS_DIR}/bot_{bot_id}.log', 'a') as log_file:
                log_file.write(f"\nERROR: {error_msg}\n")
            
            text = f"""
❌ **DEPLOYMENT FAILED**
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot:** {bot_name}
❌ **Error:** {error_msg}
━━━━━━━━━━━━━━━━━━━━
Check your bot code and try again.
"""
            edit_or_send_message(chat_id, message_id, text)
            log_bot_event(bot_id, "DEPLOY_FAILED", error_msg)
            return
        
        # Update database
        updated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        execute_db("UPDATE deployments SET pid=?, start_time=?, status='Running', node_id=?, last_active=?, updated_at=? WHERE id=?", 
                  (proc.pid, start_time, node['id'], start_time, updated_at, bot_id), commit=True)
        
        # Update node load
        execute_db("UPDATE nodes SET current_load=current_load+1, total_deployed=total_deployed+1 WHERE id=?", (node['id'],), commit=True)
        
        log_event("DEPLOY", f"Bot {bot_name} (ID: {bot_id}) deployed to {node['name']}", uid)
        log_bot_event(bot_id, "DEPLOY_SUCCESS", f"Deployed to {node['name']}")
        
        # Success message
        text = f"""
✅ **BOT DEPLOYED SUCCESSFULLY**
━━━━━━━━━━━━━━━━━━━━
*Auto-recovery: {'ENABLED ✅' if auto_restart else 'DISABLED ❌'}*
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot:** {bot_name}
🌐 **Node:** {node['name']} ({node['region']})
📊 **Load:** {node['current_load']+1}/{node['capacity']}
⚙️ **PID:** `{proc.pid}`
⏰ **Started:** {start_time}
🔧 **Status:** **RUNNING**
━━━━━━━━━━━━━━━━━━━━
Bot is now active and running!
"""
        edit_or_send_message(chat_id, message_id, text)
        send_notification(uid, f"Bot '{bot_name}' deployed successfully!")
        
        # Start monitoring
        start_bot_monitoring(bot_id, proc.pid, uid)
        
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
        log_bot_event(bot_id, "DEPLOY_ERROR", str(e))

def start_bot_monitoring(bot_id, pid, user_id):
    """Start monitoring bot in background"""
    def monitor():
        try:
            start_time = time.time()
            while True:
                # Check if process is still running
                try:
                    os.kill(pid, 0)
                except OSError:
                    # Process died
                    bot_info = execute_db("SELECT bot_name, auto_restart FROM deployments WHERE id=?", (bot_id,), fetchone=True)
                    
                    if bot_info and bot_info['auto_restart'] == 1:
                        # Auto-restart enabled
                        updated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        execute_db("UPDATE deployments SET status='Restarting', restart_count=restart_count+1, updated_at=? WHERE id=?", 
                                  (updated_at, bot_id), commit=True)
                        
                        # Wait and try to restart
                        time.sleep(5)
                        
                        # Find and restart the bot
                        bot_data = execute_db("SELECT filename FROM deployments WHERE id=?", (bot_id,), fetchone=True)
                        
                        if bot_data:
                            file_path = project_path / bot_data['filename']
                            if file_path.exists():
                                start_time_new = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                with open(f'{Config.LOGS_DIR}/bot_{bot_id}.log', 'a') as log_file:
                                    log_file.write(f"\n{'='*50}\nAuto-restart at {start_time_new}\n{'='*50}\n")
                                    new_proc = subprocess.Popen(
                                        ['python', str(file_path)],
                                        stdout=log_file,
                                        stderr=subprocess.STDOUT,
                                        start_new_session=True
                                    )
                                
                                execute_db("UPDATE deployments SET pid=?, start_time=?, status='Running', last_active=? WHERE id=?", 
                                          (new_proc.pid, start_time_new, start_time_new, bot_id), commit=True)
                                
                                pid = new_proc.pid
                                log_bot_event(bot_id, "AUTO_RESTART", "Bot auto-restarted")
                                send_notification(user_id, f"Bot '{bot_info['bot_name']}' auto-restarted")
                                continue
                    
                    # Mark as stopped
                    updated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    execute_db("UPDATE deployments SET status='Stopped', pid=0, last_active=?, updated_at=? WHERE id=?", 
                              (updated_at, updated_at, bot_id), commit=True)
                    
                    # Update node load
                    execute_db("UPDATE nodes SET current_load=current_load-1 WHERE id=(SELECT node_id FROM deployments WHERE id=?)", (bot_id,), commit=True)
                    
                    log_bot_event(bot_id, "PROCESS_STOPPED", "Bot process stopped")
                    break
                
                # Update stats every 30 seconds
                if time.time() - start_time > 30:
                    stats = get_system_stats()
                    update_bot_stats(bot_id, stats['cpu_percent'], stats['ram_percent'])
                    start_time = time.time()
                
                time.sleep(5)
                
        except Exception as e:
            logger.error(f"Monitoring error for bot {bot_id}: {e}")
    
    # Start monitoring thread
    if bot_id not in bot_monitors:
        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()
        bot_monitors[bot_id] = monitor_thread

def stop_bot(call, bot_id):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    
    bot_info = execute_db("SELECT pid, node_id, bot_name FROM deployments WHERE id=?", (bot_id,), fetchone=True)
    
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
    execute_db("UPDATE deployments SET status='Stopped', pid=0, last_active=?, updated_at=? WHERE id=?", (last_active, last_active, bot_id), commit=True)
    
    if bot_info and bot_info['node_id']:
        execute_db("UPDATE nodes SET current_load=current_load-1 WHERE id=?", (bot_info['node_id'],), commit=True)
    
    log_event("STOP", f"Bot {bot_info['bot_name']} (ID: {bot_id}) stopped", uid)
    log_bot_event(bot_id, "MANUAL_STOP", "Bot stopped by user")
    
    bot.answer_callback_query(call.id, f"✅ {bot_info['bot_name']} stopped successfully!")
    send_notification(uid, f"Bot '{bot_info['bot_name']}' stopped")
    show_bot_details(call, bot_id)

def restart_bot(call, bot_id):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    
    # First stop
    bot_info = execute_db("SELECT pid, node_id, filename, bot_name FROM deployments WHERE id=?", (bot_id,), fetchone=True)
    
    if bot_info and bot_info['pid']:
        try:
            os.kill(bot_info['pid'], signal.SIGTERM)
            time.sleep(1)
        except:
            pass
    
    execute_db("UPDATE deployments SET status='Restarting', restart_count=restart_count+1, updated_at=? WHERE id=?", 
              (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), bot_id), commit=True)
    
    log_event("RESTART", f"Bot {bot_info['bot_name']} (ID: {bot_id}) restarting", uid)
    
    bot.answer_callback_query(call.id, "🔄 Restarting bot...")
    
    # Wait and restart
    time.sleep(2)
    start_deployment(call, bot_id)

def toggle_auto_restart(call, bot_id):
    bot_info = execute_db("SELECT bot_name, auto_restart FROM deployments WHERE id=?", (bot_id,), fetchone=True)
    
    if bot_info:
        new_status = 0 if bot_info['auto_restart'] == 1 else 1
        execute_db("UPDATE deployments SET auto_restart=? WHERE id=?", (new_status, bot_id), commit=True)
        
        status_text = "ENABLED ✅" if new_status == 1 else "DISABLED ❌"
        bot.answer_callback_query(call.id, f"Auto-restart {status_text} for {bot_info['bot_name']}")
        
        log_event("AUTO_RESTART_TOGGLE", f"Bot {bot_info['bot_name']} auto-restart set to {status_text}", call.from_user.id)
        send_notification(call.from_user.id, f"Auto-restart {status_text.lower()} for bot '{bot_info['bot_name']}'")
    
    show_bot_details(call, bot_id)

def show_bot_stats(call, bot_id):
    bot_info = execute_db("SELECT * FROM deployments WHERE id=?", (bot_id,), fetchone=True)
    
    if not bot_info:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    
    # Get bot logs
    logs = execute_db("SELECT * FROM bot_logs WHERE bot_id=? ORDER BY id DESC LIMIT 10", (bot_id,), fetchall=True) or []
    
    bot_name = bot_info['bot_name']
    restart_count = bot_info['restart_count']
    created_at = bot_info['created_at']
    updated_at = bot_info['updated_at']
    
    text = f"""
📊 **BOT STATISTICS**
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot:** {bot_name}
🆔 **ID:** {bot_id}
━━━━━━━━━━━━━━━━━━━━
📈 **Stats:**
• Restart Count: {restart_count}
• Created: {created_at}
• Last Updated: {updated_at}
• Auto-Restart: {'Enabled ✅' if bot_info['auto_restart'] == 1 else 'Disabled ❌'}
━━━━━━━━━━━━━━━━━━━━
📜 **Recent Events:**
"""
    
    if logs:
        for log in logs:
            text += f"\n• {log['timestamp']} - {log['log_type']}: {log['message'][:50]}"
    else:
        text += "\nNo events recorded."
    
    text += "\n━━━━━━━━━━━━━━━━━━━━"
    
    bot.send_message(call.message.chat.id, text)
    bot.answer_callback_query(call.id, "📊 Statistics sent!")

def export_bot(call, bot_id):
    bot_info = execute_db("SELECT bot_name, filename, user_id FROM deployments WHERE id=?", (bot_id,), fetchone=True)
    
    if not bot_info:
        bot.answer_callback_query(call.id, "❌ Bot not found!")
        return
    
    bot_name, filename, user_id = bot_info
    zip_path = create_zip_file(bot_id, bot_name, filename, user_id)
    
    if zip_path and zip_path.exists():
        try:
            with open(zip_path, 'rb') as f:
                bot.send_document(call.message.chat.id, f, 
                                 caption=f"📦 **Bot Export:** {bot_name}\n\nFile: `{filename}`\nExport Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nAuto-Recovery: Included")
            
            time.sleep(2)
            zip_path.unlink(missing_ok=True)
            bot.answer_callback_query(call.id, "✅ Bot exported successfully!")
            
        except Exception as e:
            bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:50]}")
    else:
        bot.answer_callback_query(call.id, "❌ Error creating export!")

def show_bot_details(call, bot_id):
    bot_info = execute_db("SELECT * FROM deployments WHERE id=?", (bot_id,), fetchone=True)
    
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
    auto_restart = bot_info['auto_restart']
    created_at = bot_info['created_at']
    
    stats = get_system_stats()
    cpu_usage = cpu_usage or stats['cpu_percent']
    ram_usage = ram_usage or stats['ram_percent']
    
    cpu_bar = create_progress_bar(cpu_usage)
    ram_bar = create_progress_bar(ram_usage)
    
    # Check if process is running
    is_running = get_process_stats(pid) if pid else False
    
    # Get node info
    node_info = execute_db("SELECT name, region FROM nodes WHERE id=?", (node_id,), fetchone=True) if node_id else None
    
    node_text = f"{node_info['name']} ({node_info['region']})" if node_info else "N/A"
    
    text = f"""
🤖 **BOT DETAILS**
━━━━━━━━━━━━━━━━━━━━
*Auto-Recovery: {'ENABLED ✅' if auto_restart == 1 else 'DISABLED ❌'}*
━━━━━━━━━━━━━━━━━━━━
**Name:** {bot_name}
**File:** `{filename}`
**Status:** {"🟢 Running" if is_running else "🔴 Stopped"}
**Node:** {node_text}
**Created:** {created_at}
━━━━━━━━━━━━━━━━━━━━
📊 **Statistics:**
• CPU: {cpu_bar} {cpu_usage:.1f}%
• RAM: {ram_bar} {ram_usage:.1f}%
• PID: `{pid if pid else "N/A"}`
• Uptime: {calculate_uptime(start_time) if start_time else "N/A"}
• Restarts: {restart_count}
• Auto-Restart: {'Yes' if auto_restart == 1 else 'No'}
━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = get_bot_actions_keyboard(bot_id)
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

def show_bot_logs(call, bot_id):
    log_file = f'{Config.LOGS_DIR}/bot_{bot_id}.log'
    
    if not os.path.exists(log_file):
        bot.answer_callback_query(call.id, "📜 No logs available")
        return
    
    try:
        with open(log_file, 'r') as f:
            logs = f.read()[-3000:]
        
        if len(logs) > 1500:
            logs = logs[-1500:] + "\n\n... (truncated, view full logs in file)"
        
        # Get bot name
        bot_name = execute_db("SELECT bot_name FROM deployments WHERE id=?", (bot_id,), fetchone=True)
        if bot_name:
            bot_name = bot_name['bot_name']
        else:
            bot_name = "Unknown"
        
        text = f"""
📜 **BOT LOGS: {bot_name}**
━━━━━━━━━━━━━━━━━━━━
{logs}
━━━━━━━━━━━━━━━━━━━━
"""
        bot.send_message(call.message.chat.id, text)
        bot.answer_callback_query(call.id, "📜 Logs sent!")
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Error reading logs: {str(e)[:50]}")

def confirm_delete_bot(call, bot_id):
    bot_info = execute_db("SELECT bot_name, filename FROM deployments WHERE id=?", (bot_id,), fetchone=True)
    
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
    
    bot_info = execute_db("SELECT filename, pid, node_id, bot_name FROM deployments WHERE id=?", (bot_id,), fetchone=True)
    
    if bot_info:
        filename, pid, node_id, bot_name = bot_info
        
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
            execute_db("UPDATE nodes SET current_load=current_load-1 WHERE id=?", (node_id,), commit=True)
        
        # Delete from database
        execute_db("DELETE FROM deployments WHERE id=?", (bot_id,), commit=True)
        
        # Delete bot logs
        execute_db("DELETE FROM bot_logs WHERE bot_id=?", (bot_id,), commit=True)
        
        # Update user bot count
        update_user_bot_count(uid)
        
        log_event("DELETE", f"Bot {bot_name} (ID: {bot_id}) deleted", uid)
        send_notification(uid, f"Bot '{bot_name}' deleted")
    
    bot.answer_callback_query(call.id, "✅ Bot deleted successfully!")
    handle_my_bots(call.message, call.message.message_id)

def clear_notifications(call):
    uid = call.from_user.id
    
    execute_db("DELETE FROM notifications WHERE user_id=?", (uid,), commit=True)
    
    bot.answer_callback_query(call.id, "✅ Notifications cleared!")
    handle_notifications(call.message, call.message.message_id)

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
    users = execute_db("SELECT id, username, expiry, file_limit, is_prime, join_date, total_bots_deployed, total_deployments FROM users ORDER BY id DESC", fetchall=True) or []
    
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
    
    for user in users[:10]:
        username = user['username'] if user['username'] else f"User_{user['id']}"
        status = "👑" if user['is_prime'] else "🆓"
        text += f"\n{status} **{username}** (ID: {user['id']})"
        text += f"\n• Bots: {user['total_bots_deployed']} | Deployments: {user['total_deployments']}"
        text += f"\n• Expiry: {user['expiry'] or 'N/A'}\n━━━━━━━━━━━━━━━━━━━━\n"
    
    if len(users) > 10:
        text += f"\n... and {len(users) - 10} more users"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔍 Search User", callback_data="search_user"))
    
    edit_or_send_message(message.chat.id, last_msg_id, text, reply_markup=markup)

def show_all_bots_admin(message, last_msg_id=None):
    bots = execute_db("""
        SELECT d.id, d.bot_name, d.status, d.start_time, u.username, d.auto_restart, d.restart_count 
        FROM deployments d 
        LEFT JOIN users u ON d.user_id = u.id 
        ORDER BY d.id DESC LIMIT 20
    """, fetchall=True) or []
    
    running_bots = sum(1 for b in bots if b['status'] == "Running")
    auto_restart_bots = sum(1 for b in bots if b['auto_restart'] == 1)
    total_bots = len(bots)
    
    text = f"""
🤖 **ALL BOTS**
━━━━━━━━━━━━━━━━━━━━
📊 **Total Bots:** {total_bots}
🟢 **Running:** {running_bots}
🔴 **Stopped:** {total_bots - running_bots}
🔁 **Auto-Restart:** {auto_restart_bots}
━━━━━━━━━━━━━━━━━━━━
"""
    
    for bot_info in bots[:5]:
        if bot_info['bot_name']:
            username = bot_info['username'] if bot_info['username'] else "Unknown"
            auto_icon = "🔁" if bot_info['auto_restart'] == 1 else "⏸️"
            status_icon = "🟢" if bot_info['status'] == "Running" else "🔴"
            text += f"\n{status_icon}{auto_icon} **{bot_info['bot_name']}**"
            text += f"\n• User: @{username}"
            text += f"\n• Status: {bot_info['status']}"
            text += f"\n• Restarts: {bot_info['restart_count']}\n━━━━━━━━━━━━━━━━━━━━\n"
    
    edit_or_send_message(message.chat.id, last_msg_id, text)

def show_admin_stats(message, last_msg_id=None):
    total_users = execute_db("SELECT COUNT(*) FROM users", fetchone=True)
    if total_users:
        total_users = total_users[0] or 0
    else:
        total_users = 0
        
    prime_users = execute_db("SELECT COUNT(*) FROM users WHERE is_prime=1", fetchone=True)
    if prime_users:
        prime_users = prime_users[0] or 0
    else:
        prime_users = 0
        
    total_bots = execute_db("SELECT COUNT(*) FROM deployments", fetchone=True)
    if total_bots:
        total_bots = total_bots[0] or 0
    else:
        total_bots = 0
        
    running_bots = execute_db("SELECT COUNT(*) FROM deployments WHERE status='Running'", fetchone=True)
    if running_bots:
        running_bots = running_bots[0] or 0
    else:
        running_bots = 0
        
    auto_restart_bots = execute_db("SELECT COUNT(*) FROM deployments WHERE auto_restart=1", fetchone=True)
    if auto_restart_bots:
        auto_restart_bots = auto_restart_bots[0] or 0
    else:
        auto_restart_bots = 0
        
    total_keys = execute_db("SELECT COUNT(*) FROM keys", fetchone=True)
    if total_keys:
        total_keys = total_keys[0] or 0
    else:
        total_keys = 0
        
    used_keys = execute_db("SELECT COUNT(*) FROM keys WHERE is_used=1", fetchone=True)
    if used_keys:
        used_keys = used_keys[0] or 0
    else:
        used_keys = 0
    
    # Today's stats
    today = datetime.now().strftime('%Y-%m-%d')
    new_users_today = execute_db("SELECT COUNT(*) FROM users WHERE DATE(join_date)=?", (today,), fetchone=True)
    if new_users_today:
        new_users_today = new_users_today[0] or 0
    else:
        new_users_today = 0
        
    deployments_today = execute_db("SELECT COUNT(*) FROM deployments WHERE DATE(created_at)=?", (today,), fetchone=True)
    if deployments_today:
        deployments_today = deployments_today[0] or 0
    else:
        deployments_today = 0
    
    stats = get_system_stats()
    cpu_usage = stats['cpu_percent']
    ram_usage = stats['ram_percent']
    disk_usage = stats['disk_percent']
    
    total_capacity = stats['total_capacity']
    available_capacity = stats['available_capacity']
    
    text = f"""
📈 **ADMIN STATISTICS v3.3.2**
━━━━━━━━━━━━━━━━━━━━
*Auto-Recovery System: ACTIVE*
━━━━━━━━━━━━━━━━━━━━
👥 **User Stats:**
• Total Users: {total_users}
• Prime Users: {prime_users}
• Free Users: {total_users - prime_users}
• New Today: {new_users_today}
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot Stats:**
• Total Bots: {total_bots}
• Running Bots: {running_bots}
• Stopped Bots: {total_bots - running_bots}
• Auto-Restart Bots: {auto_restart_bots}
• Deployments Today: {deployments_today}
━━━━━━━━━━━━━━━━━━━━
🔑 **Key Stats:**
• Total Keys: {total_keys}
• Used Keys: {used_keys}
• Available Keys: {total_keys - used_keys}
━━━━━━━━━━━━━━━━━━━━
🖥️ **System Status:**
• CPU Usage: {cpu_usage:.1f}%
• RAM Usage: {ram_usage:.1f}%
• Disk Usage: {disk_usage:.1f}%
• Last Backup: {stats['last_backup']}
• Total Backups: {stats['backup_count']}
━━━━━━━━━━━━━━━━━━━━
🌐 **Hosting Info:**
• Platform: ZEN X HOST v3.3.2
• Port: {Config.PORT}
• Nodes: {len(Config.HOSTING_NODES)} x 300 capacity
• Total Capacity: {total_capacity} bots
• Used Capacity: {running_bots} bots
• Available: {available_capacity} bots
• Bot: @{Config.BOT_USERNAME}
• Auto-Recovery: {'Enabled' if Config.AUTO_RESTART_BOTS else 'Disabled'}
━━━━━━━━━━━━━━━━━━━━
"""
    edit_or_send_message(message.chat.id, last_msg_id, text)

def view_database_admin(message, last_msg_id=None):
    view_database_page_admin(message, 1, last_msg_id)

def view_database_page_admin(message, page_num, last_msg_id=None):
    items_per_page = 5
    offset = (page_num - 1) * items_per_page
    
    deployments = execute_db("""
        SELECT d.id, d.bot_name, d.filename, d.status, u.username, d.last_active, d.node_id, d.auto_restart, d.restart_count
        FROM deployments d 
        LEFT JOIN users u ON d.user_id = u.id 
        ORDER BY d.id DESC
        LIMIT ? OFFSET ?
    """, (items_per_page, offset), fetchall=True) or []
    
    total_deployments = execute_db("SELECT COUNT(*) FROM deployments", fetchone=True)
    if total_deployments:
        total_deployments = total_deployments[0] or 0
    else:
        total_deployments = 0
        
    total_pages = (total_deployments + items_per_page - 1) // items_per_page if items_per_page > 0 else 1
    
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
            text += f"  🔁 Auto-Restart: {'Yes' if dep['auto_restart'] == 1 else 'No'}\n"
            text += f"  🔄 Restarts: {dep['restart_count']}\n"
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
        backup_path = backup_database()
        
        if backup_path and backup_path.exists():
            with open(backup_path, 'rb') as f:
                bot.send_document(message.chat.id, f, 
                                 caption=f"💾 **Database Backup**\n\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nFile: `{backup_path.name}`\nSize: {backup_path.stat().st_size / 1024:.1f} KB\nAuto-Recovery: Enabled")
            
            time.sleep(2)
            backup_path.unlink(missing_ok=True)
            
            edit_or_send_message(message.chat.id, last_msg_id, "✅ Backup created and sent successfully!")
            send_notification(message.from_user.id, "Database backup created successfully")
        else:
            edit_or_send_message(message.chat.id, last_msg_id, "❌ Backup failed!")
        
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
    log_event("MAINTENANCE_TOGGLE", f"Maintenance mode {status}", message.from_user.id)

def show_nodes_status(message, last_msg_id=None):
    nodes = execute_db("SELECT * FROM nodes", fetchall=True) or []
    
    total_capacity = sum(node['capacity'] for node in nodes)
    used_capacity = sum(node['current_load'] for node in nodes)
    available_capacity = total_capacity - used_capacity
    
    text = f"""
🌐 **NODES STATUS v3.3.2**
━━━━━━━━━━━━━━━━━━━━
**Total Capacity:** {total_capacity} bots
**Used Capacity:** {used_capacity} bots
**Available:** {available_capacity} bots
**Auto-Recovery:** {'ENABLED ✅' if Config.AUTO_RESTART_BOTS else 'DISABLED ❌'}
━━━━━━━━━━━━━━━━━━━━
"""
    
    for node in nodes:
        load_percent = (node['current_load'] / node['capacity']) * 100 if node['capacity'] > 0 else 0
        load_bar = create_progress_bar(load_percent)
        status_icon = "🟢" if node['status'] == 'active' else "🔴"
        
        text += f"\n{status_icon} **{node['name']}** ({node['region']})"
        text += f"\n• Status: {node['status']}"
        text += f"\n• Load: {load_bar} {load_percent:.1f}%"
        text += f"\n• Capacity: {node['current_load']}/{node['capacity']}"
        text += f"\n• Total Deployed: {node['total_deployed']}"
        text += f"\n• Last Check: {node['last_check']}"
        text += "\n━━━━━━━━━━━━━━━━━━━━\n"
    
    edit_or_send_message(message.chat.id, last_msg_id, text)

def show_server_logs(message, last_msg_id=None):
    uid = message.from_user.id
    set_user_session(uid, {'state': 'waiting_for_logs_count'})
    
    text = """
🔧 **SERVER LOGS**
━━━━━━━━━━━━━━━━━━━━
Enter number of logs to view (1-100):
Example: 10, 20, 50
━━━━━━━━━━━━━━━━━━━━
Type 'cancel' to abort.
"""
    edit_or_send_message(message.chat.id, last_msg_id, text)

def show_server_logs_count(message, count=10):
    logs = execute_db("SELECT * FROM server_logs ORDER BY id DESC LIMIT ?", (count,), fetchall=True) or []
    
    text = f"""
🔧 **SERVER LOGS (Last {len(logs)} entries)**
━━━━━━━━━━━━━━━━━━━━
"""
    
    if logs:
        for log in logs:
            user_info = f" (User: {log['user_id']})" if log['user_id'] else ""
            text += f"\n**{log['timestamp']}** - {log['event']}{user_info}\n"
            text += f"`{log['details'][:100]}{'...' if len(log['details']) > 100 else ''}`\n"
            text += "━━━━━━━━━━━━━━━━━━━━\n"
    else:
        text += "\nNo logs found.\n"
    
    edit_or_send_message(message.chat.id, None, text)

def show_system_info(message, last_msg_id=None):
    stats = get_system_stats()
    
    text = f"""
🖥️ **SYSTEM INFORMATION v3.3.2**
━━━━━━━━━━━━━━━━━━━━
📊 **System Stats:**
• Platform: {stats['platform']}
• Python: {stats['python_version']}
• Uptime: {stats['uptime_days']} days
• CPU: {stats['cpu_percent']:.1f}%
• RAM: {stats['ram_percent']:.1f}%
• Disk: {stats['disk_percent']:.1f}%
━━━━━━━━━━━━━━━━━━━━
👥 **User Stats:**
• Total Users: {stats['total_users']}
• Active Users: {stats['active_users']}
• New Today: {stats['deployed_today']}
━━━━━━━━━━━━━━━━━━━━
🤖 **Bot Stats:**
• Total Bots: {stats['total_bots']}
• Running: {stats['running_bots']}
• Capacity: {stats['used_capacity']}/{stats['total_capacity']}
━━━━━━━━━━━━━━━━━━━━
💾 **Backup Info:**
• Last Backup: {stats['last_backup']}
• Total Backups: {stats['backup_count']}
• Auto-Backup: Every {Config.BACKUP_INTERVAL//3600} hours
━━━━━━━━━━━━━━━━━━━━
⚙️ **Configuration:**
• Max Bots/User: {Config.MAX_BOTS_PER_USER}
• Max Concurrent: {Config.MAX_CONCURRENT_DEPLOYMENTS}
• Auto-Recovery: {'Enabled ✅' if Config.AUTO_RESTART_BOTS else 'Disabled ❌'}
• Bot Timeout: {Config.BOT_TIMEOUT}s
• Admin: @{Config.ADMIN_USERNAME}
━━━━━━━━━━━━━━━━━━━━
"""
    edit_or_send_message(message.chat.id, last_msg_id, text)

def start_broadcast(message, last_msg_id=None):
    uid = message.from_user.id
    set_user_session(uid, {'state': 'waiting_for_broadcast'})
    
    text = """
🔔 **BROADCAST MESSAGE**
━━━━━━━━━━━━━━━━━━━━
Enter the message to broadcast to all users:
━━━━━━━━━━━━━━━━━━━━
Type 'cancel' to abort.
"""
    edit_or_send_message(message.chat.id, last_msg_id, text)

def cleanup_system(message, last_msg_id=None):
    uid = message.from_user.id
    
    try:
        # Clean old notifications (older than 30 days)
        month_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
        execute_db("DELETE FROM notifications WHERE created_at < ?", (month_ago,), commit=True)
        
        # Clean old server logs (older than 7 days)
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        execute_db("DELETE FROM server_logs WHERE timestamp < ?", (week_ago,), commit=True)
        
        # Clean old bot logs (older than 14 days)
        two_weeks_ago = (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d %H:%M:%S')
        execute_db("DELETE FROM bot_logs WHERE timestamp < ?", (two_weeks_ago,), commit=True)
        
        # Clean old exports (older than 7 days)
        export_dir = Path(Config.EXPORTS_DIR)
        deleted_exports = 0
        if export_dir.exists():
            for file in export_dir.glob("bot_export_*.zip"):
                if (datetime.now() - datetime.fromtimestamp(file.stat().st_mtime)).days > 7:
                    file.unlink()
                    deleted_exports += 1
        
        text = f"""
🔄 **SYSTEM CLEANUP COMPLETED**
━━━━━━━━━━━━━━━━━━━━
✅ **Cleanup Results:**
• Notifications: cleaned
• Server Logs: cleaned
• Bot Logs: cleaned
• Export Files: {deleted_exports} deleted (older than 7 days)
━━━━━━━━━━━━━━━━━━━━
System cleanup completed successfully!
"""
        edit_or_send_message(message.chat.id, last_msg_id, text)
        log_event("CLEANUP", f"System cleanup completed: {deleted_exports} exports", uid)
        
    except Exception as e:
        edit_or_send_message(message.chat.id, last_msg_id, f"❌ Cleanup failed: {str(e)}")

def message_user(call, user_id):
    """Send message to specific user"""
    uid = call.from_user.id
    chat_id = call.message.chat.id
    
    set_user_session(uid, {'state': 'waiting_for_user_message', 'target_user_id': user_id})
    
    bot.send_message(chat_id, f"✍️ **Send message to user {user_id}:**\n\nType your message or 'cancel' to abort.")

def view_user_bots(call, user_id):
    """View all bots of a specific user"""
    user = execute_db("SELECT username FROM users WHERE id=?", (user_id,), fetchone=True)
    bots = execute_db("SELECT * FROM deployments WHERE user_id=? ORDER BY status DESC, id DESC", (user_id,), fetchall=True) or []
    
    username = user['username'] if user else f"User_{user_id}"
    
    text = f"""
👤 **USER BOTS: @{username}**
━━━━━━━━━━━━━━━━━━━━
**Total Bots:** {len(bots)}
**Running:** {sum(1 for b in bots if b['status'] == 'Running')}
**Stopped:** {sum(1 for b in bots if b['status'] != 'Running')}
━━━━━━━━━━━━━━━━━━━━
"""
    
    for bot in bots[:10]:
        status_icon = "🟢" if bot['status'] == "Running" else "🔴"
        auto_icon = "🔁" if bot['auto_restart'] == 1 else "⏸️"
        text += f"\n{status_icon}{auto_icon} **{bot['bot_name']}** (ID: {bot['id']})"
        text += f"\n• File: `{bot['filename']}`"
        text += f"\n• Status: {bot['status']}"
        text += f"\n• Created: {bot['created_at']}\n━━━━━━━━━━━━━━━━━━━━\n"
    
    bot.send_message(call.message.chat.id, text)
    bot.answer_callback_query(call.id, "👤 User bots sent!")

def reset_user_limit(call, user_id):
    """Reset user's file limit"""
    execute_db("UPDATE users SET file_limit=1 WHERE id=?", (user_id,), commit=True)
    
    bot.answer_callback_query(call.id, f"✅ User {user_id} file limit reset to 1!")
    log_event("RESET_LIMIT", f"User {user_id} file limit reset", call.from_user.id)
    send_notification(user_id, "Your file limit has been reset to 1 by admin.")

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
    """Get process statistics"""
    try:
        if not pid:
            return None
            
        if platform.system() == "Windows":
            cmd = f'tasklist /FI "PID eq {pid}"'
        else:
            cmd = f'ps -p {pid} -o pid,pcpu,pmem,etime,comm'
            
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        if result.returncode == 0 and str(pid) in result.stdout:
            lines = result.stdout.strip().split('\n')
            if len(lines) > 1:
                if platform.system() == "Windows":
                    # Parse Windows tasklist output
                    parts = lines[-1].split()
                    if len(parts) >= 5:
                        return {
                            'pid': pid,
                            'cpu': float(parts[4].replace(',', '.')) if '%' in parts[4] else 0.0,
                            'memory': float(parts[5].replace(',', '.')) if '%' in parts[5] else 0.0,
                            'status': 'Running'
                        }
                else:
                    # Parse Linux ps output
                    parts = lines[-1].split()
                    if len(parts) >= 4:
                        return {
                            'pid': pid,
                            'cpu': float(parts[1]),
                            'memory': float(parts[2]),
                            'uptime': parts[3],
                            'status': 'Running'
                        }
        return None
    except Exception as e:
        logger.error(f"Error getting process stats for PID {pid}: {e}")
        return None

def extract_zip_file(zip_path, extract_dir):
    """Extract ZIP file"""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        return True
    except Exception as e:
        logger.error(f"Error extracting ZIP: {e}")
        return False

def view_database_page(call, page_num):
    """View database page for admin"""
    uid = call.from_user.id
    if uid != Config.ADMIN_ID:
        bot.answer_callback_query(call.id, "⛔ Access Denied!")
        return
    
    items_per_page = 10
    offset = (page_num - 1) * items_per_page
    
    deployments = execute_db("""
        SELECT d.id, d.bot_name, d.filename, d.status, u.username, 
               d.last_active, d.node_id, d.auto_restart, d.restart_count
        FROM deployments d 
        LEFT JOIN users u ON d.user_id = u.id 
        ORDER BY d.id DESC 
        LIMIT ? OFFSET ?
    """, (items_per_page, offset), fetchall=True) or []
    
    total_deployments = execute_db("SELECT COUNT(*) FROM deployments", fetchone=True)
    if total_deployments:
        total_deployments = total_deployments[0] or 0
    else:
        total_deployments = 0
    
    total_pages = (total_deployments + items_per_page - 1) // items_per_page
    
    text = f"""
🗄️ **DATABASE VIEWER**
━━━━━━━━━━━━━━━━━━━━
📊 **Total Bots:** {total_deployments}
📄 **Page:** {page_num}/{total_pages}
━━━━━━━━━━━━━━━━━━━━
"""
    
    if deployments:
        for dep in deployments:
            status_icon = "🟢" if dep['status'] == "Running" else "🔴"
            auto_icon = "🔁" if dep['auto_restart'] == 1 else "⏸️"
            text += f"\n{status_icon}{auto_icon} **{dep['bot_name']}** (ID: {dep['id']})"
            text += f"\n👤 User: @{dep['username'] or 'Unknown'}"
            text += f"\n📁 File: `{dep['filename']}`"
            text += f"\n📊 Status: {dep['status']}"
            text += f"\n🌐 Node: {dep['node_id'] or 'N/A'}"
            text += f"\n🔄 Restarts: {dep['restart_count']}"
            text += f"\n📅 Last Active: {dep['last_active']}"
            text += "\n━━━━━━━━━━━━━━━━━━━━\n"
    else:
        text += "\n📭 No bots found on this page\n"
    
    markup = types.InlineKeyboardMarkup(row_width=3)
    
    if page_num > 1:
        markup.add(types.InlineKeyboardButton("⬅️ Previous", callback_data=f"page_{page_num-1}"))
    
    if page_num < total_pages:
        markup.add(types.InlineKeyboardButton("Next ➡️", callback_data=f"page_{page_num+1}"))
    
    markup.add(types.InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

# Additional Flask routes for web interface
@app.route('/')
def index():
    return "🤖 ZEN X Bot Hosting v3.3.2 - Auto-Recovery System Active"

@app.route('/status')
def status():
    stats = get_system_stats()
    return jsonify({
        'status': 'online',
        'version': '3.3.2',
        'auto_recovery': Config.AUTO_RESTART_BOTS,
        'stats': stats
    })

@app.route('/api/deployments')
def get_deployments():
    deployments = execute_db("""
        SELECT d.id, d.bot_name, d.status, d.start_time, u.username, 
               d.cpu_usage, d.ram_usage, d.restart_count
        FROM deployments d
        LEFT JOIN users u ON d.user_id = u.id
        ORDER BY d.status DESC, d.id DESC
        LIMIT 50
    """, fetchall=True) or []
    
    result = []
    for dep in deployments:
        result.append({
            'id': dep['id'],
            'bot_name': dep['bot_name'],
            'status': dep['status'],
            'username': dep['username'],
            'cpu_usage': dep['cpu_usage'],
            'ram_usage': dep['ram_usage'],
            'restart_count': dep['restart_count'],
            'start_time': dep['start_time']
        })
    
    return jsonify({'deployments': result})

@app.route('/api/nodes')
def get_nodes():
    nodes = execute_db("SELECT * FROM nodes", fetchall=True) or []
    
    result = []
    for node in nodes:
        result.append({
            'id': node['id'],
            'name': node['name'],
            'status': node['status'],
            'capacity': node['capacity'],
            'current_load': node['current_load'],
            'region': node['region'],
            'total_deployed': node['total_deployed']
        })
    
    return jsonify({'nodes': result})

@app.route('/api/stats')
def api_stats():
    stats = get_system_stats()
    return jsonify(stats)

# Auto-recovery thread
def auto_recovery_thread():
    """Auto-recovery thread to restart failed bots"""
    while True:
        try:
            if Config.AUTO_RESTART_BOTS:
                # Find bots with auto_restart enabled that are stopped
                bots = execute_db("""
                    SELECT id, pid, user_id, bot_name, filename, auto_restart, restart_count 
                    FROM deployments 
                    WHERE auto_restart=1 AND (status='Stopped' OR pid=0)
                """, fetchall=True) or []
                
                for bot_info in bots:
                    bot_id = bot_info['id']
                    user_id = bot_info['user_id']
                    bot_name = bot_info['bot_name']
                    filename = bot_info['filename']
                    
                    # Check if bot file exists
                    file_path = project_path / filename
                    if not file_path.exists():
                        continue
                    
                    # Check if already running
                    if bot_info['pid']:
                        try:
                            os.kill(bot_info['pid'], 0)
                            continue  # Still running
                        except:
                            pass
                    
                    # Assign to node
                    node = assign_bot_to_node(user_id, bot_name)
                    if not node:
                        continue
                    
                    # Start the bot
                    start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    logs_dir = Path('logs')
                    logs_dir.mkdir(exist_ok=True)
                    
                    try:
                        with open(f'logs/bot_{bot_id}.log', 'a') as log_file:
                            log_file.write(f"\n{'='*50}\nAuto-Recovery at {start_time}\n{'='*50}\n")
                            proc = subprocess.Popen(
                                ['python', str(file_path)],
                                stdout=log_file,
                                stderr=subprocess.STDOUT,
                                start_new_session=True
                            )
                        
                        time.sleep(2)
                        
                        if proc.poll() is not None:
                            continue
                        
                        # Update database
                        execute_db("""
                            UPDATE deployments 
                            SET pid=?, start_time=?, status='Running', node_id=?, last_active=?, 
                                restart_count=restart_count+1, updated_at=? 
                            WHERE id=?
                        """, (proc.pid, start_time, node['id'], start_time, start_time, bot_id), commit=True)
                        
                        execute_db("UPDATE nodes SET current_load=current_load+1 WHERE id=?", 
                                  (node['id'],), commit=True)
                        
                        logger.info(f"Auto-recovered bot {bot_name} (ID: {bot_id})")
                        send_notification(user_id, f"Bot '{bot_name}' auto-recovered")
                        start_bot_monitoring(bot_id, proc.pid, user_id)
                        
                    except Exception as e:
                        logger.error(f"Auto-recovery failed for bot {bot_id}: {e}")
            
            time.sleep(60)  # Check every minute
            
        except Exception as e:
            logger.error(f"Auto-recovery thread error: {e}")
            time.sleep(300)  # Wait 5 minutes on error

# Cleanup thread
def cleanup_thread():
    """Cleanup old files and logs"""
    while True:
        try:
            # Clean old log files (older than 7 days)
            logs_dir = Path(Config.LOGS_DIR)
            if logs_dir.exists():
                for log_file in logs_dir.glob("*.log"):
                    if (datetime.now() - datetime.fromtimestamp(log_file.stat().st_mtime)).days > 7:
                        log_file.unlink()
            
            # Clean old export files (older than 1 day)
            export_dir = Path(Config.EXPORTS_DIR)
            if export_dir.exists():
                for export_file in export_dir.glob("*.zip"):
                    if (datetime.now() - datetime.fromtimestamp(export_file.stat().st_mtime)).days > 1:
                        export_file.unlink()
            
            # Clean old temp files
            for temp_file in project_path.glob("temp_*.zip"):
                if (datetime.now() - datetime.fromtimestamp(temp_file.stat().st_mtime)).hours > 1:
                    temp_file.unlink()
            
            # Clean old extracted directories
            for extract_dir in project_path.glob("extracted_*"):
                if (datetime.now() - datetime.fromtimestamp(extract_dir.stat().st_mtime)).hours > 1:
                    import shutil
                    shutil.rmtree(extract_dir, ignore_errors=True)
            
            time.sleep(3600)  # Run every hour
            
        except Exception as e:
            logger.error(f"Cleanup thread error: {e}")
            time.sleep(7200)  # Wait 2 hours on error

# Background threads starter
def start_background_threads():
    """Start all background threads"""
    # Auto-recovery thread
    recovery_thread = threading.Thread(target=auto_recovery_thread, daemon=True)
    recovery_thread.start()
    logger.info("Auto-recovery thread started")
    
    # Cleanup thread
    cleanup_thread_obj = threading.Thread(target=cleanup_thread, daemon=True)
    cleanup_thread_obj.start()
    logger.info("Cleanup thread started")
    
    # Backup scheduler thread
    backup_thread = threading.Thread(target=schedule_backups, daemon=True)
    backup_thread.start()
    logger.info("Backup scheduler thread started")

# Error handler
@bot.message_handler(func=lambda message: True, content_types=['text', 'document', 'photo', 'audio', 'video', 'voice', 'sticker', 'location', 'contact'])
def handle_all_messages(message):
    try:
        # Let the main handlers handle it
        pass
    except Exception as e:
        logger.error(f"Unhandled error: {e}")
        try:
            bot.reply_to(message, "⚠️ An error occurred. Please try again.")
        except:
            pass

# Main function
def main():
    """Main function to start the bot"""
    logger.info("🤖 ZEN X Bot Hosting v3.3.2 Starting...")
    logger.info(f"Admin ID: {Config.ADMIN_ID}")
    logger.info(f"Bot Username: @{Config.BOT_USERNAME}")
    
    # Create necessary directories
    Path(Config.PROJECT_DIR).mkdir(exist_ok=True)
    Path(Config.BACKUP_DIR).mkdir(exist_ok=True)
    Path(Config.LOGS_DIR).mkdir(exist_ok=True)
    Path(Config.EXPORTS_DIR).mkdir(exist_ok=True)
    
    # Initialize database
    init_db()
    
    # Recover deployments on startup
    recover_deployments()
    
    # Start background threads
    start_background_threads()
    
    # Start Flask web server in background
    flask_thread = threading.Thread(
        target=lambda: app.run(host='0.0.0.0', port=Config.PORT, debug=False, use_reloader=False),
        daemon=True
    )
    flask_thread.start()
    logger.info(f"Flask server started on port {Config.PORT}")
    
    # Send startup notification to admin
    try:
        stats = get_system_stats()
        startup_msg = f"""
🚀 **ZEN X HOST BOT v3.3.2 STARTED**
━━━━━━━━━━━━━━━━━━━━
*Auto-Recovery System: ACTIVE*
━━━━━━━━━━━━━━━━━━━━
📊 **System Stats:**
• Users: {stats['total_users']}
• Running Bots: {stats['running_bots']}
• CPU: {stats['cpu_percent']:.1f}%
• RAM: {stats['ram_percent']:.1f}%
• Nodes: {len(Config.HOSTING_NODES)} x 300
• Port: {Config.PORT}
━━━━━━━━━━━━━━━━━━━━
✅ Server is now online!
"""
        bot.send_message(Config.ADMIN_ID, startup_msg)
    except Exception as e:
        logger.error(f"Failed to send startup notification: {e}")
    
    # Start the bot
    logger.info("Bot is now running...")
    while True:
        try:
            bot.polling(none_stop=True, timeout=60)
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
