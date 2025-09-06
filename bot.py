import os
import re
import asyncio
import logging
import sys
import tempfile
import time
import requests
from urllib.parse import urlparse
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ChatAction

# Исправление для Windows
if sys.platform.startswith('win'):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.WARNING  # Уменьшаем количество логов
)
logger = logging.getLogger(__name__)

# # Конфигурация бота
# BOT_TOKEN = "7984182309:AAFrKUlavoB9UA06JT8VSTfaf0XKXaP2T7k"
# ADMIN_USERNAME = "@akhmadow_077"
# MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB лимит Telegram

import os
from telegram.ext import Application

# Токен теперь берём из Render → Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Остальные настройки оставь как есть
ADMIN_USERNAME = "@akhmadow_077"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB лимит Telegram

application = Application.builder().token(BOT_TOKEN).build()

class PowerfulVideoBot:
    def __init__(self):
        self.supported_platforms = {
            'instagram.com': 'Instagram',
            'tiktok.com': 'TikTok',
            'vm.tiktok.com': 'TikTok',
            'vt.tiktok.com': 'TikTok',
            'www.instagram.com': 'Instagram',
            'www.tiktok.com': 'TikTok'
        }
        
        # Улучшенные настройки yt-dlp
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
            # Добавляем User-Agent для обхода блокировок
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
        # Убираем utm параметры и прочий мусор
        if 'instagram.com' in url:
            # Извлекаем основную часть Instagram URL
            pattern = r'(https?://(?:www\.)?instagram\.com/(?:p|reel)/[A-Za-z0-9_-]+)'
            match = re.search(pattern, url)
            if match:
                return match.group(1) + '/'
        elif 'tiktok.com' in url:
            # Очищаем TikTok URL
            if 'vm.tiktok.com' in url or 'vt.tiktok.com' in url:
                return url.split('?')[0]
            pattern = r'(https?://(?:www\.)?tiktok\.com/@[^/]+/video/\d+)'
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return url.split('?')[0]  # Убираем параметры по умолчанию

    async def get_multiple_formats(self, url: str):
        """Получение видео с несколькими попытками и форматами"""
        cleaned_url = self.clean_url(url)
        
        # Список различных конфигураций для попыток
        format_attempts = [
            # Попытка 1: Лучшее качество до 720p
            {
                **self.base_opts,
                'format': 'best[height<=720]/best[height<=480]/best',
            },
            # Попытка 2: Принудительно mp4
            {
                **self.base_opts,
                'format': 'mp4[height<=720]/mp4/best[ext=mp4]/best',
                'merge_output_format': 'mp4',
            },
            # Попытка 3: Любое доступное видео
            {
                **self.base_opts,
                'format': 'worst[height>=240]/worst',
            },
            # Попытка 4: Для Instagram Stories и сложных случаев
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
                    # Сначала пробуем получить информацию
                    info = ydl.extract_info(cleaned_url, download=False)
                    if info and 'entries' not in info:
                        return opts, info, cleaned_url
                    elif info and 'entries' in info and info['entries']:
                        # Если это плейлист, берем первое видео
                        first_entry = info['entries'][0]
                        if first_entry:
                            return opts, first_entry, cleaned_url
            except Exception as e:
                last_error = e
                logger.warning(f"Попытка {i} не удалась: {str(e)[:100]}")
                continue
        
        # Если все попытки неудачны, пробуем оригинальный URL
        if cleaned_url != url:
            for i, opts in enumerate(format_attempts[:2], 1):  # Только первые 2 попытки
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
        start_time = time.time()
        
        # Отправляем статус
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        # Быстрое сообщение о начале
        progress_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="⚡ <b>Анализирую ссылку...</b>",
            parse_mode='HTML'
        )
        
        try:
            # Проверяем доступность URL
            await progress_msg.edit_text(
                "🔍 <b>Проверяю доступность...</b>\n▓░░░░░░░░░ 15%",
                parse_mode='HTML'
            )
            
            # Получаем оптимальные настройки и информацию о видео
            try:
                opts, info, final_url = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: asyncio.run(self.get_multiple_formats(url))
                )
            except Exception as e:
                # Если основной метод не работает, пробуем упрощенный подход
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
                    raise Exception(f"Видео недоступно. Возможные причины: приватный аккаунт, удаленное видео или блокировка региона")
            
            title = (info.get('title') or info.get('description') or 'video')[:40]
            duration = info.get('duration', 0)
            
            if duration > 600:  # 10 минут
                await progress_msg.edit_text(
                    "⏰ <b>Предупреждение!</b>\nВидео очень длинное, загрузка может занять время...",
                    parse_mode='HTML'
                )
                await asyncio.sleep(2)
            
            await progress_msg.edit_text(
                f"📱 <b>{title}</b>\n📥 Загружаю...\n▓▓▓░░░░░░░ 35%",
                parse_mode='HTML'
            )
            
            # Создаем временную папку
            with tempfile.TemporaryDirectory() as temp_dir:
                # Обновляем настройки с путем
                download_opts = {
                    **opts,
                    'outtmpl': os.path.join(temp_dir, '%(title).40s.%(ext)s'),
                }
                
                await progress_msg.edit_text(
                    f"📱 <b>{title}</b>\n⬇️ Скачиваю видео...\n▓▓▓▓▓░░░░░ 50%",
                    parse_mode='HTML'
                )
                
                # Скачиваем видео в отдельном потоке
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
                
                # Ищем загруженный файл
                video_files = []
                for file in os.listdir(temp_dir):
                    if file.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
                        video_files.append(file)
                
                if not video_files:
                    raise Exception("Файл не найден после загрузки")
                
                video_path = os.path.join(temp_dir, video_files[0])
                file_size = os.path.getsize(video_path)
                
                # Проверяем размер
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
                
                if file_size < 1024:  # Меньше 1KB
                    raise Exception("Получен пустой файл")
                
                await progress_msg.edit_text(
                    f"📱 <b>{title}</b>\n📤 Отправляю вам...\n▓▓▓▓▓▓▓▓▓░ 90%",
                    parse_mode='HTML'
                )
                
                # Отправляем видео
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                
                with open(video_path, 'rb') as video_file:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=video_file,
                        caption=f"✅ <b>{title}</b>\n\n⚡ Загружено за {time.time() - start_time:.1f}с\n💫 @VideoDownload077_Bot",
                        parse_mode='HTML',
                        supports_streaming=True,
                        read_timeout=180,
                        write_timeout=180
                    )
                
                # Удаляем сообщение прогресса
                await progress_msg.delete()
                
                # Успех - показываем кнопки
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
            error_msg = str(e).lower()
            
            # Более точная диагностика ошибок
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

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    keyboard = [
        [InlineKeyboardButton("🚀 Как пользоваться", callback_data="how_to_use")],
        [InlineKeyboardButton("📱 Поддерживаемые сайты", callback_data="supported_sites")],
        [InlineKeyboardButton("👨‍💻 Связаться с админом", url="https://t.me/akhmadow_077")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = (
        " 🎬 <b>Video Downloader Bot 2.0</b>\n\n"
        " ⚡ <b>Быстро скачиваю видео с:</b>\n"
        " 📸 Instagram (Reels, Posts, Stories)\n"
        " 🎵 TikTok (любые видео)\n\n"
        " 🔥 <b>Просто отправьте ссылку!</b>\n\n"
        " 💎 Молниеносно  🆓 Бесплатно"
    )
    
    await update.message.reply_text(
        welcome_text,
        parse_mode='HTML',
        reply_markup=reply_markup
    )

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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сообщений с URL"""
    message_text = update.message.text
    bot = PowerfulVideoBot()
    
    # Поиск URL в сообщении
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
    
    # Обрабатываем первую найденную ссылку
    url = urls[0]
    is_supported, platform = bot.is_supported_url(url)
    
    if is_supported:
        # Быстрая реакция
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
            "• Все форматы и качества\n\n"
            "🔄 <b>Скоро добавлю:</b>\n"
            "• YouTube Shorts\n"
            "• Twitter/X видео"
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
        )
    }
    
    text = texts.get(query.data, "❓ Неизвестная команда")
    
    keyboard = [
        [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
        [InlineKeyboardButton("👨‍💻 Админ", url="https://t.me/akhmadow_077")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)

def main():
    """Запуск бота"""
    print("🚀 Запуск мощного Video Downloader Bot...")
    print("⚡ Версия 2.0 - Быстрая и стабильная")
    print("📱 Админ: @akhmadow_077")
    print("✅ Готов к работе!")
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Регистрируем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CallbackQueryHandler(button_callback))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("🔥 Бот запущен и готов качать видео!")
        application.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == '__main__':
    main()