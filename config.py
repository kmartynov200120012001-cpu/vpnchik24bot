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
# PostgreSQL: строка подключения задаётся через переменную окружения на сервере.
# Формат: postgresql://user:password@host:port/database
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://vpnchik_bot_user:CHANGE_ME@127.0.0.1:5432/vpnchik_bot"
)

# Настройки VPN
FREE_TRIAL_DAYS = 3

# Сколько дней начисляется рефереру за каждую оплату/продление подписки его рефералом
# (кроме 1-дневного тарифа — он бонус не даёт)
REFERRAL_BONUS_DAYS = 10

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

# --- 3X-UI (панель управления VPN) ---
# Все значения берутся из переменных окружения на сервере — никогда не храните их в коде.
# XUI_BASE_URL — корневой адрес панели БЕЗ webBasePath, например "http://127.0.0.1:1221"
# (так как бот работает на том же сервере, обращаемся через localhost, не через внешний IP)
XUI_BASE_URL = os.environ.get("XUI_BASE_URL", "http://127.0.0.1:1221")
XUI_WEB_BASE_PATH = os.environ.get("XUI_WEB_BASE_PATH", "")  # например "/cLKcauTRI6nq259pPt"
XUI_USERNAME = os.environ.get("XUI_USERNAME")
XUI_PASSWORD = os.environ.get("XUI_PASSWORD")
XUI_INBOUND_ID = int(os.environ.get("XUI_INBOUND_ID", "2"))

# Публичный домен/IP и порт, на котором реально слушает Xray (inbound) —
# это то, что попадёт в ссылку-конфиг для клиента. Не путать с XUI_BASE_URL (это для админки).
XUI_PUBLIC_HOST = os.environ.get("XUI_PUBLIC_HOST", "virtualpullnightchik24.ru")
XUI_PUBLIC_PORT = int(os.environ.get("XUI_PUBLIC_PORT", "8443"))
# --- PLATEGA (платёжная система) ---
PLATEGA_BASE_URL = "https://app.platega.io"
PLATEGA_MERCHANT_ID = os.environ.get("PLATEGA_MERCHANT_ID")
PLATEGA_API_KEY = os.environ.get("PLATEGA_API_KEY")

# Публичный HTTPS-адрес вашего бота (домен), куда Platega шлёт callback об оплате.
# Например: "https://vpnchik24.ru"
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL")

# Локальный порт, на котором aiohttp-сервер бота слушает входящие callback'и.
# nginx проксирует внешние HTTPS-запросы на этот порт.
WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "8080"))

# --- SUPPORT BOT (отдельный бот для поддержки, @vpnchiksupportbot) ---
SUPPORT_BOT_TOKEN = os.environ.get("SUPPORT_BOT_TOKEN")

# Путь callback-эндпоинта (должен совпадать с тем, что указан в ЛК Platega
# и с PUBLIC_BASE_URL + этот путь).
PLATEGA_CALLBACK_PATH = "/platega/callback"
