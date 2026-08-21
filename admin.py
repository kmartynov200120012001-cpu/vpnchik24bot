import logging
from datetime import datetime
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramForbiddenError
from config import ADMIN_ID, PARTNER_COMMISSION_PERCENT
from database import db
from xui_client import xui

logger = logging.getLogger(__name__)
admin_router = Router()

# ==================== FSM ====================
class BroadcastFSM(StatesGroup):
    waiting_for_user_ids = State()
    waiting_for_message = State()

class SubscriptionFSM(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_days = State()

class PartnerFSM(StatesGroup):
    waiting_for_withdraw_amount = State()

class XuiSubIdFSM(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_sub_id = State()

class TariffFSM(StatesGroup):
    waiting_for_price = State()

# ==================== ФИЛЬТР НА АДМИНА ====================
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

# ==================== УВЕДОМЛЕНИЯ ПОЛЬЗОВАТЕЛЯМ ====================
async def notify_subscription_extended(bot, user_id: int, days: int, ends_at_str: str):
    try:
        ends_formatted = ""
        if ends_at_str:
            try:
                ends_at = datetime.fromisoformat(ends_at_str)
                ends_formatted = ends_at.strftime("%d.%m.%Y %H:%M")
            except (ValueError, TypeError):
                pass
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Закрыть", callback_data="delete_notification")]
        ])
        await bot.send_message(
            chat_id=user_id,
            text=(
                "✅ <b>Ваша подписка продлена!</b>\n\n"
                f"➕ Добавлено: <b>{days} дн.</b>\n"
                f"📅 Действует до: <b>{ends_formatted}</b>\n\n"
                "Спасибо, что остаётесь с нами! 💎"
            ),
            reply_markup=kb,
            parse_mode="HTML",
        )
    except TelegramForbiddenError:
        logger.warning(f"Не удалось отправить уведомление пользователю {user_id}: бот заблокирован")
    except Exception as e:
        logger.warning(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

async def notify_subscription_ended(bot, user_id: int):
    try:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Закрыть", callback_data="delete_notification")]
        ])
        await bot.send_message(
            chat_id=user_id,
            text=(
                "❌ <b>Ваша подписка завершена</b>\n\n"
                "Доступ к VPN приостановлен.\n"
                "Продлите подписку, чтобы продолжить пользоваться сервисом."
            ),
            reply_markup=kb,
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
            [InlineKeyboardButton(text="🤝 Партнёры", callback_data="admin_partners")],
            [InlineKeyboardButton(text="📨 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="⏰ Управление подпиской", callback_data="admin_subscription")],
            [InlineKeyboardButton(text="💰 Тарифы", callback_data="admin_tariffs")],
            [InlineKeyboardButton(text="🔑 Обновить VPN-ключ", callback_data="admin_update_sub_id")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="🔄 Стать новым пользователем", callback_data="admin_reset_self")],
        ]
    )

def get_admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад в админку", callback_data="admin_panel")],
        ]
    )

def get_subscription_actions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Продлить на 7 дней", callback_data=f"sub_extend_{user_id}_7")],
            [InlineKeyboardButton(text="➕ Продлить на 30 дней", callback_data=f"sub_extend_{user_id}_30")],
            [InlineKeyboardButton(text="➕ Продлить на 90 дней", callback_data=f"sub_extend_{user_id}_90")],
            [InlineKeyboardButton(text="➕ Продлить на 365 дней", callback_data=f"sub_extend_{user_id}_365")],
            [InlineKeyboardButton(text="⏳ Продлить на N дней", callback_data=f"sub_custom_{user_id}")],
            [InlineKeyboardButton(text="🔑 Обновить VPN-ключ", callback_data=f"admin_update_sub_id_user_{user_id}")],
            [InlineKeyboardButton(text="❌ Завершить подписку", callback_data=f"sub_end_{user_id}")],
            [InlineKeyboardButton(text="↩️ Назад к списку", callback_data="admin_users")],
        ]
    )

