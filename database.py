# database.py

import aiosqlite
from datetime import datetime, timedelta
from config import DB_PATH, FREE_TRIAL_DAYS


class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id              INTEGER PRIMARY KEY,
                    username             TEXT,
                    full_name            TEXT,
                    referrer_id          INTEGER,
                    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    trial_used           BOOLEAN DEFAULT 0,
                    is_trial             BOOLEAN DEFAULT 0,
                    menu_message_id      INTEGER,
                    subscription_ends_at TIMESTAMP
                )
                """
            )
            await db.commit()

            for column in [
                "menu_message_id INTEGER",
                "subscription_ends_at TIMESTAMP",
                "referrer_id INTEGER",
                "is_trial BOOLEAN DEFAULT 0",
                "xui_client_uuid TEXT",
                "xui_sub_id TEXT",
            ]:
                try:
                    await db.execute(f"ALTER TABLE users ADD COLUMN {column}")
                    await db.commit()
                except Exception:
                    pass

            # --- Таблица транзакций (платежи через Platega) ---
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    transaction_id  TEXT PRIMARY KEY,
                    user_id         INTEGER NOT NULL,
                    tariff_callback TEXT NOT NULL,
                    months          INTEGER NOT NULL,
                    days            INTEGER NOT NULL,
                    amount          REAL NOT NULL,
                    currency        TEXT NOT NULL DEFAULT 'RUB',
                    status          TEXT NOT NULL DEFAULT 'PENDING',
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.commit()

    async def add_user(
        self, user_id: int, username: str, full_name: str, referrer_id: int | None = None
    ) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            if referrer_id and referrer_id != user_id:
                cursor = await db.execute(
                    "INSERT OR IGNORE INTO users (user_id, username, full_name, referrer_id) "
                    "VALUES (?, ?, ?, ?)",
                    (user_id, username, full_name, referrer_id),
                )
            else:
                cursor = await db.execute(
                    "INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
                    (user_id, username, full_name),
                )
            await db.commit()
            return cursor.rowcount > 0

    async def get_user(self, user_id: int) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def save_menu_message_id(self, user_id: int, message_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET menu_message_id = ? WHERE user_id = ?",
                (message_id, user_id),
            )
            await db.commit()

    async def get_menu_message_id(self, user_id: int) -> int | None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT menu_message_id FROM users WHERE user_id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row and row[0] else None

    async def save_xui_client(self, user_id: int, client_uuid: str, sub_id: str) -> None:
        """Привязывает 3x-ui клиента (uuid + subId) к пользователю — один на все платформы."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET xui_client_uuid = ?, xui_sub_id = ? WHERE user_id = ?",
                (client_uuid, sub_id, user_id),
            )
            await db.commit()

    async def get_xui_client(self, user_id: int) -> tuple[str | None, str | None]:
        """Возвращает (client_uuid, sub_id) для пользователя, либо (None, None) если ещё не создан."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT xui_client_uuid, xui_sub_id FROM users WHERE user_id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            return (row[0], row[1]) if row else (None, None)

    async def activate_trial(self, user_id: int) -> None:
        """Активирует бесплатный период: trial_used=1, is_trial=1."""
        ends_at = datetime.now() + timedelta(days=FREE_TRIAL_DAYS)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET trial_used = 1, is_trial = 1, subscription_ends_at = ? "
                "WHERE user_id = ?",
                (ends_at, user_id),
            )
            await db.commit()

    async def activate_subscription(self, user_id: int, days: int) -> None:
        """Активирует платную подписку (is_trial=0)."""
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
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET subscription_ends_at = ?, is_trial = 0 WHERE user_id = ?",
                (ends_at, user_id),
            )
            await db.commit()

    async def end_subscription(self, user_id: int) -> None:
        past_date = datetime.now() - timedelta(days=1)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET subscription_ends_at = ? WHERE user_id = ?",
                (past_date, user_id),
            )
            await db.commit()

    async def reset_user(self, user_id: int) -> tuple[bool, str | None]:
        """
        Полностью удаляет пользователя и связанные с ним транзакции из БД,
        как если бы он никогда не запускал бота. Следующий /start создаст
        запись заново со значениями по умолчанию (trial_used=0, is_trial=0 и т.д.).
        Используется в админке для тестирования сценария "новый пользователь"
        без удаления всей базы bot.db.

        Возвращает (was_deleted, xui_client_uuid). xui_client_uuid отдаётся вызывающему
        коду, чтобы он мог дополнительно удалить клиента и в самой панели 3x-ui —
        здесь это не делается, чтобы database.py не зависел от xui_client.py.
        """
        client_uuid, _ = await self.get_xui_client(user_id)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM transactions WHERE user_id = ?", (user_id,))
            await db.commit()
            return cursor.rowcount > 0, client_uuid

    # ==================== РЕФЕРАЛЫ ====================

    async def get_referrals_count(self, user_id: int) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_referrals(self, user_id: int) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT user_id, username, full_name, created_at "
                "FROM users WHERE referrer_id = ? ORDER BY created_at DESC",
                (user_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

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
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO transactions "
                "(transaction_id, user_id, tariff_callback, months, days, amount, currency, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING')",
                (transaction_id, user_id, tariff_callback, months, days, amount, currency),
            )
            await db.commit()

    async def get_transaction(self, transaction_id: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update_transaction_status(self, transaction_id: str, status: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE transactions SET status = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE transaction_id = ?",
                (status, transaction_id),
            )
            await db.commit()

    # ==================== АДМИН-МЕТОДЫ ====================

    async def get_all_users(self) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT user_id, username, full_name, created_at, trial_used, "
                "subscription_ends_at, referrer_id, is_trial FROM users ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_users_count(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM users")
            row = await cursor.fetchone()
            return row[0] if row else 0


db = Database()
