# main.py

import asyncio
import io
import logging
from datetime import datetime, timedelta
from urllib.parse import quote

import qrcode
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CopyTextButton,
    BufferedInputFile,
    FSInputFile,
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.session.aiohttp import AiohttpSession

from config import BOT_TOKEN, FREE_TRIAL_DAYS, TARIFFS, PROXY_URL
from database import db
from admin import admin_router
from payments import create_payment
from webhook import run_webhook_server
from xui_client import xui

# --- Логирование ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


bot = Bot(token=BOT_TOKEN)

dp = Dispatcher()
router = Router()

CONGRATS_GIF_PATH = "congratulations.gif"
WELCOME_PIC_PATH = "Welcomepic.jpg"


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def generate_qr_code(data: str) -> BufferedInputFile:
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return BufferedInputFile(buffer.read(), filename="qr_code.png")


def get_days_since_registration(created_at_str: str) -> int:
    """Рассчитывает количество дней с момента регистрации."""
    if not created_at_str:
        return 0
    try:
        created_at = datetime.fromisoformat(created_at_str)
        delta = datetime.now() - created_at
        return max(0, delta.days)
    except (ValueError, TypeError):
        return 0


# ==================== КЛАВИАТУРЫ ====================

def get_main_keyboard_new_user() -> InlineKeyboardMarkup:
    """Меню для новых пользователей (триал ещё не использован)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="➕ Подключить VPN",
            callback_data="connect_vpn",
            style="success",
        )],
    ])


def get_main_keyboard_before_activation() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Продлить доступ", callback_data="tariffs", style="success")],
        [InlineKeyboardButton(text="🫂 Получить месяц бесплатно", callback_data="referral")],
        [InlineKeyboardButton(text="💬 Поддержка", callback_data="support")],
    ])


def get_main_keyboard_after_activation() -> InlineKeyboardMarkup:
    """Стандартное меню для ПЛАТНЫХ подписок (не триал)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Продлить доступ", callback_data="tariffs", style="success")],
        [InlineKeyboardButton(text="🫂 Получить месяц бесплатно", callback_data="referral")],
        [
            InlineKeyboardButton(text="📖 Инструкция", callback_data="connect_vpn"),
            InlineKeyboardButton(text="💬 Поддержка", callback_data="support"),
        ],
    ])


