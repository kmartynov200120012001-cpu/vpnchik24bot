# admin.py

import logging
from datetime import datetime

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramForbiddenError

from config import ADMIN_ID
from database import db

logger = logging.getLogger(__name__)

admin_router = Router()


# ==================== FSM ====================

class BroadcastFSM(StatesGroup):
    waiting_for_user_ids = State()
    waiting_for_message = State()


class SubscriptionFSM(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_days = State()


# ==================== ФИЛЬТР НА АДМИНА ====================

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


# ==================== УВЕДОМЛЕНИЯ ПОЛЬЗОВАТЕЛЯМ ====================

async def notify_subscription_extended(bot, user_id: int, days: int, ends_at_str: str):
    """Отправляет пользователю уведомление о продлении подписки."""
    try:
        ends_formatted = ""
        if ends_at_str:
            try:
                ends_at = datetime.fromisoformat(ends_at_str)
                ends_formatted = ends_at.strftime("%d.%m.%Y %H:%M")
            except (ValueError, TypeError):
                pass

        await bot.send_message(
            chat_id=user_id,
            text=(
                "✅ <b>Ваша подписка продлена!</b>\n\n"
                f"➕ Добавлено: <b>{days} дн.</b>\n"
                f"📅 Действует до: <b>{ends_formatted}</b>\n\n"
                "Спасибо, что остаётесь с нами! 💎"
            ),
            parse_mode="HTML",
        )
    except TelegramForbiddenError:
        logger.warning(f"Не удалось отправить уведомление пользователю {user_id}: бот заблокирован")
    except Exception as e:
        logger.warning(f"Ошибка отправки уведомления пользователю {user_id}: {e}")


async def notify_subscription_ended(bot, user_id: int):
    """Отправляет пользователю уведомление о завершении подписки."""
    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                "❌ <b>Ваша подписка завершена</b>\n\n"
                "Доступ к VPN приостановлен.\n"
                "Продлите подписку, чтобы продолжить пользоваться сервисом."
            ),
            parse_mode="HTML",
        )
    except TelegramForbiddenError:
        logger.warning(f"Не удалось отправить уведомление пользователю {user_id}: бот заблокирован")
    except Exception as e:
        logger.warning(f"Ошибка отправки уведомления пользователю {user_id}: {e}")


# ==================== КЛАВИАТУРЫ ====================

def get_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users")],
            [InlineKeyboardButton(text="📨 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="⏰ Управление подпиской", callback_data="admin_subscription")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        ]
    )


def get_admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад в админку", callback_data="admin_panel")],
        ]
    )


def get_subscription_actions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура действий с подпиской конкретного пользователя."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Продлить на 7 дней", callback_data=f"sub_extend_{user_id}_7")],
            [InlineKeyboardButton(text="➕ Продлить на 30 дней", callback_data=f"sub_extend_{user_id}_30")],
            [InlineKeyboardButton(text="➕ Продлить на 90 дней", callback_data=f"sub_extend_{user_id}_90")],
            [InlineKeyboardButton(text="➕ Продлить на 365 дней", callback_data=f"sub_extend_{user_id}_365")],
            [InlineKeyboardButton(text="⏳ Продлить на N дней", callback_data=f"sub_custom_{user_id}")],
            [InlineKeyboardButton(text="❌ Завершить подписку", callback_data=f"sub_end_{user_id}")],
            [InlineKeyboardButton(text="↩️ Назад к списку", callback_data="admin_users")],
        ]
    )


# ==================== КОМАНДА /admin ====================

@admin_router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return

    await message.answer(
        "🔧 <b>Админ-панель</b>\n\nВыберите действие:",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML",
    )


# ==================== ОБРАБОТЧИКИ АДМИНКИ ====================

