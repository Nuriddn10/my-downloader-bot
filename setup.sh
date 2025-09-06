#!/bin/bash

echo "🚀 Быстрый запуск Telegram Video Downloader Bot"
echo "================================================"

# Проверка Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 не найден. Установите Python3"
    exit 1
fi

echo "✅ Python3 найден"

# Установка зависимостей
echo "📦 Устанавливаем зависимости..."
pip3 install python-telegram-bot==20.7 yt-dlp==2023.12.30 aiohttp==3.9.1

# Проверка успешной установки
if [ $? -eq 0 ]; then
    echo "✅ Зависимости установлены успешно"
else
    echo "❌ Ошибка установки зависимостей"
    exit 1
fi

# Установка ffmpeg (опционально для лучшей обработки видео)
echo "🎬 Проверяем наличие ffmpeg..."
if ! command -v ffmpeg &> /dev/null; then
    echo "⚠️  ffmpeg не найден. Устанавливаем..."
    
    # Определяем ОС
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        sudo apt update && sudo apt install -y ffmpeg
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        brew install ffmpeg
    else
        echo "⚠️  Установите ffmpeg вручную для лучшей работы бота"
    fi
else
    echo "✅ ffmpeg уже установлен"
fi

echo ""
echo "🎉 Все готово для запуска бота!"
echo ""
echo "📝 Для запуска выполните:"
echo "   python3 bot.py"
echo ""
echo "🔗 Токен бота уже настроен: 7984182309:AAFrKUlavoB9UA06JT8VSTfaf0XKXaP2T7k"
echo "👨‍💻 Админ бота: @akhmadow_077"
echo ""
echo "💡 Полезные команды:"
echo "   - Остановка бота: Ctrl+C"
echo "   - Просмотр логов: tail -f bot.log (если настроено логирование в файл)"
echo ""