def get_trial_dynamic_keyboard(key_link: str) -> InlineKeyboardMarkup:
    """Динамическое меню для всего периода действия триала."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Продлить доступ", callback_data="tariffs", style="success")],
        [InlineKeyboardButton(text="🫂 Пригласить друзей", callback_data="referral")],
        [
            InlineKeyboardButton(text="📖 Инструкция", callback_data="connect_vpn"),
            InlineKeyboardButton(text="💬 Поддержка", callback_data="support"),
        ],
    ])


def get_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Главное меню", callback_data="back_to_menu")],
    ])


def get_referral_keyboard(ref_link: str, referrals_count: int) -> InlineKeyboardMarkup:
    share_text = "Привет! Держи крутой VPN — первые 3 дня бесплатно 🎁\n"
    share_url = f"https://t.me/share/url?url={quote(ref_link)}&text={quote(share_text)}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Скопировать ссылку", copy_text=CopyTextButton(text=ref_link))],
        [
            InlineKeyboardButton(text="🔳 Получить QR-код", callback_data="get_qr_code"),
            InlineKeyboardButton(text="📤 Пригласить друга", url=share_url),
        ],
        [InlineKeyboardButton(text=f"👥 Мои рефералы ({referrals_count})", callback_data="my_referrals")],
        [InlineKeyboardButton(text="← Главное меню", callback_data="back_to_menu")],
    ])


def get_device_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Android", callback_data="connect_android")],
        [InlineKeyboardButton(text="📱 iOS | iPhone, iPad", callback_data="connect_ios")],
        [InlineKeyboardButton(text="💻 Windows", callback_data="connect_windows")],
        [InlineKeyboardButton(text="💻 macOS", callback_data="connect_macos")],
        [InlineKeyboardButton(text="📺 Android TV", callback_data="connect_android_tv")],
        [InlineKeyboardButton(text="← Главное меню", callback_data="back_to_menu")],
    ])


# --- Универсальные конструкторы клавиатур для инструкций ---

def _step2_kb(url: str, next_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Скачать приложение", url=url)],
        [InlineKeyboardButton(text="✅ Сделано", callback_data=next_cb)],
        [InlineKeyboardButton(text="← Назад", callback_data="connect_vpn")],
    ])


def _step3_kb(key_link: str, done_cb: str, back_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📌 Копировать ключ-ссылку", copy_text=CopyTextButton(text=key_link))],
        [InlineKeyboardButton(text="✅ Сделано", callback_data=done_cb)],
        [InlineKeyboardButton(text="← Назад", callback_data=back_cb)],
    ])


def _tv_step2_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📺 Подключить AndroidTV", url="https://telegra.ph/Instrukciya-Android-TV-08-10")],
        [InlineKeyboardButton(text="✅ Сделано", callback_data="android_tv_done")],
        [InlineKeyboardButton(text="← Назад", callback_data="connect_vpn")],
    ])


def _format_tariff_text(tariff: dict) -> str:
    """Формирует текст кнопки тарифа с автоматическим расчётом ₽/мес."""
    if tariff["months"] >= 3:
        per_month = round(tariff["price"] / tariff["months"])
        return f"{tariff['name']} — {tariff['price']} ₽ ({per_month} ₽/мес)"
    return f"{tariff['name']} — {tariff['price']} ₽"


def get_tariffs_keyboard() -> InlineKeyboardMarkup:
    """Меню тарифов с описанием и специальными стилями."""
    buttons = []
    
    for t in TARIFFS:
        text = _format_tariff_text(t)
        cb = t["callback"]
        
        # Добавляем эмодзи и стиль для конкретных тарифов
        if t["months"] == 6:
            text += " 🔥"
            style = "success" # Зеленый
        elif t["months"] == 12:
            text += " 💎"
            style = "primary" # Синий
        else:
            style = None # Стандартный стиль
            
        buttons.append([InlineKeyboardButton(text=text, callback_data=cb, style=style)])

    buttons.append([InlineKeyboardButton(text="← Главное меню", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ==================== ТЕКСТЫ И СТАТУСЫ ====================

def get_subscription_status(user: dict) -> tuple[str, str, str]:
    """Возвращает (статус_код, статус_эмодзи, дата_окончания)."""
    trial_used = user.get("trial_used", 0)
    is_trial = user.get("is_trial", 0)
    ends_at_str = user.get("subscription_ends_at")

    if ends_at_str:
        try:
            ends_at = datetime.fromisoformat(ends_at_str)
            if ends_at > datetime.now():
                fmt = ends_at.strftime("%d.%m.%Y %H:%M")
                if is_trial:
                    return "trial_active", "🎁 триал активен", fmt
                return "active", "✅ активен", fmt
            return "expired", "❌ истёк", ""
        except (ValueError, TypeError):
            return "expired", "❌ истёк", ""
    if trial_used:
        return "expired", "❌ истёк", ""
    return "new", "🎁 доступен", ""


def get_welcome_text(name: str) -> str:
    """Приветственное сообщение для новых пользователей."""
    return (
        f"👋 Привет, <b>{name}</b>! Это <b>VPNчик 24</b>\n\n"
        f"😻 Первые 3 дня — БЕСПЛАТНО\n\n"
        f"👇 Жми кнопку ниже и подключайся"
    )


def get_paid_profile_text(user: dict) -> str:
    """Текст профиля для активной платной подписки (2 варианта)."""
    ends_at_str = user.get("subscription_ends_at")

    sub_id = user.get("xui_sub_id")
    if sub_id:
        key_link = xui.build_subscription_url(sub_id)
    else:
        # У пользователя ещё нет VPN-клиента (не проходил настройку устройства) —
        # отправляем в шаг выбора устройства, где ключ создастся автоматически.
        key_link = "Ключ ещё не создан — выберите устройство в разделе «Подключиться»"

    if not ends_at_str:
        return get_profile_text(user) # Fallback

    try:
        ends_at = datetime.fromisoformat(ends_at_str)
        now = datetime.now()
        delta = ends_at - now
        
        if delta.total_seconds() <= 0:
             return get_profile_text(user) # Истекла

        # Форматируем дату окончания
        end_date_fmt = ends_at.strftime("%d %B %Y, %H:%M")
        # Русские месяцы
        months_ru = {
            "January": "января", "February": "февраля", "March": "марта", "April": "апреля",
            "May": "мая", "June": "июня", "July": "июля", "August": "августа",
            "September": "сентября", "October": "октября", "November": "ноября", "December": "декабря"
        }
        for eng, ru in months_ru.items():
            end_date_fmt = end_date_fmt.replace(eng, ru)
            
        # Расчет оставшегося времени
        days_left = delta.days
        hours_left = delta.seconds // 3600
        
        if days_left > 3:
            # Вариант 1: Больше 3 дней
            text = (
                f"🟢 <b>VPN подключен</b>\n\n"
                f"<b>Подписка действует до</b> <i>{end_date_fmt}</i>\n\n"
                f"Продлить доступ можно в любой момент – без потери текущего периода\n\n"
                f"🔑 <b>Ваш ключ доступа:</b>\n"
                f"<blockquote><code>{key_link}</code></blockquote>"
            )
        else:
            # Вариант 2: 3 дня и меньше
            # Склонение дней и часов
            if days_left > 0:
                day_word = "день" if days_left == 1 else "дня" if days_left < 5 else "дней"
                hour_word = "час" if hours_left == 1 else "часа" if hours_left < 5 else "часов"
                time_left_text = f"{days_left} {day_word} {hours_left} {hour_word}"
            else:
                hour_word = "час" if hours_left == 1 else "часа" if hours_left < 5 else "часов"
                time_left_text = f"{hours_left} {hour_word}"
            
            text = (
                f"🟡 <b>VPN подключен</b>\n\n"
                f"Подписка скоро закончится ⏳\n"
                f"<b>Осталось:</b> <i>{time_left_text}</i>\n\n"
                f"Продлите заранее, чтобы не потерять доступ к VPN\n\n"
                f"🔑 <b>Ваш ключ доступа:</b>\n"
                f"<blockquote><code>{key_link}</code></blockquote>"
            )
            
    except Exception as e:
        logging.error(f"Error formatting paid profile: {e}")
        return get_profile_text(user)

    return text


def get_profile_text(user: dict) -> str:
    """Общая функция профиля. Для платных использует новую логику."""
    code, _, _ = get_subscription_status(user)
    
    if code == "active":
        return get_paid_profile_text(user)
        
    # Старая логика для остальных случаев
    uid = user.get("user_id", "N/A")
    name = user.get("full_name", "Не указано")
    status, msg_short = "", ""
    
    if code == "new":
        msg_short = f"Вам доступен бесплатный период на {FREE_TRIAL_DAYS} дня!"
    elif code == "trial_active":
         _, _, date_str = get_subscription_status(user)
         msg_short = f"Действует до: {date_str}"
    else:
        msg_short = "Оформите подписку, чтобы продолжить."
        
    status_map = {
        "new": "🎁 доступен",
        "trial_active": "🎁 триал активен",
        "expired": "❌ истёк"
    }
    status = status_map.get(code, "")

    return (
        f"👤 <b>Профиль:</b>\n<blockquote>ID: <code>{uid}</code>\nИмя: {name}</blockquote>\n\n"
        f"🎁 <b>Подписка:</b>\n<blockquote>Статус: {status}\n\n<i>{msg_short}</i></blockquote>"
    )


def get_trial_welcome_text(user: dict, key_link: str) -> str:
    """Текст динамического меню на весь период триала."""
    ends_at_str = user.get("subscription_ends_at")
    
    # Рассчитываем оставшееся время
    remaining_text = "—"
    if ends_at_str:
        try:
            ends_at = datetime.fromisoformat(ends_at_str)
            now = datetime.now()
            if ends_at > now:
                delta = ends_at - now
                days = delta.days
                hours = delta.seconds // 3600
                
                # Форматируем текст
                if days > 0:
                    day_word = "день" if days == 1 else "дня" if days < 5 else "дней"
                    remaining_text = f"{days} {day_word} {hours} часа"
                else:
                    remaining_text = f"{hours} часа"
        except (ValueError, TypeError):
            pass

    return (
        f"🟢 <b>VPN работает</b>\n"
        f"\n"
        f"<blockquote>До 5 устройств\n"
        f"<b>Осталось:</b> <i>{remaining_text}</i></blockquote>\n"
        f"\n"
        f"💎 Продлить доступ можно в любой момент\n"
        f"\n"
        f"🔑 <b>Ваш ключ доступа:</b>\n"
        f"<blockquote><code>{key_link}</code></blockquote>\n"
    )


def get_keyboard_for_user(user: dict) -> InlineKeyboardMarkup:
    """Выбирает клавиатуру в зависимости от статуса.
    Для триала возвращает динамическую клавиатуру с ключом.
    """
    code, _, _ = get_subscription_status(user)
    if code == "trial_active":
        # Для триала всегда динамическая клавиатура
        sub_id = user.get("xui_sub_id")
        key_link = xui.build_subscription_url(sub_id) if sub_id else "⚠️ Ключ ещё не создан"
        return get_trial_dynamic_keyboard(key_link)
    elif code == "active":
        return get_main_keyboard_after_activation()
    elif code == "new":
        return get_main_keyboard_new_user()
    else:
        return get_main_keyboard_before_activation()


# ==================== УПРАВЛЕНИЕ МЕНЮ ====================

async def delete_old_menu(bot: Bot, chat_id: int, user_id: int) -> None:
    old_id = await db.get_menu_message_id(user_id)
    if old_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_id)
        except TelegramBadRequest:
            pass


async def send_main_menu(bot: Bot, chat_id: int, user_id: int, is_activation: bool = False) -> None:
    """Единая функция отправки главного меню."""
    await delete_old_menu(bot, chat_id, user_id)
    user_data = await db.get_user(user_id)
    code, _, _ = get_subscription_status(user_data)

    if code == "trial_active":
        if is_activation:
            await bot.send_animation(chat_id=chat_id, animation=FSInputFile(CONGRATS_GIF_PATH))

        sub_id = user_data.get("xui_sub_id")
        key_link = xui.build_subscription_url(sub_id) if sub_id else "⚠️ Ключ ещё не создан"
        sent = await bot.send_message(
            chat_id=chat_id,
            text=get_trial_welcome_text(user_data, key_link),
            reply_markup=get_trial_dynamic_keyboard(key_link),
            parse_mode="HTML",
        )
    elif code == "new":
        # Приветственное сообщение с КАРТИНКОЙ
        name = user_data.get("full_name", "друг")
        sent = await bot.send_photo(
            chat_id=chat_id,
            photo=FSInputFile(WELCOME_PIC_PATH),
            caption=get_welcome_text(name),
            reply_markup=get_main_keyboard_new_user(),
            parse_mode="HTML",
        )
    else:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=get_profile_text(user_data),
            reply_markup=get_keyboard_for_user(user_data),
            parse_mode="HTML",
        )

    await db.save_menu_message_id(user_id, sent.message_id)


# ==================== ХЭНДЛЕРЫ ====================

@router.message(CommandStart())
async def cmd_start(message: Message):
    referrer_id = None
    if message.text and message.text.startswith("/start ref_"):
        try:
            referrer_id = int(message.text.split("ref_")[1].split()[0])
        except (ValueError, IndexError):
            pass

    await db.add_user(message.from_user.id, message.from_user.username, message.from_user.full_name, referrer_id)
    await send_main_menu(bot, message.chat.id, message.from_user.id, is_activation=False)


@router.callback_query(F.data == "free_trial")
async def on_free_trial(callback: CallbackQuery):
    await db.activate_trial(callback.from_user.id)
    await send_main_menu(bot, callback.message.chat.id, callback.from_user.id, is_activation=True)
    await callback.answer("🎉 Триал активирован!", show_alert=True)


@router.callback_query(F.data == "back_to_menu")
async def on_back_to_menu(callback: CallbackQuery):
    await send_main_menu(bot, callback.message.chat.id, callback.from_user.id, is_activation=False)
    await callback.answer()


@router.callback_query(F.data == "tariffs")
async def on_tariffs(callback: CallbackQuery):
    await callback.message.edit_text(
        "🌐 <b>Тарифы</b>\n\n"
        "Что входит в подписку:\n"
        "✓ <i>Безлимитный трафик и скорость</i>\n"
        "✓ <i>До 5 устройств</i>\n"
        "✓ <i>Современный протокол — устойчив к ограничениям сети</i>\n\n"
        "Дольше срок — ниже цена за месяц 👇",
        reply_markup=get_tariffs_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "referral")
async def on_referral(callback: CallbackQuery):
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{callback.from_user.id}"
    count = await db.get_referrals_count(callback.from_user.id)
    text = (
        "🎁 <b>Хочешь бесплатный VPN навсегда?</b>\n\nЗови друзей — и получай дни VPN бесплатно.\n\n"
        "✅ Друг оплатил подписку → <b>+10 дней</b> тебе\n✅ Друг продлил подписку → <b>ещё +10 дней</b>\n\n"
        "<b>3 активных друга = +30 дней каждый месяц.</b>\nЭто и есть <b>бесплатный VPN навсегда</b> 🔥\n\n"
        "VPN сейчас нужен всем, так что друзья точно скажут спасибо 🙂\n\n"
        f"👇 <b>Твоя ссылка для приглашений:</b>\n<code>{ref_link}</code>"
    )
    await callback.message.edit_text(text, reply_markup=get_referral_keyboard(ref_link, count), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "support")
async def on_support(callback: CallbackQuery):
    await callback.message.edit_text(
        "Напишите сюда, мы вам обязательно поможем 😊\n"
        '<a href="https://t.me/vpnchiksupportbot">@vpnchiksupportbot</a> 👨‍💻',
        reply_markup=get_back_keyboard(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "get_qr_code")
async def on_get_qr_code(callback: CallbackQuery):
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{callback.from_user.id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Закрыть", callback_data="delete_qr_message")]])
    await callback.message.answer_photo(photo=generate_qr_code(ref_link), reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "delete_qr_message")
async def on_delete_qr_message(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "my_referrals")
async def on_my_referrals(callback: CallbackQuery):
    refs = await db.get_referrals(callback.from_user.id)
    if not refs:
        text = "👥 <b>Твои рефералы</b>\n\nУ тебя пока нет приглашённых друзей.\n\nПоделись своей ссылкой — и получай бонусные дни VPN за каждого друга, который оформит подписку! 🎁"
    else:
        lines = [f"👥 <b>Твои рефералы ({len(refs)}):</b>\n"]
        for i, r in enumerate(refs, 1):
            n = r.get("full_name") or "—"
            u = f"@{r['username']}" if r.get("username") else "—"
            d = ""
            if r.get("created_at"):
                try:
                    d = f" | с {datetime.fromisoformat(r['created_at']).strftime('%d.%m.%Y')}"
                except Exception:
                    pass
            lines.append(f"<b>{i}.</b> {n} ({u}){d}")
        lines.append(f"\n💎 <b>Всего бонусных дней начислено:</b> {len(refs) * 10}")
        text = "\n".join(lines)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Назад к ссылке", callback_data="referral")],
        [InlineKeyboardButton(text="← Главное меню", callback_data="back_to_menu")],
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("tariff_"))
async def on_tariff_selected(callback: CallbackQuery):
    await callback.answer("💳 Создаём ссылку на оплату...")

    tariff_cb = callback.data
    tariff = next((t for t in TARIFFS if t["callback"] == tariff_cb), None)
    if not tariff:
        await callback.message.answer("⚠️ Тариф не найден, попробуйте выбрать снова.")
        return

    days = tariff["days"]

    try:
        payment = await create_payment(
            amount=tariff["price"],
            description=f"Подписка VPNчик24 — {tariff['name']}",
            user_id=callback.from_user.id,
            username=callback.from_user.username,
        )
    except Exception as e:
        logging.error(f"Ошибка создания платежа Platega: {e}")
        await callback.message.answer(
            "⚠️ Не удалось создать ссылку на оплату. Попробуйте позже или обратитесь в поддержку."
        )
        return

    transaction_id = payment.get("transactionId")
    pay_url = payment.get("redirect")

    if not transaction_id or not pay_url:
        logging.error(f"Platega вернула неожиданный ответ: {payment}")
        await callback.message.answer("⚠️ Ошибка при создании платежа. Попробуйте позже.")
        return

    await db.create_transaction(
        transaction_id=transaction_id,
        user_id=callback.from_user.id,
        tariff_callback=tariff_cb,
        months=tariff["months"],
        days=days,
        amount=tariff["price"],
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=pay_url)],
        [InlineKeyboardButton(text="← Назад к тарифам", callback_data="tariffs")],
    ])
    await callback.message.edit_text(
        f"🌐 <b>Оплата тарифа «{tariff['name']}»</b>\n\n"
        f"Сумма: <b>{tariff['price']} ₽</b>\n\n"
        "Нажмите «Оплатить» и завершите платёж. "
        "После успешной оплаты подписка продлится автоматически — вы получите уведомление здесь.",
        reply_markup=kb,
        parse_mode="HTML",
    )


# ==================== ПОДКЛЮЧЕНИЕ VPN ====================

@router.callback_query(F.data == "connect_vpn")
async def on_connect_vpn(callback: CallbackQuery):
    """Шаг 1 из 3 — выбор устройства.
    
    Если текущее сообщение — фото (приветственное меню), 
    то удаляем его и отправляем новое текстовое.
    Если текущее сообщение — текст, то просто редактируем.
    """
    if callback.message.photo:
        # Это приветственное фото-меню — удаляем и отправляем новое
        await delete_old_menu(bot, callback.message.chat.id, callback.from_user.id)
        
        sent = await callback.message.answer(
            "🏁 <b>Шаг 1 из 3</b>\n\nВыберите своё устройство ⤵️",
            reply_markup=get_device_keyboard(),
            parse_mode="HTML",
        )
        await db.save_menu_message_id(callback.from_user.id, sent.message_id)
    else:
        # Обычное текстовое меню — просто редактируем
        await callback.message.edit_text(
            "🏁 <b>Шаг 1 из 3</b>\n\nВыберите своё устройство ⤵️",
            reply_markup=get_device_keyboard(),
            parse_mode="HTML",
        )
    
    await callback.answer()


async def get_or_create_subscription_link(user_id: int) -> str:
    """
    Возвращает subscription URL для пользователя.
    Если у пользователя уже есть VPN-клиент в 3x-ui (он проходил настройку на другой
    платформе или продлевал подписку) — просто возвращает существующую ссылку.
    Иначе создаёт нового клиента на FREE_TRIAL_DAYS дней (триал) и сохраняет в БД.

    Срок действия клиента в 3x-ui синхронизируется отдельно при оплате (см. webhook.py),
    эта функция отвечает только за выдачу ссылки и первичное создание клиента.
    """
    email, sub_id = await db.get_xui_client(user_id)

    if email and sub_id:
        return xui.build_subscription_url(sub_id)

    # Клиента ещё нет — создаём с пробным сроком
    try:
        result = await xui.add_client(user_id=user_id, days=FREE_TRIAL_DAYS)
    except Exception as e:
        logging.error(f"Не удалось создать 3x-ui клиента для {user_id}: {e}")
        return "⚠️ Не удалось сгенерировать ключ. Попробуйте позже или напишите в поддержку."

    await db.save_xui_client(user_id, result["email"], result["sub_id"])
    return xui.build_subscription_url(result["sub_id"])


# --- ANDROID ---
ANDROID_STEP2_TEXT = (
    "Установка подписки. 🏁 <b>Шаг 2 из 3</b>\n\nСкачайте и установите приложение для VPN-подключения ⤵️\n\n"
    "1️⃣ Нажмите на кнопку \"🌐 Скачать приложение\"\n\n2️⃣ Как приложение будет скачано — кликайте на кнопку \"✅ Сделано\"\n\n"
    "Если приложение по кнопке ниже недоступно, нажмите сюда 👉 "
    '<a href="https://github.com/Happ-proxy/happ-android/releases/latest/download/Happ.apk">Happ APK</a>'
)


@router.callback_query(F.data == "connect_android")
async def on_connect_android(cb: CallbackQuery):
    await cb.message.edit_text(ANDROID_STEP2_TEXT, reply_markup=_step2_kb("https://play.google.com/store/apps/details?id=com.happproxy&hl=ru&pli=1", "android_step3"), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "android_step2")
async def on_android_step2(cb: CallbackQuery):
    await cb.message.edit_text(ANDROID_STEP2_TEXT, reply_markup=_step2_kb("https://play.google.com/store/apps/details?id=com.happproxy&hl=ru&pli=1", "android_step3"), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "android_step3")
async def on_android_step3(cb: CallbackQuery):
    # Активируем триал при первом входе на шаг 3
    user_data = await db.get_user(cb.from_user.id)
    if not user_data.get("trial_used", 0):
        await db.activate_trial(cb.from_user.id)

    key = await get_or_create_subscription_link(cb.from_user.id)
    text = (
        "Установка подписки. 🏁 <b>Шаг 3 из 3</b>\n\n"
        "Вставьте свою ключ-ссылку в приложение, нажав на кнопку \"📌 Добавить подписку\" ⤵️\n\n"
        f"Ваш ключ-ссылка:\n<code>{key}</code>"
    )
    await cb.message.edit_text(text, reply_markup=_step3_kb(key, "android_done", "android_step2"), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "android_done")
async def on_android_done(cb: CallbackQuery):
    await send_main_menu(bot, cb.message.chat.id, cb.from_user.id, is_activation=False)
    await cb.answer()


# --- iOS ---
IOS_STEP2_TEXT = (
    "Установка подписки. 🏁 <b>Шаг 2 из 3</b>\n\nСкачайте и установите приложение для VPN-подключения ⤵️\n\n"
    "1️⃣ Нажмите на кнопку \"🌐 Скачать приложение\"\n\n2️⃣ Как приложение будет скачано — кликайте на кнопку \"✅ Сделано\"\n\n"
    "Если приложение по кнопке ниже недоступно, нажмите сюда 👉 "
    '<a href="https://apps.apple.com/us/app/happ-proxy-utility/id6504287215">Happ Global</a>'
)


@router.callback_query(F.data == "connect_ios")
async def on_connect_ios(cb: CallbackQuery):
    await cb.message.edit_text(IOS_STEP2_TEXT, reply_markup=_step2_kb("https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973", "ios_step3"), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "ios_step2")
async def on_ios_step2(cb: CallbackQuery):
    await cb.message.edit_text(IOS_STEP2_TEXT, reply_markup=_step2_kb("https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973", "ios_step3"), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "ios_step3")
async def on_ios_step3(cb: CallbackQuery):
    user_data = await db.get_user(cb.from_user.id)
    if not user_data.get("trial_used", 0):
        await db.activate_trial(cb.from_user.id)

    key = await get_or_create_subscription_link(cb.from_user.id)
    text = (
        "Установка подписки. 🏁 <b>Шаг 3 из 3</b>\n\n"
        "Вставьте свою ключ-ссылку в приложение, нажав на кнопку \"📌 Добавить подписку\" ⤵️\n\n"
        f"Ваш ключ-ссылка:\n<code>{key}</code>"
    )
    await cb.message.edit_text(text, reply_markup=_step3_kb(key, "ios_done", "ios_step2"), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "ios_done")
async def on_ios_done(cb: CallbackQuery):
    await send_main_menu(bot, cb.message.chat.id, cb.from_user.id, is_activation=False)
    await cb.answer()


# --- WINDOWS ---
WIN_STEP2_TEXT = (
    "Установка подписки. 🏁 <b>Шаг 2 из 3</b>\n\nСкачайте и установите приложение для VPN-подключения ⤵️\n\n"
    "1️⃣ Нажмите на кнопку \"🌐 Скачать приложение\"\n\n2️⃣ Как приложение будет скачано — кликайте на кнопку \"✅ Сделано\""
)


@router.callback_query(F.data == "connect_windows")
async def on_connect_windows(cb: CallbackQuery):
    await cb.message.edit_text(WIN_STEP2_TEXT, reply_markup=_step2_kb("https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe", "windows_step3"), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "windows_step2")
async def on_windows_step2(cb: CallbackQuery):
    await cb.message.edit_text(WIN_STEP2_TEXT, reply_markup=_step2_kb("https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe", "windows_step3"), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "windows_step3")
async def on_windows_step3(cb: CallbackQuery):
    user_data = await db.get_user(cb.from_user.id)
    if not user_data.get("trial_used", 0):
        await db.activate_trial(cb.from_user.id)

    key = await get_or_create_subscription_link(cb.from_user.id)
    text = (
        "Установка подписки. 🏁 <b>Шаг 3 из 3</b>\n\n"
        "Вставьте свою ключ-ссылку в приложение, нажав на кнопку \"📌 Добавить подписку\" ⤵️\n\n"
        f"Ваш ключ-ссылка:\n<code>{key}</code>"
    )
    await cb.message.edit_text(text, reply_markup=_step3_kb(key, "windows_done", "windows_step2"), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "windows_done")
async def on_windows_done(cb: CallbackQuery):
    await send_main_menu(bot, cb.message.chat.id, cb.from_user.id, is_activation=False)
    await cb.answer()


# --- MACOS ---
MAC_STEP2_TEXT = (
    "Установка подписки. 🏁 <b>Шаг 2 из 3</b>\n\nСкачайте и установите приложение для VPN-подключения ⤵️\n\n"
    "1️⃣ Нажмите на кнопку \"🌐 Скачать приложение\"\n\n2️⃣ Как приложение будет скачано — кликайте на кнопку \"✅ Сделано\"\n\n"
    "Если приложение по кнопке ниже недоступно, нажмите сюда 👉 "
    '<a href="https://apps.apple.com/us/app/happ-proxy-utility/id6504287215">Happ Global</a>'
)


@router.callback_query(F.data == "connect_macos")
async def on_connect_macos(cb: CallbackQuery):
    await cb.message.edit_text(MAC_STEP2_TEXT, reply_markup=_step2_kb("https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973", "macos_step3"), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "macos_step2")
async def on_macos_step2(cb: CallbackQuery):
    await cb.message.edit_text(MAC_STEP2_TEXT, reply_markup=_step2_kb("https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973", "macos_step3"), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "macos_step3")
async def on_macos_step3(cb: CallbackQuery):
    user_data = await db.get_user(cb.from_user.id)
    if not user_data.get("trial_used", 0):
        await db.activate_trial(cb.from_user.id)

    key = await get_or_create_subscription_link(cb.from_user.id)
    text = (
        "Установка подписки. 🏁 <b>Шаг 3 из 3</b>\n\n"
        "Вставьте свою ключ-ссылку в приложение, нажав на кнопку \"📌 Добавить подписку\" ⤵️\n\n"
        f"Ваш ключ-ссылка:\n<code>{key}</code>"
    )
    await cb.message.edit_text(text, reply_markup=_step3_kb(key, "macos_done", "macos_step2"), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "macos_done")
async def on_macos_done(cb: CallbackQuery):
    await send_main_menu(bot, cb.message.chat.id, cb.from_user.id, is_activation=False)
    await cb.answer()


# --- ANDROID TV ---

@router.callback_query(F.data == "connect_android_tv")
async def on_connect_android_tv(cb: CallbackQuery):
    # Активируем триал при выборе Android TV
    user_data = await db.get_user(cb.from_user.id)
    if not user_data.get("trial_used", 0):
        await db.activate_trial(cb.from_user.id)
    
    await cb.message.edit_text(
        "Нажмите на кнопку ниже и следуйте инструкции 👇",
        reply_markup=_tv_step2_kb(), parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data == "android_tv_done")
async def on_android_tv_done(cb: CallbackQuery):
    await send_main_menu(bot, cb.message.chat.id, cb.from_user.id, is_activation=False)
    await cb.answer()


# ==================== ЮРИДИЧЕСКИЕ КОМАНДЫ ====================

@router.message(Command("privacy"))
async def cmd_privacy(message: Message):
    """Политика конфиденциальности."""
    await message.answer(
        "🔒 <b>Политика конфиденциальности</b>\n\n"
        "Ознакомьтесь с документом по ссылке ниже:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="📄 Читать политику",
                    url="https://telegra.ph/Politika-konfidencialnosti-06-21-31"
                )],
            ]
        ),
        parse_mode="HTML",
    )


@router.message(Command("agreement"))
async def cmd_agreement(message: Message):
    """Пользовательское соглашение."""
    await message.answer(
        "📜 <b>Пользовательское соглашение</b>\n\n"
        "Ознакомьтесь с документом по ссылке ниже:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="📄 Читать соглашение",
                    url="https://telegra.ph/Polzovatelskoe-soglashenie-04-01-19"
                )],
            ]
        ),
        parse_mode="HTML",
    )


# ==================== ТОЧКА ВХОДА ====================

async def main():
    await db.init()
    logging.info("База данных инициализирована")
    dp.include_router(router)
    dp.include_router(admin_router)
    await bot.delete_webhook(drop_pending_updates=True)

    # Запускаем сервер приёма callback'ов от Platega параллельно с polling
    await run_webhook_server(bot)

    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped")
