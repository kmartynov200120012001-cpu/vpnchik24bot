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

from config import BOT_TOKEN, FREE_TRIAL_DAYS, TARIFFS
from database import db
from admin import admin_router
from payments import create_payment
from webhook import run_webhook_server
from xui_client import xui
from aiogram.types import LinkPreviewOptions

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
    if not created_at_str:
        return 0
    try:
        created_at = datetime.fromisoformat(created_at_str)
        delta = datetime.now() - created_at
        return max(0, delta.days)
    except (ValueError, TypeError):
        return 0


async def get_or_create_subscription_link(user_id: int) -> str:
    """Возвращает subscription URL. Создаёт клиента в 3x-ui если его нет."""
    email, sub_id = await db.get_xui_client(user_id)
    if email and sub_id:
        return xui.build_subscription_url(sub_id)

    try:
        result = await xui.add_client(user_id=user_id, days=FREE_TRIAL_DAYS)
    except Exception as e:
        logging.error(f"Не удалось создать 3x-ui клиента для {user_id}: {e}")
        return "⚠️ Не удалось сгенерировать ключ. Попробуйте позже или напишите в поддержку."

    await db.save_xui_client(user_id, result["email"], result["sub_id"])
    return xui.build_subscription_url(result["sub_id"])


# ==================== КЛАВИАТУРЫ ====================

def get_main_keyboard_new_user() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Подключить VPN", callback_data="connect_vpn", style="success")],
    ])


def get_main_keyboard_before_activation() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Продлить доступ", callback_data="tariffs", style="success")],
        [InlineKeyboardButton(text="🫂 Получить месяц бесплатно", callback_data="referral")],
        [InlineKeyboardButton(text="💬 Поддержка", callback_data="support")],
    ])


def get_main_keyboard_after_activation() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Продлить доступ", callback_data="tariffs", style="success")],
        [InlineKeyboardButton(text="🫂 Получить месяц бесплатно", callback_data="referral")],
        [
            InlineKeyboardButton(text="📖 Инструкция", callback_data="connect_vpn"),
            InlineKeyboardButton(text="💬 Поддержка", callback_data="support"),
        ],
    ])


def get_trial_dynamic_keyboard(key_link: str) -> InlineKeyboardMarkup:
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


# Клавиатура для инструкции Android
def _android_instruction_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово", callback_data="setup_done", style="success")],
        [InlineKeyboardButton(text="🆘 Нужна помощь", callback_data="support")],
        [
            InlineKeyboardButton(text="← Назад", callback_data="connect_vpn"),
            InlineKeyboardButton(text="⮎ Главное меню", callback_data="back_to_menu"),
        ],
    ])


# Клавиатура для инструкции iOS
def _ios_instruction_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово", callback_data="setup_done", style="success")],
        [InlineKeyboardButton(text="🆘 Нужна помощь", callback_data="support")],
        [
            InlineKeyboardButton(text="← Назад", callback_data="connect_vpn"),
            InlineKeyboardButton(text="⮎ Главное меню", callback_data="back_to_menu"),
        ],
    ])


# Клавиатура для инструкции Windows
def _windows_instruction_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово", callback_data="setup_done", style="success")],
        [InlineKeyboardButton(text="🆘 Нужна помощь", callback_data="support")],
        [
            InlineKeyboardButton(text="← Назад", callback_data="connect_vpn"),
            InlineKeyboardButton(text="⮎ Главное меню", callback_data="back_to_menu"),
        ],
    ])


# Клавиатура для инструкции macOS
def _macos_instruction_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово", callback_data="setup_done", style="success")],
        [InlineKeyboardButton(text="🆘 Нужна помощь", callback_data="support")],
        [
            InlineKeyboardButton(text="← Назад", callback_data="connect_vpn"),
            InlineKeyboardButton(text="⮎ Главное меню", callback_data="back_to_menu"),
        ],
    ])


# Старая клавиатура для Android TV
def _tv_step2_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📺 Подключить AndroidTV", url="https://telegra.ph/Instrukciya-Android-TV-08-10")],
        [InlineKeyboardButton(text="✅ Сделано", callback_data="android_tv_done")],
        [InlineKeyboardButton(text="← Назад", callback_data="connect_vpn")],
    ])


def _format_tariff_text(tariff: dict) -> str:
    if tariff["months"] >= 3:
        per_month = round(tariff["price"] / tariff["months"])
        return f"{tariff['name']} — {tariff['price']} ₽ ({per_month} ₽/мес)"
    return f"{tariff['name']} — {tariff['price']} ₽"


