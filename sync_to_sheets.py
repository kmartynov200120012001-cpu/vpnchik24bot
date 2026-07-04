#!/usr/bin/env python3
# sync_to_sheets.py
"""
Синхронизирует данные из PostgreSQL бота в Google Sheets.
Запускается независимо от бота (например, через cron каждые 15 минут) —
не влияет на скорость работы самого бота.

Настройка:
1. Создайте Service Account в Google Cloud Console, включите Google Sheets API
   и Google Drive API, скачайте JSON-ключ.
2. Положите ключ на сервер, например /opt/vpnchik24bot/google_credentials.json
3. Создайте таблицу в Google Sheets, расшарьте её на email из ключа
   (поле "client_email" в JSON) с правами редактора.
4. Задайте переменные окружения (или впишите напрямую ниже):
   - DATABASE_URL — та же строка, что использует бот
   - GOOGLE_CREDENTIALS_PATH — путь к JSON-ключу
   - GOOGLE_SHEET_ID — ID таблицы (из её URL: .../d/ЭТОТ_ID/edit)

Запуск вручную: python3 sync_to_sheets.py
Через cron (каждые 15 минут):
    */15 * * * * cd /opt/vpnchik24bot && /opt/vpnchik24bot/venv/bin/python3 sync_to_sheets.py >> /var/log/vpnchik-sync.log 2>&1
"""

import asyncio
import os
from datetime import datetime

import asyncpg
import gspread
from google.oauth2.service_account import Credentials

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://vpnchik_bot_user:CHANGE_ME@127.0.0.1:5432/vpnchik_bot"
)
GOOGLE_CREDENTIALS_PATH = os.environ.get(
    "GOOGLE_CREDENTIALS_PATH", "/opt/vpnchik24bot/google_credentials.json"
)
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "PASTE_YOUR_SHEET_ID_HERE")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_gspread_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_PATH, scopes=SCOPES)
    return gspread.authorize(creds)


async def fetch_users(conn: asyncpg.Connection) -> list[list]:
    """Лист 1: Пользователи — снапшот на текущий момент."""
    rows = await conn.fetch(
        """
        SELECT
            u.user_id,
            u.username,
            u.full_name,
            u.created_at,
            u.referrer_id,
            u.trial_used,
            u.is_trial,
            u.subscription_ends_at,
            u.xui_email,
            (SELECT COUNT(*) FROM users r WHERE r.referrer_id = u.user_id) AS total_referrals,
            (SELECT COALESCE(SUM(amount), 0) FROM transactions t
                WHERE t.user_id = u.user_id AND t.status = 'CONFIRMED') AS total_paid_amount
        FROM users u
        ORDER BY u.created_at DESC
        """
    )

    header = [
        "user_id", "username", "full_name", "registered_at", "referrer_id",
        "trial_used", "is_trial_now", "subscription_ends_at", "status",
        "days_since_registration", "xui_email", "total_referrals", "total_paid_amount",
    ]
    result = [header]

    now = datetime.now()
    for r in rows:
        ends_at = r["subscription_ends_at"]
        if ends_at and ends_at > now:
            status = "trial" if r["is_trial"] else "active"
        elif r["trial_used"] or ends_at:
            status = "expired"
        else:
            status = "new"

        days_since_reg = (now - r["created_at"]).days if r["created_at"] else 0

        result.append([
            r["user_id"],
            r["username"] or "",
            r["full_name"] or "",
            r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else "",
            r["referrer_id"] or "",
            "TRUE" if r["trial_used"] else "FALSE",
            "TRUE" if r["is_trial"] else "FALSE",
            ends_at.strftime("%Y-%m-%d %H:%M:%S") if ends_at else "",
            status,
            days_since_reg,
            r["xui_email"] or "",
            r["total_referrals"],
            float(r["total_paid_amount"]),
        ])
    return result


async def fetch_transactions(conn: asyncpg.Connection) -> list[list]:
    """Лист 2: Транзакции — полная история платежей."""
    rows = await conn.fetch(
        """
        SELECT
            t.transaction_id, t.user_id, u.username, t.tariff_callback,
            t.months, t.days, t.amount, t.currency, t.status,
            t.created_at, t.updated_at
        FROM transactions t
        LEFT JOIN users u ON u.user_id = t.user_id
        ORDER BY t.created_at DESC
        """
    )

    header = [
        "transaction_id", "user_id", "username", "tariff", "months", "days",
        "amount", "currency", "status", "created_at", "updated_at",
    ]
    result = [header]
    for r in rows:
        result.append([
            r["transaction_id"],
            r["user_id"],
            r["username"] or "",
            r["tariff_callback"],
            r["months"],
            r["days"],
            float(r["amount"]),
            r["currency"],
            r["status"],
            r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else "",
            r["updated_at"].strftime("%Y-%m-%d %H:%M:%S") if r["updated_at"] else "",
        ])
    return result


