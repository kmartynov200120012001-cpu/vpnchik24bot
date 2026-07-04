# support_bot.py
"""
Отдельный бот поддержки (@vpnchiksupportbot).

Как это работает:
- Пользователь пишет что угодно этому боту -> сообщение пересылается админу
  (ADMIN_ID) с подписью, кто автор (user_id, username, имя).
- Админ отвечает на пересланное сообщение (обычный Reply в Telegram) ->
  бот отправляет этот ответ обратно тому самому пользователю.

Запуск отдельно от основного бота: python3 support_bot.py
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message

from config import SUPPORT_BOT_TOKEN, ADMIN_ID

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

bot = Bot(token=SUPPORT_BOT_TOKEN)
dp = Dispatcher()
router = Router()

# user_id -> message_id пересланного админу сообщения (чтобы по reply понять, кому отвечать)
forwarded_map: dict[int, int] = {}


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Здравствуйте! Опишите свой вопрос одним сообщением — мы ответим здесь же, как только сможем."
    )


@router.message(F.chat.id == ADMIN_ID, F.reply_to_message)
async def on_admin_reply(message: Message):
    """Админ ответил (reply) на пересланное сообщение — отправляем ответ пользователю."""
    replied_id = message.reply_to_message.message_id
    user_id = next((uid for uid, mid in forwarded_map.items() if mid == replied_id), None)

    if user_id is None:
        await message.answer("⚠️ Не удалось понять, кому отвечать (сообщение слишком старое).")
        return

    try:
        await bot.copy_message(chat_id=user_id, from_chat_id=ADMIN_ID, message_id=message.message_id)
        await message.answer("✅ Отправлено пользователю.")
    except Exception as e:
        await message.answer(f"⚠️ Не удалось отправить ответ: {e}")


@router.message()
async def on_user_message(message: Message):
    """Любое сообщение от обычного пользователя — пересылаем админу."""
    if message.from_user.id == ADMIN_ID:
        return  # админ пишет боту напрямую (не через reply) — игнорируем

    user = message.from_user
    caption = (
        f"✉️ Сообщение от {user.full_name} "
        f"(@{user.username if user.username else '—'}, id: {user.id})"
    )
    await bot.send_message(chat_id=ADMIN_ID, text=caption)
    forwarded = await bot.forward_message(
        chat_id=ADMIN_ID, from_chat_id=message.chat.id, message_id=message.message_id
    )
    forwarded_map[user.id] = forwarded.message_id

    await message.answer("✅ Сообщение отправлено в поддержку, ожидайте ответа.")


async def main():
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Support bot stopped")
