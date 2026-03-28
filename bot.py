import os
import re
import asyncio
import logging
import sys
import tempfile
import time
import json
import sqlite3
from datetime import datetime, timedelta
import requests
from urllib.parse import urlparse
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ChatAction
from telegram.error import TelegramError

# После всех импортов, перед классами
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")
    
    def log_message(self, format, *args):
        pass  # Отключаем логи

def start_health_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"✅ Health check server started on port {port}")
    server.serve_forever()

# Запускаем health check сервер в отдельном потоке
if os.environ.get('RENDER'):
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()

# Исправление для Windows
if sys.platform.startswith('win'):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Настройка логирования
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Отключаем логирование библиотек
logging.getLogger("telegram").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("httpcore").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)

# Конфигурация бота
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_USERNAME = "@akhmadow_077"
ADMIN_USER_ID = 6080696151  # ЗАМЕНИТЕ НА ВАШ ID от @userinfobot
MAX_FILE_SIZE = 50 * 1024 * 1024
DATABASE_FILE = "bot_users.db"

class DatabaseManager:
    def __init__(self, db_file):
        self.db_file = db_file
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                phone_number TEXT,
                registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                downloads_count INTEGER DEFAULT 0,
                is_blocked INTEGER DEFAULT 0,
                contact_shared INTEGER DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                url TEXT,
                platform TEXT,
                download_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                success INTEGER DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT,
                sent_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                broadcast_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        try:
            cursor.execute('ALTER TABLE users ADD COLUMN contact_shared INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        
        conn.commit()
        conn.close()
    
    def add_user(self, user_id, username=None, first_name=None, last_name=None, phone_number=None):
        """Добавление нового пользователя"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('SELECT contact_shared, phone_number FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        
        if result:
            contact_shared, existing_phone = result
            if contact_shared and existing_phone:
                cursor.execute('''
                    UPDATE users SET last_activity = CURRENT_TIMESTAMP 
                    WHERE user_id = ?
                ''', (user_id,))
                conn.commit()
                conn.close()
                
                user_info = f"{first_name or 'Неизвестно'} {last_name or ''}".strip()
                print(f"🟢 Пользователь {user_info} (ID: {user_id}) по номеру {existing_phone} подключен")
                return
        
        cursor.execute('''
            INSERT OR REPLACE INTO users 
            (user_id, username, first_name, last_name, phone_number, last_activity, contact_shared)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
        ''', (user_id, username, first_name, last_name, phone_number, 1 if phone_number else 0))
        
        conn.commit()
        conn.close()
        
        user_info = f"{first_name or 'Неизвестно'} {last_name or ''}".strip()
        phone_info = phone_number or "не указан"
        if phone_number:
            print(f"🟢 Пользователь {user_info} (ID: {user_id}) по номеру {phone_info} подключен")
        else:
            print(f"🟢 Пользователь {user_info} (ID: {user_id}) подключен без номера")
    
    def update_contact_info(self, user_id, phone_number):
        """Обновление контактной информации пользователя"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE users SET phone_number = ?, contact_shared = 1, last_activity = CURRENT_TIMESTAMP 
            WHERE user_id = ?
        ''', (phone_number, user_id))
        
        conn.commit()
        conn.close()
    
    def check_contact_shared(self, user_id):
        """Проверка, отправлял ли пользователь контакт"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('SELECT contact_shared FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        
        conn.close()
        return result and result[0] == 1
    
    def update_user_activity(self, user_id):
        """Обновление последней активности пользователя"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE users SET last_activity = CURRENT_TIMESTAMP 
            WHERE user_id = ?
        ''', (user_id,))
        
        conn.commit()
        conn.close()
    
    def add_download(self, user_id, url, platform, success=True):
        """Добавление записи о загрузке"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO downloads (user_id, url, platform, success)
            VALUES (?, ?, ?, ?)
        ''', (user_id, url, platform, 1 if success else 0))
        
        if success:
            cursor.execute('''
                UPDATE users SET downloads_count = downloads_count + 1 
                WHERE user_id = ?
            ''', (user_id,))
        
        conn.commit()
        conn.close()
    
    def get_all_users(self):
        """Получение всех пользователей для рассылки"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT user_id FROM users WHERE is_blocked = 0
        ''')
        
        users = [row[0] for row in cursor.fetchall()]
        conn.close()
        return users
    
    def get_stats(self):
        """Получение статистики"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM users WHERE is_blocked = 0')
        active_users = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM downloads WHERE success = 1')
        total_downloads = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT COUNT(*) FROM users 
            WHERE date(last_activity) = date('now')
        ''')
        today_active = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT platform, COUNT(*) FROM downloads 
            WHERE success = 1 GROUP BY platform
        ''')
        platform_stats = dict(cursor.fetchall())
        
        conn.close()
        
        return {
            'total_users': total_users,
            'active_users': active_users,
            'total_downloads': total_downloads,
            'today_active': today_active,
            'platform_stats': platform_stats
        }
    
    def block_user(self, user_id):
        """Блокировка пользователя"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE users SET is_blocked = 1 WHERE user_id = ?
        ''', (user_id,))
        
        conn.commit()
        conn.close()
        
        print(f"🔴 Пользователь {user_id} заблокировал бота")