def get_tariffs_admin_keyboard(tariffs: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for t in tariffs:
        status = "✅" if t["is_active"] else "❌"
        per_month = f" ({round(t['price'] / t['months'])} ₽/мес)" if t["months"] >= 3 else ""
        text = f"{status} {t['name']} — {t['price']} ₽{per_month}"
        buttons.append([InlineKeyboardButton(
            text=text,
            callback_data=f"tariff_admin_{t['callback']}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Назад в админку", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_tariff_manage_keyboard(tariff: dict) -> InlineKeyboardMarkup:
    toggle_text = "❌ Выключить" if tariff["is_active"] else "✅ Включить"
    toggle_cb = f"tariff_toggle_{tariff['callback']}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Изменить цену", callback_data=f"tariff_price_{tariff['callback']}")],
        [InlineKeyboardButton(text=toggle_text, callback_data=toggle_cb)],
        [InlineKeyboardButton(text="↩️ Назад к тарифам", callback_data="admin_tariffs")],
    ])

# ==================== КОМАНДА /admin ====================
@admin_router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    await message.answer(
        "⚙️ <b>Админ-панель</b>\n\nВыберите действие:",
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
        "⚙️ <b>Админ-панель</b>\n\nВыберите действие:",
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
            status = f"✅ активна до {ends_at.strftime('%d.%m.%Y %H:%M')}" if ends_at > datetime.now() else "❌ истекла"
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
            status = f"✅ активна до {ends_at.strftime('%d.%m.%Y %H:%M')}" if ends_at > datetime.now() else "❌ истекла"
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
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    parts = callback.data.split("_")
    user_id = int(parts[2])
    days = int(parts[3])
    await db.activate_subscription(user_id, days)
    try:
        email, sub_id = await db.get_xui_client(user_id)
        if email:
            await xui.update_client_expiry(email, days, extend=True)
        else:
            result = await xui.add_client(user_id=user_id, days=days)
            await db.save_xui_client(user_id, result["email"], result["sub_id"])
    except Exception as e:
        logging.error(f"Не удалось продлить 3x-ui клиента для {user_id} (admin extend): {e}")
    user = await db.get_user(user_id)
    ends_at_str = user.get("subscription_ends_at")
    ends_formatted = ""
    if ends_at_str:
        try:
            ends_formatted = datetime.fromisoformat(ends_at_str).strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            pass
    name = user.get("full_name") or "—"
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
    await callback.answer(f"✅ Продлено на {days} дн.", show_alert=True)

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
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Отмена", callback_data=f"admin_sub_manage_{user_id}")],
        ]),
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
        await message.answer("⚠️ Ошибка: пользователь не выбран.")
        return
    await db.activate_subscription(user_id, days)
    try:
        email, sub_id = await db.get_xui_client(user_id)
        if email:
            await xui.update_client_expiry(email, days, extend=True)
        else:
            result = await xui.add_client(user_id=user_id, days=days)
            await db.save_xui_client(user_id, result["email"], result["sub_id"])
    except Exception as e:
        logging.error(f"Не удалось продлить 3x-ui клиента для {user_id} (admin custom days): {e}")
    await state.clear()
    user = await db.get_user(user_id)
    ends_at_str = user.get("subscription_ends_at")
    ends_formatted = ""
    if ends_at_str:
        try:
            ends_formatted = datetime.fromisoformat(ends_at_str).strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            pass
    name = user.get("full_name") or "—"
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
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    user_id = int(callback.data.split("_")[-1])
    await db.end_subscription(user_id)
    try:
        email, sub_id = await db.get_xui_client(user_id)
        if email:
            await xui.expire_client(email)
    except Exception as e:
        logging.error(f"Не удалось завершить 3x-ui клиента для {user_id} (admin sub_end): {e}")
    user = await db.get_user(user_id)
    name = user.get("full_name") or "—"
    await notify_subscription_ended(callback.bot, user_id)
    await callback.message.edit_text(
        f"❌ <b>Подписка завершена!</b>\n\n"
        f"👤 {name} (<code>{user_id}</code>)\n"
        f"📅 Статус: <b>неактивна</b>\n"
        f"📨 Уведомление отправлено",
        reply_markup=get_subscription_actions_keyboard(user_id),
        parse_mode="HTML",
    )
    await callback.answer("❌ Подписка завершена", show_alert=True)

