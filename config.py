# config.py

import os

BOT_TOKEN = os.environ.get("BOT_TOKEN")

# --- АДМИН ---
ADMIN_ID = 907393161  # ← ваш Telegram ID

# --- ПРОКСИ ---
PROXY_URL = os.environ.get("PROXY_URL")  # None, если не задано — бот работает без прокси

# База данных (PostgreSQL)
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://vpnchik_bot_user:CHANGE_ME@127.0.0.1:5432/vpnchik_bot"
)

# Настройки VPN
FREE_TRIAL_DAYS = 3

# Лимиты трафика
TRIAL_TRAFFIC_GB = 30       # триал: 30 ГБ
PAID_TRAFFIC_GB = 999       # платная подписка (кроме тарифа 1 день): 999 ГБ, трафик не сбрасывается
DAY_TRAFFIC_GB = 10         # тариф 1 день: 10 ГБ, трафик сбрасывается при каждой покупке

# Сколько дней начисляется рефереру за каждую оплату/продление подписки его рефералом
# (кроме 1-дневного тарифа — он бонус не даёт)
REFERRAL_BONUS_DAYS = 10
PARTNER_COMMISSION_PERCENT = 30

# --- Тарифы ---
TARIFFS = [
    {"name": "1 месяц",   "months": 1,  "days": 30,  "price": 199,  "callback": "tariff_1m"},
    {"name": "3 месяца",  "months": 3,  "days": 90,  "price": 499,  "callback": "tariff_3m"},
    {"name": "6 месяцев", "months": 6,  "days": 180, "price": 899,  "callback": "tariff_6m"},
    {"name": "12 месяцев","months": 12, "days": 365, "price": 1599, "callback": "tariff_12m"},
    {"name": "1 день",    "months": 0,  "days": 1,   "price": 11,   "callback": "tariff_1d"},
]

# --- 3X-UI (панель управления VPN) ---
XUI_BASE_URL = os.environ.get("XUI_BASE_URL", "http://127.0.0.1:1221")
XUI_WEB_BASE_PATH = os.environ.get("XUI_WEB_BASE_PATH", "")
XUI_USERNAME = os.environ.get("XUI_USERNAME")
XUI_PASSWORD = os.environ.get("XUI_PASSWORD")
XUI_INBOUND_IDS: list[int] = [
    int(x.strip())
    for x in os.environ.get("XUI_INBOUND_IDS", "2,4").split(",")
    if x.strip().isdigit()
]
XUI_INBOUND_ID = XUI_INBOUND_IDS[0] if XUI_INBOUND_IDS else 2

XUI_PUBLIC_HOST = os.environ.get("XUI_PUBLIC_HOST", "virtualpullnightchik24.ru")
XUI_PUBLIC_PORT = int(os.environ.get("XUI_PUBLIC_PORT", "8443"))

# --- PLATEGA (платёжная система) ---
PLATEGA_BASE_URL = "https://app.platega.io"
PLATEGA_MERCHANT_ID = os.environ.get("PLATEGA_MERCHANT_ID")
PLATEGA_API_KEY = os.environ.get("PLATEGA_API_KEY")

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL")
WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "8080"))

# --- SUPPORT BOT ---
SUPPORT_BOT_TOKEN = os.environ.get("SUPPORT_BOT_TOKEN")

PLATEGA_CALLBACK_PATH = "/platega/callback"
