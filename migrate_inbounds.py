#!/usr/bin/env python3
# migrate_inbounds.py
"""
Одноразовый скрипт миграции: добавляет всех существующих VPN-клиентов
в inbound'ы из XUI_INBOUND_IDS, в которых они ещё не состоят.

Запускать вручную на сервере при:
  - первом добавлении нового inbound'а (например, inbound 4),
  - любом следующем — достаточно добавить ID в XUI_INBOUND_IDS и запустить снова.

Бот при этом может работать — скрипт читает данные из той же БД и той же панели 3x-ui,
но ничего не удаляет и не изменяет существующие подписки.

Запуск:
    python3 migrate_inbounds.py

Переменные окружения должны быть те же, что и у бота (DATABASE_URL, XUI_*, и т.д.).
"""

import asyncio
import logging

import asyncpg

from config import DATABASE_URL, XUI_INBOUND_IDS
from xui_client import xui

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


async def get_all_xui_clients(conn: asyncpg.Connection) -> list[dict]:
    """Возвращает всех пользователей, у которых есть xui_email (т.е. уже созданы в панели)."""
    rows = await conn.fetch(
        "SELECT user_id, xui_email, xui_sub_id FROM users "
        "WHERE xui_email IS NOT NULL AND xui_email != '' "
        "ORDER BY user_id"
    )
    return [dict(r) for r in rows]


async def main():
    logging.info(f"Целевые inbound'ы из конфига: {XUI_INBOUND_IDS}")

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        clients = await get_all_xui_clients(conn)
    finally:
        await conn.close()

    total = len(clients)
    logging.info(f"Всего клиентов в БД: {total}")

    if not clients:
        logging.info("Клиентов нет — ничего делать не нужно.")
        return

    ok = 0
    already_synced = 0
    errors = 0

    for i, client in enumerate(clients, 1):
        email = client["xui_email"]
        user_id = client["user_id"]
        try:
            added_to = await xui.sync_client_inbounds(email)
            if added_to:
                logging.info(f"[{i}/{total}] user={user_id} email={email} → добавлен в {added_to}")
                ok += 1
            else:
                logging.debug(f"[{i}/{total}] user={user_id} email={email} → уже во всех inbound'ах")
                already_synced += 1
        except Exception as e:
            logging.error(f"[{i}/{total}] user={user_id} email={email} → ОШИБКА: {e}")
            errors += 1

        # Небольшая пауза, чтобы не перегружать панель
        await asyncio.sleep(0.1)

    logging.info(
        f"\n=== Готово ===\n"
        f"  Добавлены в новые inbound'ы: {ok}\n"
        f"  Уже были во всех inbound'ах:  {already_synced}\n"
        f"  Ошибки:                       {errors}\n"
        f"  Всего обработано:             {total}"
    )

    await xui.close()


if __name__ == "__main__":
    asyncio.run(main())