# ==================== ТАРИФЫ ====================
@admin_router.callback_query(F.data == "admin_tariffs")
async def on_admin_tariffs(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    tariffs = await db.get_all_tariffs_admin()
    await callback.message.edit_text(
        "💰 <b>Управление тарифами</b>\n\n"
        "Выберите тариф для редактирования:\n"
        "<i>✅ — активен, ❌ — выключен</i>",
        reply_markup=get_tariffs_admin_keyboard(tariffs),
        parse_mode="HTML",
    )
    await callback.answer()

@admin_router.callback_query(F.data.startswith("tariff_admin_"))
async def on_tariff_admin_select(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    cb = callback.data.replace("tariff_admin_", "")
    tariff = await db.get_tariff_by_callback(cb)
    if not tariff:
        await callback.answer("❌ Тариф не найден.", show_alert=True)
        return
    status = "✅ активен" if tariff["is_active"] else "❌ выключен"
    per_month = f"\n💰 Стоимость в месяц: <b>{round(tariff['price'] / tariff['months'])} ₽</b>" if tariff["months"] >= 3 else ""
    await callback.message.edit_text(
        f"💰 <b>Тариф «{tariff['name']}»</b>\n\n"
        f"📅 Срок: <b>{tariff['days']} дн.</b>\n"
        f"💵 Цена: <b>{tariff['price']} ₽</b>{per_month}\n"
        f"Статус: <b>{status}</b>\n\n"
        "Выберите действие:",
        reply_markup=get_tariff_manage_keyboard(tariff),
        parse_mode="HTML",
    )
    await callback.answer()

@admin_router.callback_query(F.data.startswith("tariff_price_"))
async def on_tariff_price_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    cb = callback.data.replace("tariff_price_", "")
    tariff = await db.get_tariff_by_callback(cb)
    if not tariff:
        await callback.answer("❌ Тариф не найден.", show_alert=True)
        return
    await state.update_data(tariff_callback=cb)
    await state.set_state(TariffFSM.waiting_for_price)
    await callback.message.edit_text(
        f"💰 <b>Изменение цены тарифа «{tariff['name']}»</b>\n\n"
        f"Текущая цена: <b>{tariff['price']} ₽</b>\n\n"
        "Введите новую цену в рублях (целое число):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Отмена", callback_data=f"tariff_admin_{cb}")],
        ]),
        parse_mode="HTML",
    )
    await callback.answer()

@admin_router.message(TariffFSM.waiting_for_price)
async def on_tariff_price_input(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        price = int(message.text.strip())
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое положительное число.")
        return
    data = await state.get_data()
    cb = data["tariff_callback"]
    await db.update_tariff_price(cb, price)
    await state.clear()
    tariff = await db.get_tariff_by_callback(cb)
    await message.answer(
        f"✅ <b>Цена обновлена!</b>\n\n"
        f"Тариф «{tariff['name']}»: <b>{price} ₽</b>",
        reply_markup=get_tariff_manage_keyboard(tariff),
        parse_mode="HTML",
    )

@admin_router.callback_query(F.data.startswith("tariff_toggle_"))
async def on_tariff_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    cb = callback.data.replace("tariff_toggle_", "")
    tariff = await db.get_tariff_by_callback(cb)
    if not tariff:
        await callback.answer("❌ Тариф не найден.", show_alert=True)
        return
    new_state = not tariff["is_active"]
    await db.set_tariff_active(cb, new_state)
    tariff = await db.get_tariff_by_callback(cb)
    status_text = "✅ активен" if tariff["is_active"] else "❌ выключен"
    per_month = f"\n💰 Стоимость в месяц: <b>{round(tariff['price'] / tariff['months'])} ₽</b>" if tariff["months"] >= 3 else ""
    await callback.answer(f"Тариф {'включён ✅' if new_state else 'выключен ❌'}", show_alert=False)
    await callback.message.edit_text(
        f"💰 <b>Тариф «{tariff['name']}»</b>\n\n"
        f"📅 Срок: <b>{tariff['days']} дн.</b>\n"
        f"💵 Цена: <b>{tariff['price']} ₽</b>{per_month}\n"
        f"Статус: <b>{status_text}</b>\n\n"
        "Выберите действие:",
        reply_markup=get_tariff_manage_keyboard(tariff),
        parse_mode="HTML",
    )

@admin_router.callback_query(TariffFSM.waiting_for_price, F.data.startswith("tariff_admin_"))
async def cancel_tariff_price(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await on_tariff_admin_select(callback)

# ==================== СБРОС СЕБЯ ====================
@admin_router.callback_query(F.data == "admin_reset_self")
async def on_admin_reset_self_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    await callback.message.edit_text(
        "🔄 <b>Стать новым пользователем</b>\n\n"
        "Это удалит вашу запись из базы (триал, подписку, историю транзакций) — "
        "так, как будто вы запускаете бота первый раз.\n\n"
        "Это действие необратимо. Продолжить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, сбросить меня", callback_data="admin_reset_self_confirmed")],
            [InlineKeyboardButton(text="↩️ Отмена", callback_data="admin_panel")],
        ]),
        parse_mode="HTML",
    )
    await callback.answer()

