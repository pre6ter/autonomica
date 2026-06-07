# Telegram Voice-to-Text Bot

Telegram бот для преобразования голосовых сообщений и аудиофайлов в текст с использованием OpenAI Whisper.

## Особенности

- Поддержка голосовых сообщений и аудиофайлов
- Использование модели Whisper large-v3 для наилучшего качества распознавания
- Поддержка файлов до 20MB
- Автоматическая конвертация аудио в нужный формат
- Обработка длинных текстов с разбивкой на части

## Требования

- Python 3.8+
- FFmpeg
- Telegram Bot Token
- Зависимости из requirements.txt

## Установка

1. Клонируйте репозиторий:
```bash
git clone https://github.com/yourusername/telegram-voice-to-text-bot.git
cd telegram-voice-to-text-bot
```

2. Создайте виртуальное окружение:
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# или
venv\Scripts\activate  # Windows
```

3. Установите зависимости:
```bash
pip install -r requirements.txt
```

4. Установите FFmpeg:
```bash
# Mac
brew install ffmpeg

# Linux
sudo apt-get install ffmpeg

# Windows
# Скачайте с https://ffmpeg.org/download.html
```

## Запуск

```bash
cd src
python bot.py
```

## Структура проекта

```
.
├── README.md
├── requirements.txt
├── config/
│   └── config.py
├── src/
│   └── bot.py
├── data/
│   └── temp/
├── logs/
└── tests/
```

## Использование

1. Найдите бота в Telegram: @your_bot_name
2. Отправьте команду `/start`
3. Отправьте голосовое сообщение или аудиофайл
4. Получите текстовую расшифровку

## Команды

- `/start` - начать работу с ботом
- `/stop` - остановить текущую обработку

## Лицензия

MIT # autonomica
