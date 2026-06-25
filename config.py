# config.py

import os

BOT_TOKEN = os.environ.get("BOT_TOKEN")

# --- АДМИН ---
ADMIN_ID = 907393161  # ← ваш Telegram ID

# --- ПРОКСИ ---
# На сервере в Европе прокси обычно не нужен — Telegram API доступен напрямую.
# Если всё же нужен, задайте его через переменную окружения PROXY_URL на сервере,
# а не прямо в коде (там был ваш логин/пароль в открытом виде).
PROXY_URL = os.environ.get("PROXY_URL")  # None, если не задано — бот работает без прокси

# База данных (SQLite, асинхронная)
DB_PATH = "bot.db"

# Настройки VPN
FREE_TRIAL_DAYS = 3

# --- Тарифы ---
# months = 0 означает, что "₽/мес" в скобках показывать не нужно (например, для 1 дня)
# Для months >= 3 стоимость за месяц считается автоматически как round(price / months)
TARIFFS = [
    {"name": "1 месяц",   "months": 1,  "days": 30,  "price": 199,  "callback": "tariff_1m"},
    {"name": "3 месяца",  "months": 3,  "days": 90,  "price": 449,  "callback": "tariff_3m"},
    {"name": "6 месяцев", "months": 6,  "days": 180, "price": 849,  "callback": "tariff_6m"},
    {"name": "12 месяцев","months": 12, "days": 365, "price": 1549, "callback": "tariff_12m"},
    {"name": "1 день",    "months": 0,  "days": 1,   "price": 11,   "callback": "tariff_1d"},
]

# --- PLATEGA (платёжная система) ---
# Все значения берутся из переменных окружения на сервере — никогда не храните их в коде.
PLATEGA_BASE_URL = "https://app.platega.io"
PLATEGA_MERCHANT_ID = os.environ.get("PLATEGA_MERCHANT_ID")
PLATEGA_API_KEY = os.environ.get("PLATEGA_API_KEY")

# Публичный HTTPS-адрес вашего бота (домен), куда Platega шлёт callback об оплате.
# Например: "https://vpnchik24.ru"
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL")

# Локальный порт, на котором aiohttp-сервер бота слушает входящие callback'и.
# nginx проксирует внешние HTTPS-запросы на этот порт.
WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "8080"))

# Путь callback-эндпоинта (должен совпадать с тем, что указан в ЛК Platega
# и с PUBLIC_BASE_URL + этот путь).
PLATEGA_CALLBACK_PATH = "/platega/callback"
