# webhook.py
"""
Лёгкий HTTP-сервер на aiohttp, который слушает callback от Platega
о изменении статуса транзакции и продлевает подписку пользователю.

Работает в том же процессе и event loop, что и сам бот (polling),
просто на отдельном порту. nginx снаружи проксирует HTTPS на этот порт.
"""

import logging

from aiohttp import web

from config import PLATEGA_MERCHANT_ID, PLATEGA_API_KEY, PLATEGA_CALLBACK_PATH, WEBHOOK_PORT, REFERRAL_BONUS_DAYS
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
        tariff_callback = tx["tariff_callback"]
        await db.activate_subscription(user_id, days)
        logging.info(f"Подписка пользователя {user_id} продлена на {days} дней (tx={transaction_id})")

        try:
            await db.log_event(
                user_id, "payment_confirmed",
                tariff_callback=tariff_callback, transaction_id=transaction_id,
            )
        except Exception as e:
            logging.warning(f"Не удалось залогировать событие payment_confirmed для {user_id}: {e}")

        # Создаём или продлеваем реального VPN-клиента в 3x-ui
        try:
            email, sub_id = await db.get_xui_client(user_id)
            if email:
                await xui.update_client_expiry(email, days, extend=True)
            else:
                result = await xui.add_client(user_id=user_id, days=days)
                await db.save_xui_client(user_id, result["email"], result["sub_id"])
        except Exception as e:
            logging.error(f"Не удалось создать/продлить 3x-ui клиента для {user_id} (tx={transaction_id}): {e}")
            # Подписка в нашей БД уже продлена — пользователь не останется без доступа полностью,
            # но стоит проверить вручную через админку при возникновении такой ошибки в логах.

        bot = request.app["bot"]

        # --- Реферальный бонус: +REFERRAL_BONUS_DAYS рефереру за каждую оплату/продление
        # реферала, кроме 1-дневного тарифа (tariff_1d не считается). Защищено от
        # повторного начисления по transaction_id, на случай дублирующего callback.
        if tariff_callback != "tariff_1d":
            already_awarded = await db.has_referral_bonus_for_transaction(transaction_id)
            if not already_awarded:
                referrer_id = await db.get_referrer_id(user_id)
                if referrer_id:
                    await db.activate_subscription(referrer_id, REFERRAL_BONUS_DAYS)
                    await db.record_referral_bonus(transaction_id, referrer_id, user_id, REFERRAL_BONUS_DAYS)
                    logging.info(
                        f"Реферальный бонус: рефереру {referrer_id} начислено "
                        f"{REFERRAL_BONUS_DAYS} дн. за оплату реферала {user_id} (tx={transaction_id})"
                    )

                    try:
                        ref_email, ref_sub_id = await db.get_xui_client(referrer_id)
                        if ref_email:
                            await xui.update_client_expiry(ref_email, REFERRAL_BONUS_DAYS, extend=True)
                        else:
                            ref_result = await xui.add_client(user_id=referrer_id, days=REFERRAL_BONUS_DAYS)
                            await db.save_xui_client(referrer_id, ref_result["email"], ref_result["sub_id"])
                    except Exception as e:
                        logging.error(
                            f"Не удалось продлить 3x-ui клиента рефереру {referrer_id} "
                            f"за бонус (tx={transaction_id}): {e}"
                        )

                    try:
                        await bot.send_message(
                            chat_id=referrer_id,
                            text=(
                                "🎁 <b>Реферальный бонус!</b>\n\n"
                                f"Ваш друг оплатил подписку — вам начислено "
                                f"<b>+{REFERRAL_BONUS_DAYS} дней</b> VPN.\n"
                                "Спасибо, что приглашаете друзей! 🫂"
                            ),
                            parse_mode="HTML",
                        )
                    except Exception as e:
                        logging.error(f"Не удалось отправить уведомление о бонусе рефереру {referrer_id}: {e}")

        # Уведомляем покупателя в Telegram об успешной оплате
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
