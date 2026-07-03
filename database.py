# database.py
"""
Слой доступа к данным бота — PostgreSQL через asyncpg с connection pool.

Совместимость с прежним кодом (main.py, admin.py, webhook.py): все datetime-поля
(created_at, subscription_ends_at и т.п.) при чтении форматируются обратно в ISO-строки
("YYYY-MM-DDTHH:MM:SS.ffffff"), как раньше отдавал aiosqlite/SQLite — благодаря этому
остальной код, использующий datetime.fromisoformat(...), не пришлось переписывать.
"""

import asyncpg
from datetime import datetime, timedelta

from config import DATABASE_URL, FREE_TRIAL_DAYS


def _iso(value) -> str | None:
    """Приводит datetime к ISO-строке (как раньше отдавал SQLite); None остаётся None."""
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

    async def add_user(
        self, user_id: int, username: str, full_name: str, referrer_id: int | None = None
    ) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            if referrer_id and referrer_id != user_id:
                result = await conn.execute(
                    "INSERT INTO users (user_id, username, full_name, referrer_id) "
                    "VALUES ($1, $2, $3, $4) ON CONFLICT (user_id) DO NOTHING",
                    user_id, username, full_name, referrer_id,
                )
            else:
                result = await conn.execute(
                    "INSERT INTO users (user_id, username, full_name) VALUES ($1, $2, $3) "
                    "ON CONFLICT (user_id) DO NOTHING",
                    user_id, username, full_name,
                )
            # asyncpg возвращает строку вида "INSERT 0 1" (вставлена 1 строка) или "INSERT 0 0"
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
            value = await conn.fetchval(
                "SELECT menu_message_id FROM users WHERE user_id = $1", user_id
            )
            return value

    async def save_xui_client(self, user_id: int, email: str, sub_id: str) -> None:
        """Привязывает 3x-ui клиента (email + subId) к пользователю — один на все платформы."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET xui_email = $1, xui_sub_id = $2 WHERE user_id = $3",
                email, sub_id, user_id,
            )

    async def get_xui_client(self, user_id: int) -> tuple[str | None, str | None]:
        """Возвращает (email, sub_id) для пользователя, либо (None, None) если ещё не создан."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT xui_email, xui_sub_id FROM users WHERE user_id = $1", user_id
            )
            return (row["xui_email"], row["xui_sub_id"]) if row else (None, None)

    async def activate_trial(self, user_id: int) -> None:
        """Активирует бесплатный период: trial_used=TRUE, is_trial=TRUE."""
        ends_at = datetime.now() + timedelta(days=FREE_TRIAL_DAYS)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET trial_used = TRUE, is_trial = TRUE, subscription_ends_at = $1 "
                "WHERE user_id = $2",
                ends_at, user_id,
            )

    async def activate_subscription(self, user_id: int, days: int) -> None:
        """Активирует платную подписку (is_trial=FALSE), продлевая от большего из (сейчас, текущий срок)."""
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

    async def reset_user(self, user_id: int) -> tuple[bool, str | None]:
        """
        Полностью удаляет пользователя и связанные с ним транзакции из БД,
        как если бы он никогда не запускал бота. Следующий /start создаст
        запись заново со значениями по умолчанию.
        Используется в админке для тестирования сценария "новый пользователь".

        Возвращает (was_deleted, xui_email). xui_email отдаётся вызывающему
        коду, чтобы он мог дополнительно удалить клиента и в самой панели 3x-ui.
        """
        email, _ = await self.get_xui_client(user_id)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute("DELETE FROM users WHERE user_id = $1", user_id)
            await conn.execute("DELETE FROM transactions WHERE user_id = $1", user_id)
            was_deleted = result.endswith(" 1")
            return was_deleted, email

    # ==================== РЕФЕРАЛЫ ====================

    async def get_referrer_id(self, user_id: int) -> int | None:
        """Возвращает referrer_id пользователя (того, кто его пригласил), либо None."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT referrer_id FROM users WHERE user_id = $1", user_id
            )
            return value

    async def get_referrals_count(self, user_id: int) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE referrer_id = $1", user_id
            )
            return value or 0

    async def get_referrals(self, user_id: int) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id, username, full_name, created_at "
                "FROM users WHERE referrer_id = $1 ORDER BY created_at DESC",
                user_id,
            )
            return [_row_to_dict(row) for row in rows]

    # ==================== ТРАНЗАКЦИИ (ОПЛАТА) ====================

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
        """Сохраняет созданную, но ещё не оплаченную транзакцию."""
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
        """Защита от повторного начисления — Platega может присылать callback несколько раз."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT 1 FROM referral_bonuses WHERE transaction_id = $1", transaction_id
            )
            return value is not None

    async def record_referral_bonus(
        self, transaction_id: str, referrer_id: int, referral_id: int, days_awarded: int
    ) -> None:
        """Записывает факт начисления бонуса рефереру за оплату его реферала."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO referral_bonuses "
                "(transaction_id, referrer_id, referral_id, days_awarded) VALUES ($1, $2, $3, $4)",
                transaction_id, referrer_id, referral_id, days_awarded,
            )

    async def get_referral_bonus_days_total(self, referrer_id: int) -> int:
        """Суммарное количество бонусных дней, начисленных рефереру за всё время."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COALESCE(SUM(days_awarded), 0) FROM referral_bonuses WHERE referrer_id = $1",
                referrer_id,
            )
            return value or 0

    # ==================== АДМИН-МЕТОДЫ ====================

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
            value = await conn.fetchval("SELECT COUNT(*) FROM users")
            return value or 0

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


db = Database()