@admin_router.callback_query(F.data == "admin_panel")
async def on_admin_panel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    await state.clear()
    await callback.message.edit_text(
        "🔧 <b>Админ-панель</b>\n\nВыберите действие:",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin_stats")
async def on_admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    count = await db.get_users_count()
    await callback.message.edit_text(
        f"📊 <b>Статистика</b>\n\n"
        f"Всего пользователей: <b>{count}</b>",
        reply_markup=get_admin_back_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin_users")
async def on_admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    users = await db.get_all_users()

    if not users:
        await callback.message.edit_text(
            "👥 Пользователей пока нет.",
            reply_markup=get_admin_back_keyboard(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    lines = ["👥 <b>Список пользователей:</b>\n"]
    buttons = []

    for i, u in enumerate(users, 1):
        username = f"@{u['username']}" if u.get("username") else "—"
        name = u.get("full_name") or "—"
        user_id = u["user_id"]

        ends_at_str = u.get("subscription_ends_at")
        if ends_at_str:
            try:
                ends_at = datetime.fromisoformat(ends_at_str)
                if ends_at > datetime.now():
                    status = f"✅ до {ends_at.strftime('%d.%m.%Y')}"
                else:
                    status = "❌ истекла"
            except (ValueError, TypeError):
                status = "❌ истекла"
        else:
            status = "⚪ нет"

        lines.append(
            f"<b>{i}.</b> <code>{user_id}</code>\n"
            f"   {name} ({username})\n"
            f"   Подписка: {status}\n"
        )

        buttons.append([InlineKeyboardButton(
            text=f"⚙️ {name[:20]} ({user_id})",
            callback_data=f"admin_sub_manage_{user_id}",
        )])

    text = "\n".join(lines)

    if len(text) > 3500:
        text = text[:3500] + "\n\n<i>... список сокращён</i>"

    buttons.append([InlineKeyboardButton(text="↩️ Назад в админку", callback_data="admin_panel")])

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )
    await callback.answer()


# ==================== УПРАВЛЕНИЕ ПОДПИСКОЙ ====================

@admin_router.callback_query(F.data == "admin_subscription")
async def on_admin_subscription_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    await state.set_state(SubscriptionFSM.waiting_for_user_id)
    await callback.message.edit_text(
        "⏰ <b>Управление подпиской</b>\n\n"
        "Введите ID пользователя, которому хотите изменить подписку:\n\n"
        "<i>Или выберите пользователя из списка через кнопку «👥 Список пользователей»</i>",
        reply_markup=get_admin_back_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.message(SubscriptionFSM.waiting_for_user_id)
async def on_subscription_user_id(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите корректный числовой ID.")
        return

    user = await db.get_user(user_id)
    if not user:
        await message.answer(
            f"❌ Пользователь с ID <code>{user_id}</code> не найден.",
            parse_mode="HTML",
        )
        return

    ends_at_str = user.get("subscription_ends_at")
    if ends_at_str:
        try:
            ends_at = datetime.fromisoformat(ends_at_str)
            if ends_at > datetime.now():
                status = f"✅ активна до {ends_at.strftime('%d.%m.%Y %H:%M')}"
            else:
                status = "❌ истекла"
        except (ValueError, TypeError):
            status = "❌ истекла"
    else:
        status = "⚪ отсутствует"

    name = user.get("full_name") or "—"
    username = f"@{user['username']}" if user.get("username") else "—"

    await state.clear()
    await message.answer(
        f"👤 <b>Пользователь:</b> {name} ({username})\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
        f"📅 <b>Подписка:</b> {status}\n\n"
        "Выберите действие:",
        reply_markup=get_subscription_actions_keyboard(user_id),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data.startswith("admin_sub_manage_"))
async def on_admin_sub_manage(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    user_id = int(callback.data.split("_")[-1])
    user = await db.get_user(user_id)

    if not user:
        await callback.answer("❌ Пользователь не найден.", show_alert=True)
        return

    ends_at_str = user.get("subscription_ends_at")
    if ends_at_str:
        try:
            ends_at = datetime.fromisoformat(ends_at_str)
            if ends_at > datetime.now():
                status = f"✅ активна до {ends_at.strftime('%d.%m.%Y %H:%M')}"
            else:
                status = "❌ истекла"
        except (ValueError, TypeError):
            status = "❌ истекла"
    else:
        status = "⚪ отсутствует"

    name = user.get("full_name") or "—"
    username = f"@{user['username']}" if user.get("username") else "—"

    await callback.message.edit_text(
        f"👤 <b>Пользователь:</b> {name} ({username})\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
        f"📅 <b>Подписка:</b> {status}\n\n"
        "Выберите действие:",
        reply_markup=get_subscription_actions_keyboard(user_id),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("sub_extend_"))
async def on_sub_extend(callback: CallbackQuery):
    """Продление подписки на фиксированное количество дней + уведомление пользователю."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    parts = callback.data.split("_")
    user_id = int(parts[2])
    days = int(parts[3])

    await db.activate_subscription(user_id, days)

    user = await db.get_user(user_id)
    ends_at_str = user.get("subscription_ends_at")
    ends_formatted = ""
    if ends_at_str:
        try:
            ends_at = datetime.fromisoformat(ends_at_str)
            ends_formatted = ends_at.strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            pass

    name = user.get("full_name") or "—"

    # ✅ Отправляем уведомление пользователю
    await notify_subscription_extended(callback.bot, user_id, days, ends_at_str)

    await callback.message.edit_text(
        f"✅ <b>Подписка продлена!</b>\n\n"
        f"👤 {name} (<code>{user_id}</code>)\n"
        f"➕ Добавлено: <b>{days} дн.</b>\n"
        f"📅 Действует до: <b>{ends_formatted}</b>\n"
        f"📨 Уведомление отправлено",
        reply_markup=get_subscription_actions_keyboard(user_id),
        parse_mode="HTML",
    )
    await callback.answer(f"✅ Продлено на {days} дн. + уведомление", show_alert=True)


@admin_router.callback_query(F.data.startswith("sub_custom_"))
async def on_sub_custom_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    user_id = int(callback.data.split("_")[-1])
    await state.update_data(target_user_id=user_id)
    await state.set_state(SubscriptionFSM.waiting_for_days)

    await callback.message.edit_text(
        f"⏳ <b>Продление подписки</b>\n\n"
        f"Введите количество дней для продления пользователя <code>{user_id}</code>:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="↩️ Отмена", callback_data=f"admin_sub_manage_{user_id}")],
            ]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.message(SubscriptionFSM.waiting_for_days)
async def on_sub_custom_days(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    try:
        days = int(message.text.strip())
        if days <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите положительное число дней.")
        return

    data = await state.get_data()
    user_id = data.get("target_user_id")

    if not user_id:
        await state.clear()
        await message.answer("❌ Ошибка: пользователь не выбран.")
        return

    await db.activate_subscription(user_id, days)
    await state.clear()

    user = await db.get_user(user_id)
    ends_at_str = user.get("subscription_ends_at")
    ends_formatted = ""
    if ends_at_str:
        try:
            ends_at = datetime.fromisoformat(ends_at_str)
            ends_formatted = ends_at.strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            pass

    name = user.get("full_name") or "—"

    # ✅ Отправляем уведомление пользователю
    await notify_subscription_extended(message.bot, user_id, days, ends_at_str)

    await message.answer(
        f"✅ <b>Подписка продлена!</b>\n\n"
        f"👤 {name} (<code>{user_id}</code>)\n"
        f"➕ Добавлено: <b>{days} дн.</b>\n"
        f"📅 Действует до: <b>{ends_formatted}</b>\n"
        f"📨 Уведомление отправлено",
        reply_markup=get_subscription_actions_keyboard(user_id),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data.startswith("sub_end_"))
async def on_sub_end(callback: CallbackQuery):
    """Завершение подписки пользователя + уведомление."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    user_id = int(callback.data.split("_")[-1])
    await db.end_subscription(user_id)

    user = await db.get_user(user_id)
    name = user.get("full_name") or "—"

    # ✅ Отправляем уведомление пользователю
    await notify_subscription_ended(callback.bot, user_id)

    await callback.message.edit_text(
        f"❌ <b>Подписка завершена!</b>\n\n"
        f"👤 {name} (<code>{user_id}</code>)\n"
        f"📅 Статус: <b>неактивна</b>\n"
        f"📨 Уведомление отправлено",
        reply_markup=get_subscription_actions_keyboard(user_id),
        parse_mode="HTML",
    )
    await callback.answer("❌ Подписка завершена + уведомление", show_alert=True)


# ==================== РАССЫЛКА ====================

@admin_router.callback_query(F.data == "admin_broadcast")
async def on_admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    await state.set_state(BroadcastFSM.waiting_for_user_ids)
    await callback.message.edit_text(
        "📨 <b>Рассылка</b>\n\n"
        "Введите ID пользователей через запятую или пробел.\n"
        "Пример: <code>123456 789012 345678</code>\n\n"
        "Или отправьте <b>all</b> для рассылки всем пользователям.",
        reply_markup=get_admin_back_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.message(BroadcastFSM.waiting_for_user_ids)
async def on_broadcast_user_ids(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    text = message.text.strip().lower()

    if text == "all":
        users = await db.get_all_users()
        target_ids = [u["user_id"] for u in users]
    else:
        raw_ids = text.replace(",", " ").split()
        target_ids = []
        for raw_id in raw_ids:
            try:
                target_ids.append(int(raw_id))
            except ValueError:
                continue

    if not target_ids:
        await message.answer("❌ Не удалось распознать ни одного ID. Попробуйте снова.")
        return

    await state.update_data(target_ids=target_ids)
    await state.set_state(BroadcastFSM.waiting_for_message)

    await message.answer(
        f"✅ Выбрано получателей: <b>{len(target_ids)}</b>\n\n"
        "Теперь отправьте сообщение для рассылки (текст, фото, документ).",
        parse_mode="HTML",
    )


@admin_router.message(BroadcastFSM.waiting_for_message)
async def on_broadcast_message(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    target_ids: list[int] = data.get("target_ids", [])

    success = 0
    failed = 0

    for user_id in target_ids:
        try:
            await message.copy_to(chat_id=user_id)
            success += 1
        except Exception as e:
            logger.warning(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
            failed += 1

    await state.clear()

    await message.answer(
        f"📨 <b>Рассылка завершена</b>\n\n"
        f"✅ Успешно: {success}\n"
        f"❌ Ошибок: {failed}",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML",
    )


# ==================== СБРОС FSM ПРИ ОТМЕНЕ ====================

@admin_router.callback_query(BroadcastFSM.waiting_for_user_ids, F.data == "admin_panel")
async def cancel_broadcast_user_ids(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await on_admin_panel(callback, state)


@admin_router.callback_query(BroadcastFSM.waiting_for_message, F.data == "admin_panel")
async def cancel_broadcast_message(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await on_admin_panel(callback, state)


@admin_router.callback_query(SubscriptionFSM.waiting_for_user_id, F.data == "admin_panel")
async def cancel_subscription_user_id(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await on_admin_panel(callback, state)