class PowerfulVideoBot:
    def __init__(self):
        self.db = DatabaseManager(DATABASE_FILE)
        
        self.supported_platforms = {
            'instagram.com': 'Instagram',
            'tiktok.com': 'TikTok',
            'vm.tiktok.com': 'TikTok',
            'vt.tiktok.com': 'TikTok',
            'www.instagram.com': 'Instagram',
            'www.tiktok.com': 'TikTok'
        }
        
        self.base_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'writethumbnail': False,
            'writeinfojson': False,
            'noplaylist': True,
            'ignoreerrors': True,
            'no_check_certificate': True,
            'prefer_insecure': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
        }
        
    def is_supported_url(self, url: str) -> tuple:
        """Проверка поддержки URL"""
        url_lower = url.lower()
        for platform, name in self.supported_platforms.items():
            if platform in url_lower:
                return True, name
        return False, None

    def clean_url(self, url: str) -> str:
        """Очистка URL от лишних параметров"""
        if 'instagram.com' in url:
            pattern = r'(https?://(?:www\.)?instagram\.com/(?:p|reel)/[A-Za-z0-9_-]+)'
            match = re.search(pattern, url)
            if match:
                return match.group(1) + '/'
        elif 'tiktok.com' in url:
            if 'vm.tiktok.com' in url or 'vt.tiktok.com' in url:
                return url.split('?')[0]
            pattern = r'(https?://(?:www\.)?tiktok\.com/@[^/]+/video/\d+)'
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return url.split('?')[0]

    def get_multiple_formats_sync(self, url: str):
        """Синхронная версия получения видео"""
        cleaned_url = self.clean_url(url)
        
        format_attempts = [
            {
                **self.base_opts,
                'format': 'best[height<=720]/best[height<=480]/best',
            },
            {
                **self.base_opts,
                'format': 'mp4[height<=720]/mp4/best[ext=mp4]/best',
                'merge_output_format': 'mp4',
            },
            {
                **self.base_opts,
                'format': 'worst[height>=240]/worst',
            },
            {
                **self.base_opts,
                'format': 'best',
                'extractor_args': {
                    'instagram': {
                        'comment_count': 0,
                        'like_count': 0,
                    }
                }
            }
        ]
        
        last_error = None
        
        for i, opts in enumerate(format_attempts, 1):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(cleaned_url, download=False)
                    if info and 'entries' not in info:
                        return opts, info, cleaned_url
                    elif info and 'entries' in info and info['entries']:
                        first_entry = info['entries'][0]
                        if first_entry:
                            return opts, first_entry, cleaned_url
            except Exception as e:
                last_error = e
                continue
        
        if cleaned_url != url:
            for i, opts in enumerate(format_attempts[:2], 1):
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                        if info:
                            return opts, info, url
                except Exception as e:
                    last_error = e
                    continue
        
        raise Exception(f"Не удалось загрузить видео после всех попыток. Последняя ошибка: {str(last_error)[:200]}")

    async def download_video(self, url: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Быстрое скачивание видео с множественными попытками"""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        start_time = time.time()
        
        self.db.update_user_activity(user_id)
        
        is_supported, platform = self.is_supported_url(url)
        
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        progress_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="⚡ <b>Анализирую ссылку...</b>",
            parse_mode='HTML'
        )
        
        try:
            await progress_msg.edit_text(
                "🔍 <b>Проверяю доступность...</b>\n▓░░░░░░░░░ 15%",
                parse_mode='HTML'
            )
            
            try:
                opts, info, final_url = await asyncio.get_event_loop().run_in_executor(
                    None, self.get_multiple_formats_sync, url
                )
            except Exception as e:
                simple_opts = {
                    **self.base_opts,
                    'format': 'best',
                    'no_check_certificate': True,
                }
                try:
                    with yt_dlp.YoutubeDL(simple_opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                        if not info:
                            raise Exception("Видео недоступно или удалено")
                        opts = simple_opts
                        final_url = url
                except Exception as simple_error:
                    self.db.add_download(user_id, url, platform or 'Unknown', success=False)
                    raise Exception(f"Видео недоступно. Возможные причины: приватный аккаунт, удаленное видео или блокировка региона")
            
            title = (info.get('title') or info.get('description') or 'video')[:40]
            duration = info.get('duration', 0)
            
            if duration > 600:
                await progress_msg.edit_text(
                    "⏰ <b>Предупреждение!</b>\nВидео очень длинное, загрузка может занять время...",
                    parse_mode='HTML'
                )
                await asyncio.sleep(2)
            
            await progress_msg.edit_text(
                f"📱 <b>{title}</b>\n📥 Загружаю...\n▓▓▓░░░░░░░ 35%",
                parse_mode='HTML'
            )
            
            with tempfile.TemporaryDirectory() as temp_dir:
                download_opts = {
                    **opts,
                    'outtmpl': os.path.join(temp_dir, '%(title).40s.%(ext)s'),
                }
                
                await progress_msg.edit_text(
                    f"📱 <b>{title}</b>\n⬇️ Скачиваю видео...\n▓▓▓▓▓░░░░░ 50%",
                    parse_mode='HTML'
                )
                
                def download_task():
                    with yt_dlp.YoutubeDL(download_opts) as ydl:
                        ydl.download([final_url])
                        return True
                
                success = await asyncio.get_event_loop().run_in_executor(None, download_task)
                
                if not success:
                    raise Exception("Не удалось скачать видео")
                
                await progress_msg.edit_text(
                    f"📱 <b>{title}</b>\n🔄 Обрабатываю файл...\n▓▓▓▓▓▓▓░░░ 70%",
                    parse_mode='HTML'
                )
                
                video_files = []
                for file in os.listdir(temp_dir):
                    if file.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
                        video_files.append(file)
                
                if not video_files:
                    raise Exception("Файл не найден после загрузки")
                
                video_path = os.path.join(temp_dir, video_files[0])
                file_size = os.path.getsize(video_path)
                
                if file_size > MAX_FILE_SIZE:
                    size_mb = file_size / (1024 * 1024)
                    await progress_msg.edit_text(
                        f"📦 <b>Файл слишком большой!</b>\n"
                        f"📏 Размер: {size_mb:.1f}MB\n"
                        f"⚠️ Максимум: 50MB\n\n"
                        f"💡 Попробуйте другое видео",
                        parse_mode='HTML'
                    )
                    return
                
                if file_size < 1024:
                    raise Exception("Получен пустой файл")
                
                await progress_msg.edit_text(
                    f"📱 <b>{title}</b>\n📤 Отправляю вам...\n▓▓▓▓▓▓▓▓▓░ 90%",
                    parse_mode='HTML'
                )
                
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                
                with open(video_path, 'rb') as video_file:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=video_file,
                        caption=f"✅ <b>{title}</b>\n\n⚡ Загружено за {time.time() - start_time:.1f}с\n💫 @Nurkosbot",
                        parse_mode='HTML',
                        supports_streaming=True,
                        read_timeout=180,
                        write_timeout=180
                    )
                
                await progress_msg.delete()
                
                self.db.add_download(user_id, url, platform or 'Unknown', success=True)
                
                keyboard = [
                    [InlineKeyboardButton("🔥 Скачать ещё", callback_data="download_more")],
                    [InlineKeyboardButton("⭐ Оценить бота", callback_data="rate_bot")],
                    [InlineKeyboardButton("👨‍💻 Админ", url="https://t.me/akhmadow_077")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="🎉 <b>Готово!</b>\n\n🔥 Скачать ещё видео? Просто отправьте ссылку!",
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                
        except Exception as e:
            self.db.add_download(user_id, url, platform or 'Unknown', success=False)
            
            error_msg = str(e).lower()
            
            if 'private' in error_msg or 'login' in error_msg:
                error_text = "🔒 <b>Аккаунт или видео приватное!</b>\n\n💡 Могу скачать только публичные видео"
            elif 'not found' in error_msg or '404' in error_msg:
                error_text = "❌ <b>Видео не найдено!</b>\n\n💡 Возможно, оно удалено или ссылка неверная"
            elif 'region' in error_msg or 'geo' in error_msg:
                error_text = "🌍 <b>Видео заблокировано в вашем регионе</b>\n\n💡 Попробуйте другое видео"
            elif 'copyright' in error_msg or 'dmca' in error_msg:
                error_text = "⚖️ <b>Видео заблокировано из-за авторских прав</b>\n\n💡 Попробуйте другое видео"
            elif 'rate limit' in error_msg or 'too many' in error_msg:
                error_text = "⏳ <b>Слишком много запросов</b>\n\n💡 Подождите несколько минут и попробуйте снова"
            elif 'format' in error_msg:
                error_text = "🎬 <b>Неподдерживаемый формат видео</b>\n\n💡 Попробуйте другую ссылку"
            else:
                error_text = "⚡ <b>Не удалось загрузить видео</b>\n\n🔧 Возможные причины:\n• Неверная ссылка\n• Приватный аккаунт\n• Временные проблемы с сервисом"
            
            keyboard = [
                [InlineKeyboardButton("🔄 Попробовать снова", callback_data="try_again")],
                [InlineKeyboardButton("📱 Поддерживаемые сайты", callback_data="supported_sites")],
                [InlineKeyboardButton("👨‍💻 Связаться с админом", url="https://t.me/akhmadow_077")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await progress_msg.edit_text(
                error_text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )

bot = PowerfulVideoBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.effective_user
    
    contact_shared = bot.db.check_contact_shared(user.id)
    
    bot.db.add_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    if not contact_shared:
        contact_keyboard = ReplyKeyboardMarkup([
            [KeyboardButton("📱 Поделиться номером", request_contact=True)]
        ], resize_keyboard=True, one_time_keyboard=True)
        
        await update.message.reply_text(
            "👋 <b>Добро пожаловать!</b>\n\n"
            "🔐 Для полного доступа к боту, поделитесь своим номером телефона:",
            parse_mode='HTML',
            reply_markup=contact_keyboard
        )
        return
    
    keyboard = [
        [InlineKeyboardButton("🚀 Как пользоваться", callback_data="how_to_use")],
        [InlineKeyboardButton("📱 Поддерживаемые сайты", callback_data="supported_sites")],
        [InlineKeyboardButton("👨‍💻 Связаться с админом", url="https://t.me/akhmadow_077")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = (
        "🎬 <b>Akhmadov's Video Downloader Bot 2.0</b>\n\n"
        "⚡ <b>Быстро скачиваю видео с:</b>\n"
        "📸 Instagram (Reels, Posts, Stories)\n"
        "🎵 TikTok (все видео)\n\n"
        "🔥 <b>Просто отправьте ссылку!</b>\n\n"
        "💎 Молниеносно  🆓 Бесплатно"
    )
    
    await update.message.reply_text(
        welcome_text,
        parse_mode='HTML',
        reply_markup=reply_markup
    )

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка контакта пользователя"""
    contact = update.message.contact
    user = update.effective_user
    
    if contact.user_id == user.id:
        bot.db.update_contact_info(user.id, contact.phone_number)
        
        user_info = f"{user.first_name or 'Неизвестно'} {user.last_name or ''}".strip()
        print(f"📞 Пользователь {user_info} (ID: {user.id}) поделился номером {contact.phone_number}")
        
        await update.message.reply_text(
            "✅ <b>Спасибо!</b>\n\n"
            "Теперь вы можете полноценно пользоваться ботом.\n"
            "📝 Отправьте ссылку на видео для загрузки!",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardRemove()
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /stats - только для админа"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ У вас нет прав для просмотра статистики.")
        return
    
    stats = bot.db.get_stats()
    platform_text = "\n".join([f"• {platform}: {count}" for platform, count in stats['platform_stats'].items()])
    
    stats_text = f"""📊 <b>Статистика бота</b>

👥 <b>Пользователи:</b>
• Всего: {stats['total_users']}
• Активные: {stats['active_users']}
• Сегодня активны: {stats['today_active']}

📥 <b>Загрузки:</b>
• Всего: {stats['total_downloads']}

🌐 <b>По платформам:</b>
{platform_text or "Загрузок пока нет"}

🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}"""
    
    await update.message.reply_text(stats_text, parse_mode='HTML')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    help_text = (
        "🆘 <b>Помощь - Video Downloader Bot</b>\n\n"
        "📱 <b>Поддерживаемые платформы:</b>\n"
        "• Instagram (все типы постов)\n"
        "• TikTok (включая короткие ссылки)\n\n"
        "⚡ <b>Как использовать:</b>\n"
        "1. Скопируйте ссылку на видео\n"
        "2. Отправьте её в этот чат\n"
        "3. Получите видео за секунды!\n\n"
        "🔥 <b>Фишки бота:</b>\n"
        "• Молниеносная скорость\n"
        "• HD качество\n"
        "• Без водяных знаков\n"
        "• Работает 24/7"
    )
    
    keyboard = [
        [InlineKeyboardButton("👨‍💻 Админ", url="https://t.me/akhmadow_077")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(help_text, parse_mode='HTML', reply_markup=reply_markup)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /broadcast - рассылка сообщения (только для админа)"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ У вас нет прав для рассылки.")
        return
    
    if len(context.args) == 0:
        await update.message.reply_text(
            "📢 <b>Использование:</b>\n"
            "/broadcast ваше сообщение\n\n"
            "<b>Пример:</b>\n"
            "/broadcast Привет! Новая версия бота уже доступна!",
            parse_mode='HTML'
        )
        return
    
    message = ' '.join(context.args)
    users = bot.db.get_all_users()
    
    if not users:
        await update.message.reply_text("❌ Нет пользователей для рассылки.")
        return
    
    await update.message.reply_text(f"🚀 Начинаю рассылку {len(users)} пользователям...")
    
    sent_count = 0
    failed_count = 0
    
    for user_id_broadcast in users:
        try:
            await context.bot.send_message(
                chat_id=user_id_broadcast,
                text=message,
                parse_mode='HTML'
            )
            sent_count += 1
            await asyncio.sleep(0.1)
        except TelegramError as e:
            failed_count += 1
            if "blocked" in str(e).lower():
                bot.db.block_user(user_id_broadcast)
        except Exception:
            failed_count += 1
    
    await update.message.reply_text(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📤 Отправлено: {sent_count}\n"
        f"❌ Ошибок: {failed_count}",
        parse_mode='HTML'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сообщений с URL"""
    message_text = update.message.text
    user_id = update.effective_user.id
    
    bot.db.update_user_activity(user_id)
    
    url_pattern = r'https?://[^\s]+'
    urls = re.findall(url_pattern, message_text)
    
    if not urls:
        keyboard = [
            [InlineKeyboardButton("🚀 Как пользоваться", callback_data="how_to_use")],
            [InlineKeyboardButton("👨‍💻 Админ", url="https://t.me/akhmadow_077")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "❓ <b>Отправьте ссылку на видео!</b>\n\n"
            "📝 <b>Пример:</b>\n"
            "https://www.instagram.com/p/...\n"
            "https://www.tiktok.com/@user/video/...",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return
    
    url = urls[0]
    is_supported, platform = bot.is_supported_url(url)
    
    if is_supported:
        await update.message.reply_text(
            f"⚡ <b>{platform} обнаружен!</b>\n🚀 Начинаю загрузку...",
            parse_mode='HTML'
        )
        await bot.download_video(url, update, context)
    else:
        keyboard = [
            [InlineKeyboardButton("📱 Поддерживаемые сайты", callback_data="supported_sites")],
            [InlineKeyboardButton("👨‍💻 Админ", url="https://t.me/akhmadow_077")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "❌ <b>Неподдерживаемый сайт!</b>\n\n"
            "✅ Я работаю с:\n• Instagram\n• TikTok",
            parse_mode='HTML',
            reply_markup=reply_markup
        )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок"""
    query = update.callback_query
    await query.answer()
    
    bot.db.update_user_activity(query.from_user.id)
    
    texts = {
        "how_to_use": (
            "🚀 <b>Как пользоваться:</b>\n\n"
            "1️⃣ Откройте Instagram или TikTok\n"
            "2️⃣ Найдите нужное видео\n"
            "3️⃣ Нажмите 'Поделиться' → 'Копировать ссылку'\n"
            "4️⃣ Отправьте ссылку в этот чат\n"
            "5️⃣ Получите видео за секунды! ⚡\n\n"
            "🔥 Это очень просто!"
        ),
        "supported_sites": (
            "📱 <b>Поддерживаемые сайты:</b>\n\n"
            "📸 <b>Instagram:</b>\n"
            "• Обычные посты\n"
            "• Reels (короткие видео)\n"
            "• Stories (истории)\n"
            "• IGTV видео\n\n"
            "🎵 <b>TikTok:</b>\n"
            "• Любые видео\n"
            "• Короткие ссылки (vm.tiktok.com)\n"
            "• Все форматы и качества"
        ),
        "download_more": (
            "🔥 <b>Готов к новому скачиванию!</b>\n\n"
            "📝 Отправляйте следующую ссылку\n⚡ Я работаю очень быстро!"
        ),
        "try_again": (
            "🔄 <b>Попробуем ещё раз!</b>\n\n"
            "💡 <b>Советы:</b>\n"
            "• Убедитесь, что аккаунт публичный\n"
            "• Проверьте корректность ссылки\n"
            "• Попробуйте скопировать ссылку заново"
        ),
        "rate_bot": (
            "⭐ <b>Спасибо за использование бота!</b>\n\n"
            "🔥 Если бот помог вам:\n"
            "• Расскажите друзьям\n"
            "• Оцените работу\n"
            "• Свяжитесь с админом для предложений\n\n"
            "💫 Ваш отзыв важен для нас!"
        ),
        "main_menu": (
            "🏠 <b>Главное меню</b>\n\n"
            "🎬 Отправьте ссылку на видео для загрузки!\n"
            "⚡ Поддерживаются Instagram и TikTok"
        )
    }
    
    text = texts.get(query.data, "Отправьте ссылку на видео!")
    
    if query.data == "main_menu":
        keyboard = [
            [InlineKeyboardButton("🚀 Как пользоваться", callback_data="how_to_use")],
            [InlineKeyboardButton("📱 Поддерживаемые сайты", callback_data="supported_sites")],
            [InlineKeyboardButton("👨‍💻 Связаться с админом", url="https://t.me/akhmadow_077")]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
            [InlineKeyboardButton("👨‍💻 Админ", url="https://t.me/akhmadow_077")]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)

def main():
    """Запуск бота на Render"""
    print("🚀 Запуск Video Downloader Bot на Render...")
    print("⚡ Версия 3.0")
    print("📱 Админ: @akhmadow_077")
    print(f"📊 База данных: {DATABASE_FILE}")
    print("="*50)
    
    try:
        # Создаем приложение с таймаутами для Render
        application = (
            Application.builder()
            .token(BOT_TOKEN)
            .connect_timeout(30)
            .read_timeout(30)
            .write_timeout(30)
            .pool_timeout(30)
            .build()
        )
        
        # Регистрируем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("broadcast", broadcast_command))
        application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
        application.add_handler(CallbackQueryHandler(button_callback))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("🔥 Бот запущен и готов работать!")
        
        # Простой запуск для Render
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
    except KeyboardInterrupt:
        print("\n👋 Остановка бота...")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()