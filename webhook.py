# webhook.py
"""
Лёгкий HTTP-сервер на aiohttp, который слушает callback от Platega
о изменении статуса транзакции и продлевает подписку пользователю.

Работает в том же процессе и event loop, что и сам бот (polling),
просто на отдельном порту. nginx снаружи проксирует HTTPS на этот порт.
"""

import logging

from aiohttp import web

from config import PLATEGA_MERCHANT_ID, PLATEGA_API_KEY, PLATEGA_CALLBACK_PATH, WEBHOOK_PORT
from database import db
from xui_client import xui


async def handle_platega_callback(request: web.Request) -> web.Response:
    # --- Проверка подлинности запроса ---
    merchant_id = request.headers.get("X-MerchantId")
    secret = request.headers.get("X-Secret")

    if merchant_id != PLATEGA_MERCHANT_ID or secret != PLATEGA_API_KEY:
        logging.warning("Platega callback: неверные X-MerchantId/X-Secret")
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid json"}, status=400)

    transaction_id = data.get("id")
    status = data.get("status")

    if not transaction_id or not status:
        return web.json_response({"ok": False, "error": "missing fields"}, status=400)

    logging.info(f"Platega callback: transaction={transaction_id} status={status}")

    tx = await db.get_transaction(transaction_id)
    if not tx:
        logging.warning(f"Platega callback: транзакция {transaction_id} не найдена в БД")
        # Отвечаем 200, чтобы Platega не повторяла запрос бесконечно за неизвестную транзакцию
        return web.json_response({"ok": True})

    # Защита от повторной обработки (Platega может прислать callback несколько раз)
    if tx["status"] == "CONFIRMED":
        return web.json_response({"ok": True})

    await db.update_transaction_status(transaction_id, status)

    if status == "CONFIRMED":
        user_id = tx["user_id"]
        days = tx["days"]
        await db.activate_subscription(user_id, days)
        logging.info(f"Подписка пользователя {user_id} продлена на {days} дней (tx={transaction_id})")

        # Создаём или продлеваем реального VPN-клиента в 3x-ui
        try:
            client_uuid, sub_id = await db.get_xui_client(user_id)
            if client_uuid:
                await xui.update_client_expiry(client_uuid, days, extend=True)
            else:
                result = await xui.add_client(user_id=user_id, days=days)
                await db.save_xui_client(user_id, result["client_uuid"], result["sub_id"])
        except Exception as e:
            logging.error(f"Не удалось создать/продлить 3x-ui клиента для {user_id} (tx={transaction_id}): {e}")
            # Подписка в нашей БД уже продлена — пользователь не останется без доступа полностью,
            # но стоит проверить вручную через админку при возникновении такой ошибки в логах.

        # Уведомляем пользователя в Telegram об успешной оплате
        bot = request.app["bot"]
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "✅ <b>Оплата прошла успешно!</b>\n\n"
                    f"Ваша подписка продлена на {days} дн.\n"
                    "Зайдите в меню — ключ доступа уже обновлён."
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

    return web.json_response({"ok": True})


def create_webhook_app(bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_post(PLATEGA_CALLBACK_PATH, handle_platega_callback)
    return app


async def run_webhook_server(bot) -> web.AppRunner:
    """Запускает aiohttp-сервер на WEBHOOK_PORT. Вызывать из той же asyncio-программы, что и polling."""
    app = create_webhook_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=WEBHOOK_PORT)
    await site.start()
    logging.info(f"Webhook-сервер запущен на 127.0.0.1:{WEBHOOK_PORT}{PLATEGA_CALLBACK_PATH}")
    return runner