@admin_router.callback_query(F.data == "admin_reset_self_confirmed")
async def on_admin_reset_self_confirmed(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    user_id = callback.from_user.id
    _, xui_email = await db.reset_user(user_id)
    if xui_email:
        try:
            await xui.delete_client(xui_email)
        except Exception as e:
            logging.error(f"Не удалось удалить 3x-ui клиента {xui_email} при сбросе {user_id}: {e}")
    await callback.message.edit_text(
        "✅ <b>Готово!</b>\n\n"
        "Ваша запись удалена из базы (включая VPN-ключ). Отправьте команду /start, "
        "чтобы пройти онбординг как новый пользователь.",
        parse_mode="HTML",
    )
    await callback.answer("🔄 Вы сброшены как новый пользователь", show_alert=True)

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
        await message.answer("⚠️ Не удалось распознать ни одного ID. Попробуйте снова.")
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="delete_notification")]
    ])
    for user_id in target_ids:
        try:
            if message.photo:
                await message.bot.send_photo(
                    chat_id=user_id, photo=message.photo[-1].file_id,
                    caption=message.caption, reply_markup=kb, parse_mode="HTML",
                )
            elif message.document:
                await message.bot.send_document(
                    chat_id=user_id, document=message.document.file_id,
                    caption=message.caption, reply_markup=kb, parse_mode="HTML",
                )
            elif message.sticker:
                await message.bot.send_sticker(chat_id=user_id, sticker=message.sticker.file_id)
                await message.bot.send_message(chat_id=user_id, text=" ", reply_markup=kb)
            else:
                await message.bot.send_message(
                    chat_id=user_id, text=message.text, reply_markup=kb, parse_mode="HTML",
                )
            success += 1
        except Exception as e:
            logger.warning(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
            failed += 1
    await state.clear()
    await message.answer(
        f"📨 <b>Рассылка завершена</b>\n\n"
        f"✅ Успешно: {success}\n❌ Ошибок: {failed}",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML",
    )

# ==================== ЗАКРЫТЬ УВЕДОМЛЕНИЕ ====================
@admin_router.callback_query(F.data == "delete_notification")
async def on_delete_notification(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()

# ==================== СБРОС FSM ====================
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

@admin_router.callback_query(SubscriptionFSM.waiting_for_days, F.data.startswith("admin_sub_manage_"))
async def cancel_subscription_days(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await on_admin_sub_manage(callback)

@admin_router.callback_query(PartnerFSM.waiting_for_withdraw_amount, F.data.startswith("partner_manage_"))
async def cancel_partner_withdraw(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await on_partner_manage(callback)

# ==================== ПАРТНЁРЫ ====================
@admin_router.callback_query(F.data == "admin_partners")
async def on_admin_partners(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    partners = await db.get_all_partners()
    if not partners:
        await callback.message.edit_text(
            "🤝 <b>Партнёры</b>\n\nПартнёров пока нет.\n\n"
            "Пользователи становятся партнёрами самостоятельно, вызвав команду /partner в боте.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📨 Пригласить в партнёры", switch_inline_query="partner_invite")],
                [InlineKeyboardButton(text="↩️ Назад в админку", callback_data="admin_panel")],
            ]),
            parse_mode="HTML",
        )
        await callback.answer()
        return
    buttons = []
    for p in partners:
        user_id = p["user_id"]
        name = p.get("full_name") or "—"
        username = f"@{p['username']}" if p.get("username") else ""
        total_came = await db.get_partner_referrals_count(user_id)
        paid_count = await db.get_partner_referrals_with_paid_count(user_id)
        total_paid = await db.get_partner_referrals_total_paid_amount(user_id)
        commission = round(total_paid * PARTNER_COMMISSION_PERCENT / 100, 2)
        withdrawn = await db.get_partner_withdrawn_amount(user_id)
        text = f"🤝 {name} {username}\n👥 {total_came} | 💳 {paid_count} | 💰 {commission:.0f}₽ | 💸 {withdrawn:.0f}₽"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"partner_manage_{user_id}")])
    buttons.append([InlineKeyboardButton(text="📨 Пригласить в партнёры", switch_inline_query="partner_invite")])
    buttons.append([InlineKeyboardButton(text="↩️ Назад в админку", callback_data="admin_panel")])
    await callback.message.edit_text(
        "🤝 <b>Партнёры</b>\n\nВыберите партнёра для просмотра детальной статистики:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )
    await callback.answer()

@admin_router.callback_query(F.data.startswith("partner_manage_"))
async def on_partner_manage(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    user_id = int(callback.data.split("_")[-1])
    user = await db.get_user(user_id)
    if not user:
        await callback.answer("❌ Пользователь не найден.", show_alert=True)
        return
    name = user.get("full_name") or "—"
    username = f"@{user['username']}" if user.get("username") else "—"
    total_came = await db.get_partner_referrals_count(user_id)
    trial_activated = await db.get_partner_referrals_with_trial_count(user_id)
    paid_count = await db.get_partner_referrals_with_paid_count(user_id)
    total_paid = await db.get_partner_referrals_total_paid_amount(user_id)
    commission = round(total_paid * PARTNER_COMMISSION_PERCENT / 100, 2)
    withdrawn = await db.get_partner_withdrawn_amount(user_id)
    available = round(commission - withdrawn, 2)
    text = (
        f"🤝 <b>Партнёр:</b> {name} ({username})\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n\n"
        f"👥 Пришло по ссылке: <b>{total_came}</b>\n"
        f"🎁 Активировали триал: <b>{trial_activated}</b>\n"
        f"💳 Оплатили подписку: <b>{paid_count}</b>\n"
        f"💰 Сумма оплат: <b>{total_paid:.2f} ₽</b>\n"
        f"💎 Заработок ({PARTNER_COMMISSION_PERCENT}%): <b>{commission:.2f} ₽</b>\n"
        f"💸 Выведено: <b>{withdrawn:.2f} ₽</b>\n"
        f"✅ Доступно к выводу: <b>{available:.2f} ₽</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Отметить вывод", callback_data=f"partner_withdraw_{user_id}")],
        [InlineKeyboardButton(text="❌ Снять статус партнёра", callback_data=f"partner_remove_{user_id}")],
        [InlineKeyboardButton(text="↩️ Назад к партнёрам", callback_data="admin_partners")],
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@admin_router.callback_query(F.data.startswith("partner_withdraw_"))
async def on_partner_withdraw_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    user_id = int(callback.data.split("_")[-1])
    await state.update_data(target_partner_id=user_id)
    await state.set_state(PartnerFSM.waiting_for_withdraw_amount)
    await callback.message.edit_text(
        f"💸 <b>Отметка вывода денег</b>\n\n"
        f"Введите сумму (в рублях), которую вы вывели партнёру <code>{user_id}</code>:\n\n"
        f"<i>Например: 1500</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Отмена", callback_data=f"partner_manage_{user_id}")],
        ]),
        parse_mode="HTML",
    )
    await callback.answer()

