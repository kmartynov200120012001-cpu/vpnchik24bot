# config.py

import os
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# --- АДМИН ---
ADMIN_ID = 907393161  # ← ваш Telegram ID

# --- ПРОКСИ (удали эту строку, когда перенесёшь бота на сервер) ---
PROXY_URL = "http://VH1md9:N5AywE@152.232.74.4:9435"

# База данных (SQLite, асинхронная)
DB_PATH = "bot.db"

# Настройки VPN
FREE_TRIAL_DAYS = 3

# --- Тарифы ---
# months = 0 означает, что "₽/мес" в скобках показывать не нужно (например, для 1 дня)
# Для months >= 3 стоимость за месяц считается автоматически как round(price / months)
TARIFFS = [
    {"name": "1 месяц",   "months": 1,  "price": 199,  "callback": "tariff_1m"},
    {"name": "3 месяца",  "months": 3,  "price": 449,  "callback": "tariff_3m"},
    {"name": "6 месяцев", "months": 6,  "price": 849,  "callback": "tariff_6m"},
    {"name": "12 месяцев","months": 12, "price": 1549, "callback": "tariff_12m"},
    {"name": "1 день",    "months": 0,  "price": 11,   "callback": "tariff_1d"},
]
