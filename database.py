# database.py
"""
Слой доступа к данным бота — PostgreSQL через asyncpg с connection pool.
"""

import asyncpg
from datetime import datetime, timedelta

from config import DATABASE_URL, FREE_TRIAL_DAYS, TARIFFS as DEFAULT_TARIFFS


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_to_dict(row: asyncpg.Record | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for key in ("created_at", "subscription_ends_at", "updated_at"):
        if key in d:
            d[key] = _iso(d[key])
    return d


class Database:
    def __init__(self, dsn: str = DATABASE_URL):
        self.dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=10)
        return self._pool

    async def init(self):
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id              BIGINT PRIMARY KEY,
                    username             TEXT,
                    full_name            TEXT,
                    referrer_id          BIGINT,
                    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    trial_used           BOOLEAN DEFAULT FALSE,
                    is_trial             BOOLEAN DEFAULT FALSE,
                    menu_message_id      BIGINT,
                    subscription_ends_at TIMESTAMP,
                    xui_email            TEXT,
                    xui_sub_id           TEXT
                )
                """
            )

            await conn.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS expiry_notified_for TIMESTAMP"
            )
            await conn.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_partner BOOLEAN NOT NULL DEFAULT FALSE"
            )
            await conn.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS partner_id BIGINT"
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS partner_withdrawals (
                    id          SERIAL PRIMARY KEY,
                    partner_id  BIGINT NOT NULL,
                    amount      DOUBLE PRECISION NOT NULL,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    transaction_id  TEXT PRIMARY KEY,
                    user_id         BIGINT NOT NULL,
                    tariff_callback TEXT NOT NULL,
                    months          INTEGER NOT NULL,
                    days            INTEGER NOT NULL,
                    amount          DOUBLE PRECISION NOT NULL,
                    currency        TEXT NOT NULL DEFAULT 'RUB',
                    status          TEXT NOT NULL DEFAULT 'PENDING',
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS referral_bonuses (
                    id              SERIAL PRIMARY KEY,
                    transaction_id  TEXT NOT NULL,
                    referrer_id     BIGINT NOT NULL,
                    referral_id     BIGINT NOT NULL,
                    days_awarded    INTEGER NOT NULL,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_events (
                    id              SERIAL PRIMARY KEY,
                    user_id         BIGINT NOT NULL,
                    event_type      TEXT NOT NULL,
                    tariff_callback TEXT,
                    transaction_id  TEXT,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_deletions (
                    id          SERIAL PRIMARY KEY,
                    chat_id     BIGINT NOT NULL,
                    message_id  BIGINT NOT NULL,
                    delete_at   TIMESTAMP NOT NULL,
                    deleted     BOOLEAN NOT NULL DEFAULT FALSE
                )
                """
            )

            # Таблица тарифов — редактируется через админку
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tariffs (
                    id          SERIAL PRIMARY KEY,
                    callback    TEXT UNIQUE NOT NULL,
                    name        TEXT NOT NULL,
                    months      INTEGER NOT NULL,
                    days        INTEGER NOT NULL,
                    price       INTEGER NOT NULL,
                    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
                    sort_order  INTEGER NOT NULL DEFAULT 0
                )
                """
            )

            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_deletions_pending "
                "ON scheduled_deletions (delete_at) WHERE deleted = FALSE"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_events_user_id ON user_events (user_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_events_created_at ON user_events (created_at)"
            )

        # Заполняем тарифы дефолтными значениями если таблица пустая
        await self._init_default_tariffs()

    async def _init_default_tariffs(self):
        """Заполняет таблицу тарифов дефолтными значениями из config.py если она пустая."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM tariffs")
            if count == 0:
                for i, t in enumerate(DEFAULT_TARIFFS):
                    await conn.execute(
                        "INSERT INTO tariffs (callback, name, months, days, price, is_active, sort_order) "
                        "VALUES ($1, $2, $3, $4, $5, TRUE, $6) ON CONFLICT (callback) DO NOTHING",
                        t["callback"], t["name"], t["months"], t["days"], t["price"], i,
                    )

    # ==================== ТАРИФЫ ====================

    async def get_tariffs(self) -> list[dict]:
        """Все активные тарифы в порядке sort_order — для показа пользователям."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM tariffs WHERE is_active = TRUE ORDER BY sort_order, id"
            )
            return [dict(row) for row in rows]

    async def get_all_tariffs_admin(self) -> list[dict]:
        """Все тарифы включая неактивные — для админки."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM tariffs ORDER BY sort_order, id"
            )
            return [dict(row) for row in rows]

    async def get_tariff_by_callback(self, callback: str) -> dict | None:
        """Один тариф по callback."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tariffs WHERE callback = $1", callback
            )
            return dict(row) if row else None

    async def update_tariff_price(self, callback: str, price: int) -> None:
        """Обновить цену тарифа."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE tariffs SET price = $1 WHERE callback = $2",
                price, callback,
            )

    async def set_tariff_active(self, callback: str, is_active: bool) -> None:
        """Включить или выключить тариф."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE tariffs SET is_active = $1 WHERE callback = $2",
                is_active, callback,
            )

    # ==================== ПОЛЬЗОВАТЕЛИ ====================

    async def add_user(
        self,
        user_id: int,
        username: str,
        full_name: str,
        referrer_id: int | None = None,
        partner_id: int | None = None,
    ) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "INSERT INTO users (user_id, username, full_name, referrer_id, partner_id) "
                "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (user_id) DO NOTHING",
                user_id, username, full_name,
                referrer_id if (referrer_id and referrer_id != user_id) else None,
                partner_id if (partner_id and partner_id != user_id) else None,
            )
            return result.endswith(" 1")

    async def get_user(self, user_id: int) -> dict | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            return _row_to_dict(row)

    async def save_menu_message_id(self, user_id: int, message_id: int) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET menu_message_id = $1 WHERE user_id = $2",
                message_id, user_id,
            )

    async def get_menu_message_id(self, user_id: int) -> int | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT menu_message_id FROM users WHERE user_id = $1", user_id
            )

    async def save_xui_client(self, user_id: int, email: str, sub_id: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET xui_email = $1, xui_sub_id = $2 WHERE user_id = $3",
                email, sub_id, user_id,
            )

    async def update_xui_sub_id(self, user_id: int, sub_id: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET xui_sub_id = $1 WHERE user_id = $2",
                sub_id, user_id,
            )

    async def get_xui_client(self, user_id: int) -> tuple[str | None, str | None]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT xui_email, xui_sub_id FROM users WHERE user_id = $1", user_id
            )
            return (row["xui_email"], row["xui_sub_id"]) if row else (None, None)

    async def activate_trial(self, user_id: int) -> None:
        ends_at = datetime.now() + timedelta(days=FREE_TRIAL_DAYS)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET trial_used = TRUE, is_trial = TRUE, subscription_ends_at = $1 "
                "WHERE user_id = $2",
                ends_at, user_id,
            )

    async def activate_subscription(self, user_id: int, days: int) -> None:
        user = await self.get_user(user_id)
        if user and user.get("subscription_ends_at"):
            try:
                current_end = datetime.fromisoformat(user["subscription_ends_at"])
                base_date = max(current_end, datetime.now())
            except (ValueError, TypeError):
                base_date = datetime.now()
        else:
            base_date = datetime.now()

        ends_at = base_date + timedelta(days=days)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET subscription_ends_at = $1, is_trial = FALSE WHERE user_id = $2",
                ends_at, user_id,
            )

    async def end_subscription(self, user_id: int) -> None:
        past_date = datetime.now() - timedelta(days=1)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET subscription_ends_at = $1 WHERE user_id = $2",
                past_date, user_id,
            )

    async def get_users_expiring_soon(self) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id, full_name, subscription_ends_at
                FROM users
                WHERE subscription_ends_at IS NOT NULL
                  AND subscription_ends_at > CURRENT_TIMESTAMP + INTERVAL '23 hours'
                  AND subscription_ends_at <= CURRENT_TIMESTAMP + INTERVAL '25 hours'
                  AND (
                        expiry_notified_for IS NULL
                        OR expiry_notified_for != subscription_ends_at
                      )
                """
            )
            return [_row_to_dict(row) for row in rows]

    async def mark_expiry_notified(self, user_id: int, subscription_ends_at: datetime) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET expiry_notified_for = $1 WHERE user_id = $2",
                subscription_ends_at, user_id,
            )

    # ==================== ОТЛОЖЕННОЕ УДАЛЕНИЕ ====================

    async def schedule_message_deletion(self, chat_id: int, message_id: int, delay_seconds: int) -> None:
        delete_at = datetime.now() + timedelta(seconds=delay_seconds)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO scheduled_deletions (chat_id, message_id, delete_at) VALUES ($1, $2, $3)",
                chat_id, message_id, delete_at,
            )

    async def get_due_deletions(self) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, chat_id, message_id FROM scheduled_deletions "
                "WHERE deleted = FALSE AND delete_at <= CURRENT_TIMESTAMP"
            )
            return [dict(row) for row in rows]

    async def mark_deletion_done(self, deletion_id: int) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE scheduled_deletions SET deleted = TRUE WHERE id = $1", deletion_id
            )

    async def reset_user(self, user_id: int) -> tuple[bool, str | None]:
        email, _ = await self.get_xui_client(user_id)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute("DELETE FROM users WHERE user_id = $1", user_id)
            await conn.execute("DELETE FROM transactions WHERE user_id = $1", user_id)
            was_deleted = result.endswith(" 1")
            return was_deleted, email

    # ==================== РЕФЕРАЛЫ ====================

    async def get_referrer_id(self, user_id: int) -> int | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT referrer_id FROM users WHERE user_id = $1", user_id
            )

    async def get_referrals_count(self, user_id: int) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE referrer_id = $1", user_id
            ) or 0

    async def get_referrals(self, user_id: int) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id, username, full_name, created_at "
                "FROM users WHERE referrer_id = $1 ORDER BY created_at DESC",
                user_id,
            )
            return [_row_to_dict(row) for row in rows]

    # ==================== ТРАНЗАКЦИИ ====================

    async def create_transaction(
        self,
        transaction_id: str,
        user_id: int,
        tariff_callback: str,
        months: int,
        days: int,
        amount: float,
        currency: str = "RUB",
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO transactions "
                "(transaction_id, user_id, tariff_callback, months, days, amount, currency, status) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, 'PENDING')",
                transaction_id, user_id, tariff_callback, months, days, amount, currency,
            )

    async def get_transaction(self, transaction_id: str) -> dict | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM transactions WHERE transaction_id = $1", transaction_id
            )
            return _row_to_dict(row)

    async def update_transaction_status(self, transaction_id: str, status: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE transactions SET status = $1, updated_at = CURRENT_TIMESTAMP "
                "WHERE transaction_id = $2",
                status, transaction_id,
            )

    # ==================== РЕФЕРАЛЬНЫЕ БОНУСЫ ====================

    async def has_referral_bonus_for_transaction(self, transaction_id: str) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT 1 FROM referral_bonuses WHERE transaction_id = $1", transaction_id
            )
            return value is not None

    async def record_referral_bonus(
        self, transaction_id: str, referrer_id: int, referral_id: int, days_awarded: int
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO referral_bonuses "
                "(transaction_id, referrer_id, referral_id, days_awarded) VALUES ($1, $2, $3, $4)",
                transaction_id, referrer_id, referral_id, days_awarded,
            )

    async def get_referral_bonus_days_total(self, referrer_id: int) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COALESCE(SUM(days_awarded), 0) FROM referral_bonuses WHERE referrer_id = $1",
                referrer_id,
            ) or 0

    # ==================== ВОРОНКА ====================

    async def log_event(
        self,
        user_id: int,
        event_type: str,
        tariff_callback: str | None = None,
        transaction_id: str | None = None,
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO user_events (user_id, event_type, tariff_callback, transaction_id) "
                "VALUES ($1, $2, $3, $4)",
                user_id, event_type, tariff_callback, transaction_id,
            )

    # ==================== ПАРТНЁРСКАЯ ПРОГРАММА ====================

    async def set_partner_status(self, user_id: int, is_partner: bool) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET is_partner = $1 WHERE user_id = $2", is_partner, user_id
            )

    async def get_all_partners(self) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id, username, full_name, created_at FROM users "
                "WHERE is_partner = TRUE ORDER BY created_at DESC"
            )
            return [_row_to_dict(row) for row in rows]

    async def get_partner_referrals_count(self, partner_id: int) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE partner_id = $1", partner_id
            ) or 0

    async def get_partner_referrals_with_trial_count(self, partner_id: int) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE partner_id = $1 AND trial_used = TRUE",
                partner_id,
            ) or 0

    async def get_partner_referrals_with_paid_count(self, partner_id: int) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT COUNT(DISTINCT t.user_id)
                FROM transactions t
                JOIN users u ON u.user_id = t.user_id
                WHERE u.partner_id = $1 AND t.status = 'CONFIRMED'
                """,
                partner_id,
            ) or 0

    async def get_partner_referrals_total_paid_amount(self, partner_id: int) -> float:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return float(await conn.fetchval(
                """
                SELECT COALESCE(SUM(t.amount), 0)
                FROM transactions t
                JOIN users u ON u.user_id = t.user_id
                WHERE u.partner_id = $1 AND t.status = 'CONFIRMED'
                """,
                partner_id,
            ) or 0)

    async def add_partner_withdrawal(self, partner_id: int, amount: float) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO partner_withdrawals (partner_id, amount) VALUES ($1, $2)",
                partner_id, amount,
            )

    async def get_partner_withdrawn_amount(self, partner_id: int) -> float:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return float(await conn.fetchval(
                "SELECT COALESCE(SUM(amount), 0) FROM partner_withdrawals WHERE partner_id = $1",
                partner_id,
            ) or 0)

    # ==================== АДМИН ====================

    async def get_all_users(self) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id, username, full_name, created_at, trial_used, "
                "subscription_ends_at, referrer_id, is_trial FROM users ORDER BY created_at DESC"
            )
            return [_row_to_dict(row) for row in rows]

    async def get_users_count(self) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM users") or 0

    async def get_trial_users_count(self) -> int:
        """Сколько пользователей активировали триал."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM users WHERE trial_used = TRUE") or 0

    async def get_paid_users_count(self) -> int:
        """Сколько уникальных пользователей хотя бы раз оплатили."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(DISTINCT user_id) FROM transactions WHERE status = 'CONFIRMED'"
            ) or 0

    async def get_total_payments_amount(self) -> float:
        """Суммарная сумма всех подтверждённых оплат."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return float(await conn.fetchval(
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE status = 'CONFIRMED'"
            ) or 0)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


db = Database()
