# payments.py
"""
Модуль для работы с платёжной системой Platega.
Документация: https://docs.platega.io/
"""

import logging
import uuid

import aiohttp

from config import PLATEGA_BASE_URL, PLATEGA_MERCHANT_ID, PLATEGA_API_KEY, PUBLIC_BASE_URL, PLATEGA_CALLBACK_PATH


def _headers() -> dict:
    return {
        "X-MerchantId": PLATEGA_MERCHANT_ID,
        "X-Secret": PLATEGA_API_KEY,
        "Content-Type": "application/json",
    }


async def create_payment(
    amount: float,
    description: str,
    user_id: int,
    username: str | None = None,
    payment_method: int = 2,
    currency: str = "RUB",
) -> dict:
    """
    Создаёт транзакцию в Platega и возвращает ответ API.

    Ожидаемые поля в ответе (если запрос успешен):
      - transactionId: уникальный ID транзакции в Platega
      - redirect: ссылка на страницу оплаты, которую нужно показать пользователю
      - status: обычно "PENDING"
      - expiresIn: сколько времени действует ссылка

    Поднимает aiohttp.ClientResponseError, если Platega вернула ошибку HTTP.
    """
    url = f"{PLATEGA_BASE_URL}/transaction/process"

    payload = {
        "paymentMethod": payment_method,
        "paymentDetails": {
            "amount": amount,
            "currency": currency,
        },
        "description": description,
        # Куда вернуть пользователя после успешной/неуспешной оплаты.
        # Если у вас есть бот — можно вести на t.me/<bot_username>,
        # чтобы пользователь просто вернулся в Telegram.
        "return": f"{PUBLIC_BASE_URL}/payment/success",
        "failedUrl": f"{PUBLIC_BASE_URL}/payment/failed",
        "payload": str(user_id),
        "metadata": {
            "userId": str(user_id),
            "userName": f"@{username}" if username else "unknown",
        },
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=_headers()) as resp:
            data = await resp.json()
            if resp.status >= 400:
                logging.error(f"Platega create_payment error {resp.status}: {data}")
                resp.raise_for_status()
            return data


async def get_transaction_status(transaction_id: str) -> dict:
    """
    Запрашивает текущий статус транзакции у Platega.
    Возвращает словарь с полем 'status' (PENDING / CONFIRMED / CANCELED / CHARGEBACKED) и др.
    """
    url = f"{PLATEGA_BASE_URL}/transaction/{transaction_id}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=_headers()) as resp:
            data = await resp.json()
            if resp.status >= 400:
                logging.error(f"Platega get_transaction_status error {resp.status}: {data}")
                resp.raise_for_status()
            return data