def get_tariffs_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for t in TARIFFS:
        text = _format_tariff_text(t)
        cb = t["callback"]
        if t["months"] == 6:
            text += " 🔥"
            style = "success"
        elif t["months"] == 12:
            text += " 💎"
            style = "primary"
        else:
            style = None
        buttons.append([InlineKeyboardButton(text=text, callback_data=cb, style=style)])
    buttons.append([InlineKeyboardButton(text="← Главное меню", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ==================== ТЕКСТЫ И СТАТУСЫ ====================

def get_subscription_status(user: dict) -> tuple[str, str, str]:
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
    return (
        f"👋 Привет, <b>{name}</b>! Это <b>VPNчик 24</b>\n\n"
        f"😻 Первые 3 дня — БЕСПЛАТНО\n\n"
        f"👇 Жми кнопку ниже и подключайся"
    )


def get_paid_profile_text(user: dict) -> str:
    """Текст профиля для активной платной подписки (2 варианта)."""
    ends_at_str = user.get("subscription_ends_at")
    sub_id = user.get("xui_sub_id")
    
    # Получаем реальную ссылку из xui или плейсхолдер
    if sub_id:
        key_link = xui.build_subscription_url(sub_id)
    else:
        key_link = "Ключ ещё не создан — выберите устройство в разделе «Подключиться»"

    if not ends_at_str:
        return get_profile_text(user)

    try:
        ends_at = datetime.fromisoformat(ends_at_str)
        now = datetime.now()
        
        # Корректировка времени: +1 час для отображения МСК
        display_end_time = ends_at + timedelta(hours=1)
        
        delta = ends_at - now

        if delta.total_seconds() <= 0:
            return get_profile_text(user)

        # Форматируем дату окончания (с учетом +1 часа)
        # Для варианта > 3 дней: полная дата
        end_date_full = display_end_time.strftime("%d %B %Y, %H:%M (МСК)")
        # Для варианта <= 3 дней: короткая дата (день месяца и время)
        end_date_short = display_end_time.strftime("%d %B в %H:%M")
        
        months_ru = {
            "January": "января", "February": "февраля", "March": "марта", "April": "апреля",
            "May": "мая", "June": "июня", "July": "июля", "August": "августа",
            "September": "сентября", "October": "октября", "November": "ноября", "December": "декабря"
        }
        for eng, ru in months_ru.items():
            end_date_full = end_date_full.replace(eng, ru)
            end_date_short = end_date_short.replace(eng, ru)

        days_left = delta.days
        hours_left = delta.seconds // 3600
        
        # Форматирование времени для варианта "< 3 дней" (если нужно будет вернуть обратный отсчет)
        if days_left > 0:
            day_word = "день" if days_left == 1 else "дня" if days_left < 5 else "дней"
            hour_word = "час" if hours_left == 1 else "часа" if hours_left < 5 else "часов"
            time_left_text = f"{days_left} {day_word} {hours_left} {hour_word}"
        else:
            hour_word = "час" if hours_left == 1 else "часа" if hours_left < 5 else "часов"
            time_left_text = f"{hours_left} {hour_word}"

        if days_left > 3:
            # Вариант 1: Больше 3 дней
            text = (
                f"🟢 <b>VPN работает</b>\n\n"
                f"<blockquote><b>Активен до:</b>\n"
                f"<i>{end_date_full}</i></blockquote>\n\n"
                f"💎 Продлить доступ можно в любой момент\n\n"
                f"🔑 <b>Ваш VPN-ключ:</b>\n"
                f"<blockquote><code>{key_link}</code></blockquote>"
            )
        else:
            # Вариант 2: 3 дня и меньше (НОВЫЙ ТЕКСТ)
            text = (
                f"🟡 <b>VPN работает</b>\n\n"
                f"<blockquote><b>Подписка истекает через:</b>\n"
                f"<i>{time_left_text}</i></blockquote>\n\n"
                f"💎 Продлите заранее, чтобы не потерять доступ\n\n"
                f"🔑 <b>Ваш VPN-ключ:</b>\n"
                f"<blockquote><code>{key_link}</code></blockquote>"
            )
            
    except Exception as e:
        logging.error(f"Error formatting paid profile: {e}")
        return get_profile_text(user)

    return text

def get_profile_text(user: dict) -> str:
    code, _, _ = get_subscription_status(user)
    if code == "active":
        return get_paid_profile_text(user)

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

    status_map = {"new": "🎁 доступен", "trial_active": "🎁 триал активен", "expired": "❌ истёк"}
    status = status_map.get(code, "")

    return (
        f"👤 <b>Профиль:</b>\n<blockquote>ID: <code>{uid}</code>\nИмя: {name}</blockquote>\n\n"
        f"🎁 <b>Подписка:</b>\n<blockquote>Статус: {status}\n\n<i>{msg_short}</i></blockquote>"
    )


def get_trial_welcome_text(user: dict, key_link: str) -> str:
    ends_at_str = user.get("subscription_ends_at")
    remaining_text = "—"
    if ends_at_str:
        try:
            ends_at = datetime.fromisoformat(ends_at_str)
            now = datetime.now()
            if ends_at > now:
                delta = ends_at - now
                days = delta.days
                hours = delta.seconds // 3600
                if days > 0:
                    day_word = "день" if days == 1 else "дня" if days < 5 else "дней"
                    remaining_text = f"{days} {day_word} {hours} часа"
                else:
                    remaining_text = f"{hours} часа"
        except (ValueError, TypeError):
            pass

    return (
        f"🟢 <b>VPN работает</b>\n\n"
        f"<blockquote>До 5 устройств\n"
        f"<b>Осталось:</b> <i>{remaining_text}</i></blockquote>\n\n"
        f"💎 Продлить доступ можно в любой момент\n\n"
        f"🔑 <b>Ваш ключ доступа:</b>\n"
        f"<blockquote><code>{key_link}</code></blockquote>\n"
    )


def get_keyboard_for_user(user: dict) -> InlineKeyboardMarkup:
    code, _, _ = get_subscription_status(user)
    if code == "trial_active":
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
        await callback.message.answer("⚠️ Не удалось создать ссылку на оплату. Попробуйте позже или обратитесь в поддержку.")
        return

    transaction_id = payment.get("transactionId")
    pay_url = payment.get("redirect")
    if not transaction_id or not pay_url:
        logging.error(f"Platega вернула неожиданный ответ: {payment}")
        await callback.message.answer("⚠️ Ошибка при создании платежа. Попробуйте позже.")
        return

    await db.create_transaction(
        transaction_id=transaction_id, user_id=callback.from_user.id,
        tariff_callback=tariff_cb, months=tariff["months"], days=days, amount=tariff["price"],
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
        reply_markup=kb, parse_mode="HTML",
    )


# ==================== ПОДКЛЮЧЕНИЕ VPN ====================

@router.callback_query(F.data == "connect_vpn")
async def on_connect_vpn(callback: CallbackQuery):
    if callback.message.photo:
        await delete_old_menu(bot, callback.message.chat.id, callback.from_user.id)
        sent = await callback.message.answer(
            "<b>Выберите ваше устройство:</b>",
            reply_markup=get_device_keyboard(), parse_mode="HTML",
        )
        await db.save_menu_message_id(callback.from_user.id, sent.message_id)
    else:
        await callback.message.edit_text(
            "<b>Выберите ваше устройство:</b>",
            reply_markup=get_device_keyboard(), parse_mode="HTML",
        )
    await callback.answer()


# --- ANDROID ---
@router.callback_query(F.data == "connect_android")
async def on_connect_android(cb: CallbackQuery):
    user_data = await db.get_user(cb.from_user.id)
    if not user_data.get("trial_used", 0):
        await db.activate_trial(cb.from_user.id)

    key = await get_or_create_subscription_link(cb.from_user.id)

    text = (
        f"<b>Инструкция для Android</b>\n\n"
        f"1️⃣ Нажмите на ссылку, чтобы скопировать вашу подписку:\n"
        f"<blockquote><code>{key}</code></blockquote>\n\n"
        f"2️⃣ Установите приложение Happ из "
        f'<a href="https://play.google.com/store/apps/details?id=com.happproxy"><b>Google Play</b></a>'
        f" или "
        f'<a href="https://github.com/Happ-proxy/happ-android/releases/latest/download/Happ.apk"><b>скачайте APK</b></a>'
        f"\n\n"
        f"3️⃣ Откройте приложение, нажмите ➕ в верхнем правом углу и выберите \"Добавить из буфера\"\n\n"
        f"4️⃣ Включите VPN"
    )

    await cb.message.edit_text(text, reply_markup=_android_instruction_kb(), parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
    await cb.answer()


# --- iOS ---
@router.callback_query(F.data == "connect_ios")
async def on_connect_ios(cb: CallbackQuery):
    user_data = await db.get_user(cb.from_user.id)
    if not user_data.get("trial_used", 0):
        await db.activate_trial(cb.from_user.id)

    key = await get_or_create_subscription_link(cb.from_user.id)

    text = (
        f"<b>Инструкция для iPhone / iPad</b>\n\n"
        f"1️⃣ Нажмите на ссылку, чтобы скопировать вашу подписку:\n"
        f"<blockquote><code>{key}</code></blockquote>\n\n"
        f"2️⃣ Установите приложение <b>INCY</b> из "
        f'<a href="https://apps.apple.com/ru/app/incy/id6756943388"><b>App Store</b></a>'
        f"\n\n"
        f"3️⃣ Откройте приложение, нажмите ➕ в верхнем правом углу и выберите \"Добавить из буфера\"\n\n"
        f"4️⃣ Включите VPN"
    )

    await cb.message.edit_text(text, reply_markup=_ios_instruction_kb(), parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
    await cb.answer()


# --- WINDOWS ---
@router.callback_query(F.data == "connect_windows")
async def on_connect_windows(cb: CallbackQuery):
    user_data = await db.get_user(cb.from_user.id)
    if not user_data.get("trial_used", 0):
        await db.activate_trial(cb.from_user.id)

    key = await get_or_create_subscription_link(cb.from_user.id)

    text = (
        f"<b>Инструкция для Windows</b>\n\n"
        f"<b>1️⃣ Нажмите на ссылку, чтобы скопировать вашу подписку:</b>\n"
        f"<blockquote><code>{key}</code></blockquote>\n\n"
        f"<b>2️⃣ Скачайте и установите приложение Happ:</b>\n"
        f'<a href="https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe">Скачать Happ</a>\n\n'
        f"<b>3️⃣ Откройте приложение, нажмите ➕ в верхнем правом углу и выберите \"Добавить из буфера\".</b>\n\n"
        f"<b>4️⃣ Включите VPN</b>"
    )

    await cb.message.edit_text(text, reply_markup=_windows_instruction_kb(), parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
    await cb.answer()


# --- MACOS ---
@router.callback_query(F.data == "connect_macos")
async def on_connect_macos(cb: CallbackQuery):
    user_data = await db.get_user(cb.from_user.id)
    if not user_data.get("trial_used", 0):
        await db.activate_trial(cb.from_user.id)

    key = await get_or_create_subscription_link(cb.from_user.id)

    text = (
        f"<b>Инструкция для macOS</b>\n\n"
        f"<b>1️⃣ Нажмите на ссылку, чтобы скопировать вашу подписку:</b>\n"
        f"<blockquote><code>{key}</code></blockquote>\n\n"
        f"<b>2️⃣ Установите приложение INCY из App Store:</b>\n"
        f'<a href="https://apps.apple.com/ru/app/incy/id6756943388">Скачать INCY</a>\n\n'
        f"<b>3️⃣ Откройте приложение, нажмите ➕ в верхнем правом углу и выберите \"Добавить из буфера\".</b>\n\n"
        f"<b>4️⃣ Включите VPN</b>"
    )

    await cb.message.edit_text(text, reply_markup=_macos_instruction_kb(), parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
    await cb.answer()


# --- ОБЩИЙ ОБРАБОТЧИК «ГОТОВО» (для Android, iOS, Windows, macOS) ---
@router.callback_query(F.data == "setup_done")
async def on_setup_done(cb: CallbackQuery):
    await send_main_menu(bot, cb.message.chat.id, cb.from_user.id, is_activation=False)
    await cb.answer()


# --- ANDROID TV (СТАРАЯ ЛОГИКА) ---
@router.callback_query(F.data == "connect_android_tv")
async def on_connect_android_tv(cb: CallbackQuery):
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
    await message.answer(
        "🔒 <b>Политика конфиденциальности</b>\n\nОзнакомьтесь с документом по ссылке ниже:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📄 Читать политику", url="https://telegra.ph/Politika-konfidencialnosti-06-21-31")],
        ]), parse_mode="HTML",
    )


@router.message(Command("agreement"))
async def cmd_agreement(message: Message):
    await message.answer(
        "📜 <b>Пользовательское соглашение</b>\n\nОзнакомьтесь с документом по ссылке ниже:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📄 Читать соглашение", url="https://telegra.ph/Polzovatelskoe-soglashenie-04-01-19")],
        ]), parse_mode="HTML",
    )


# ==================== ТОЧКА ВХОДА ====================

async def main():
    await db.init()
    logging.info("База данных инициализирована")
    dp.include_router(router)
    dp.include_router(admin_router)
    await bot.delete_webhook(drop_pending_updates=True)
    await run_webhook_server(bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped")