async def fetch_referrals(conn: asyncpg.Connection) -> list[list]:
    """Лист 3: Рефералы — кто кого привёл и какие бонусы начислены."""
    rows = await conn.fetch(
        """
        SELECT
            rb.referrer_id, ur.username AS referrer_username,
            rb.referral_id, uf.username AS referral_username,
            rb.transaction_id, rb.days_awarded, rb.created_at
        FROM referral_bonuses rb
        LEFT JOIN users ur ON ur.user_id = rb.referrer_id
        LEFT JOIN users uf ON uf.user_id = rb.referral_id
        ORDER BY rb.created_at DESC
        """
    )

    header = [
        "referrer_id", "referrer_username", "referral_id", "referral_username",
        "transaction_id", "days_awarded", "awarded_at",
    ]
    result = [header]
    for r in rows:
        result.append([
            r["referrer_id"],
            r["referrer_username"] or "",
            r["referral_id"],
            r["referral_username"] or "",
            r["transaction_id"],
            r["days_awarded"],
            r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else "",
        ])
    return result


async def fetch_subscriptions_history(conn: asyncpg.Connection) -> list[list]:
    """
    Лист 5: Подписки — вся история активаций (и триал, и платные).
    UNION между confirmed-транзакциями и разовым событием активации триала.
    """
    rows = await conn.fetch(
        """
        SELECT
            t.user_id, u.username, 'paid' AS subscription_type,
            t.tariff_callback AS tariff, t.created_at AS activated_at,
            t.days AS duration_days, t.amount AS amount_paid, t.transaction_id
        FROM transactions t
        LEFT JOIN users u ON u.user_id = t.user_id
        WHERE t.status = 'CONFIRMED'

        UNION ALL

        SELECT
            u.user_id, u.username, 'trial' AS subscription_type,
            NULL AS tariff, u.created_at AS activated_at,
            NULL AS duration_days, 0 AS amount_paid, NULL AS transaction_id
        FROM users u
        WHERE u.trial_used = TRUE

        ORDER BY activated_at DESC
        """
    )

    header = [
        "user_id", "username", "subscription_type", "tariff",
        "activated_at", "duration_days", "amount_paid", "transaction_id",
    ]
    result = [header]
    for r in rows:
        result.append([
            r["user_id"],
            r["username"] or "",
            r["subscription_type"],
            r["tariff"] or "",
            r["activated_at"].strftime("%Y-%m-%d %H:%M:%S") if r["activated_at"] else "",
            r["duration_days"] or "",
            float(r["amount_paid"]) if r["amount_paid"] is not None else 0,
            r["transaction_id"] or "",
        ])
    return result


async def fetch_funnel_events(conn: asyncpg.Connection) -> list[list]:
    """Лист 6: Воронка событий — построчная выгрузка user_events."""
    rows = await conn.fetch(
        """
        SELECT
            e.user_id, u.username, e.event_type, e.tariff_callback,
            e.transaction_id, e.created_at
        FROM user_events e
        LEFT JOIN users u ON u.user_id = e.user_id
        ORDER BY e.created_at DESC
        LIMIT 5000
        """
    )

    header = ["user_id", "username", "event_type", "tariff", "transaction_id", "created_at"]
    result = [header]
    for r in rows:
        result.append([
            r["user_id"],
            r["username"] or "",
            r["event_type"],
            r["tariff_callback"] or "",
            r["transaction_id"] or "",
            r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else "",
        ])
    return result


def _write_sheet_sync(gc: gspread.Client, sheet_name: str, data: list[list]):
    """Синхронная часть записи в Google Sheets — выполняется в отдельном потоке."""
    spreadsheet = gc.open_by_key(GOOGLE_SHEET_ID)

    try:
        worksheet = spreadsheet.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=100, cols=20)

    worksheet.clear()
    if data:
        rows_needed = len(data)
        cols_needed = max(len(row) for row in data)
        worksheet.resize(rows=max(rows_needed, 1), cols=max(cols_needed, 1))
        worksheet.update(data, "A1")

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Лист '{sheet_name}' обновлён: {len(data) - 1} строк данных")


async def write_sheet(gc: gspread.Client, sheet_name: str, data: list[list]):
    await asyncio.to_thread(_write_sheet_sync, gc, sheet_name, data)


async def main():
    conn = await asyncpg.connect(DATABASE_URL)
    gc = await asyncio.to_thread(get_gspread_client)

    try:
        users_data = await fetch_users(conn)
        await write_sheet(gc, "Пользователи", users_data)

        transactions_data = await fetch_transactions(conn)
        await write_sheet(gc, "Транзакции", transactions_data)

        referrals_data = await fetch_referrals(conn)
        await write_sheet(gc, "Рефералы", referrals_data)

        subscriptions_data = await fetch_subscriptions_history(conn)
        await write_sheet(gc, "Подписки", subscriptions_data)

        funnel_data = await fetch_funnel_events(conn)
        await write_sheet(gc, "Воронка событий", funnel_data)

        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Синхронизация завершена успешно")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