@admin_router.message(PartnerFSM.waiting_for_withdraw_amount)
async def on_partner_withdraw_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    partner_id = data.get("target_partner_id")
    try:
        amount = float(message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите положительное число (сумму в рублях).")
        return
    await db.add_partner_withdrawal(partner_id, amount)
    user = await db.get_user(partner_id)
    name = user.get("full_name") or "—"
    new_withdrawn = await db.get_partner_withdrawn_amount(partner_id)
    await state.clear()
    await message.answer(
        f"✅ <b>Вывод отмечен!</b>\n\n"
        f"👤 {name} (<code>{partner_id}</code>)\n"
        f"💸 Выведено: <b>{amount:.2f} ₽</b>\n"
        f"💰 Всего выведено: <b>{new_withdrawn:.2f} ₽</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад к партнёру", callback_data=f"partner_manage_{partner_id}")],
        ]),
        parse_mode="HTML",
    )

@admin_router.callback_query(F.data.startswith("partner_remove_"))
async def on_partner_remove(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    user_id = int(callback.data.split("_")[-1])
    await db.set_partner_status(user_id, False)
    await callback.message.edit_text(
        f"✅ Статус партнёра снят с пользователя <code>{user_id}</code>.",
        reply_markup=get_admin_back_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer("❌ Партнёр удалён", show_alert=True)

# ==================== ОБНОВЛЕНИЕ VPN-КЛЮЧА ====================
@admin_router.callback_query(F.data == "admin_update_sub_id")
async def on_admin_update_sub_id_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    await state.set_state(XuiSubIdFSM.waiting_for_user_id)
    await callback.message.edit_text(
        "🔑 <b>Обновление VPN-ключа</b>\n\nВведите Telegram ID пользователя:",
        reply_markup=get_admin_back_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()

@admin_router.callback_query(F.data.startswith("admin_update_sub_id_user_"))
async def on_admin_update_sub_id_from_user(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    user_id = int(callback.data.split("_")[-1])
    await state.update_data(target_user_id=user_id)
    await state.set_state(XuiSubIdFSM.waiting_for_sub_id)
    await _show_sub_id_prompt(callback.message, user_id, edit=True)
    await callback.answer()

@admin_router.message(XuiSubIdFSM.waiting_for_user_id)
async def on_xui_user_id_input(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите корректный числовой ID.")
        return
    user = await db.get_user(user_id)
    if not user:
        await message.answer(f"❌ Пользователь <code>{user_id}</code> не найден.", parse_mode="HTML")
        return
    await state.update_data(target_user_id=user_id)
    await state.set_state(XuiSubIdFSM.waiting_for_sub_id)
    await _show_sub_id_prompt(message, user_id, edit=False)

async def _show_sub_id_prompt(message, user_id: int, edit: bool):
    user = await db.get_user(user_id)
    current_sub_id = user.get("xui_sub_id") or "—"
    name = user.get("full_name") or "—"
    text = (
        f"🔑 <b>Обновление VPN-ключа</b>\n\n"
        f"👤 {name} (<code>{user_id}</code>)\n"
        f"Текущий sub_id: <code>{current_sub_id}</code>\n\n"
        f"Введите новый sub_id из панели 3x-ui:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Отмена", callback_data="admin_panel")],
    ])
    if edit:
        await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")

@admin_router.message(XuiSubIdFSM.waiting_for_sub_id)
async def on_xui_new_sub_id_input(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    new_sub_id = message.text.strip()
    if not new_sub_id:
        await message.answer("❌ Sub_id не может быть пустым.")
        return
    data = await state.get_data()
    user_id = data["target_user_id"]
    await db.update_xui_sub_id(user_id, new_sub_id)
    await state.clear()
    user = await db.get_user(user_id)
    name = user.get("full_name") or "—"
    await message.answer(
        f"✅ <b>VPN-ключ обновлён!</b>\n\n"
        f"👤 {name} (<code>{user_id}</code>)\n"
        f"🔗 Новый sub_id: <code>{new_sub_id}</code>",
        reply_markup=get_subscription_actions_keyboard(user_id),
        parse_mode="HTML",
    )

@admin_router.callback_query(XuiSubIdFSM.waiting_for_user_id, F.data == "admin_panel")
@admin_router.callback_query(XuiSubIdFSM.waiting_for_sub_id, F.data == "admin_panel")
async def cancel_xui_sub_id_update(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await on_admin_panel(callback, state)
