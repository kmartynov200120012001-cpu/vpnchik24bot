# xui_client.py
"""
Обёртка над API панели 3x-ui: логин, создание/продление клиента в inbound,
получение subscription URL и сборка vless-ссылки.

Документация по эндпоинтам (неофициальная, собрана из исходников и Postman-коллекций):
  POST {base}/login                                  — авторизация, ставит cookie "session"
  GET  {base}/panel/api/inbounds/get/{id}             — детали inbound (включая Reality-параметры)
  POST {base}/panel/api/inbounds/addClient            — добавить клиента
  POST {base}/panel/api/inbounds/{id}/delClient/{uuid}— удалить клиента
  POST {base}/panel/api/inbounds/updateClient/{uuid}  — обновить клиента (например, продлить срок)
"""

import json
import logging
import secrets
import string
import uuid as uuid_lib

import aiohttp

from config import (
    XUI_BASE_URL,
    XUI_WEB_BASE_PATH,
    XUI_USERNAME,
    XUI_PASSWORD,
    XUI_INBOUND_ID,
    XUI_PUBLIC_HOST,
    XUI_PUBLIC_PORT,
)


def _panel_url(path: str) -> str:
    """Склеивает базовый URL панели + webBasePath + путь API."""
    base = XUI_BASE_URL.rstrip("/")
    web_base = XUI_WEB_BASE_PATH.strip("/")
    path = path.lstrip("/")
    if web_base:
        return f"{base}/{web_base}/{path}"
    return f"{base}/{path}"


def _gen_sub_id(length: int = 16) -> str:
    """Генерирует случайный subId — панель сама его не создаёт при добавлении через API."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class XUIClient:
    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._logged_in = False

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # cookie_jar хранит сессию между запросами — именно так передаётся "session" cookie
            self._session = aiohttp.ClientSession()
        return self._session

    async def login(self) -> None:
        session = await self._ensure_session()
        url = _panel_url("/login")
        async with session.post(
            url,
            data={"username": XUI_USERNAME, "password": XUI_PASSWORD},
        ) as resp:
            data = await resp.json(content_type=None)
            if not data.get("success", False):
                raise RuntimeError(f"3x-ui login failed: {data}")
            self._logged_in = True
            logging.info("3x-ui: успешный логин в панель")

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Делает запрос к API; при первой неудаче (например, протухшая сессия) логинится и повторяет один раз."""
        if not self._logged_in:
            await self.login()

        session = await self._ensure_session()
        url = _panel_url(path)

        async with session.request(method, url, **kwargs) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception:
                text = await resp.text()
                raise RuntimeError(f"3x-ui: не удалось распарсить ответ ({resp.status}): {text[:300]}")

        if not data.get("success", False) and resp.status in (401, 403):
            # Сессия протухла — логинимся заново и повторяем запрос один раз
            self._logged_in = False
            await self.login()
            session = await self._ensure_session()
            async with session.request(method, url, **kwargs) as resp:
                data = await resp.json(content_type=None)

        return data

    async def get_inbound(self, inbound_id: int = XUI_INBOUND_ID) -> dict:
        """Возвращает детали inbound, включая распарсенные settings и streamSettings."""
        data = await self._request("GET", f"/panel/api/inbounds/get/{inbound_id}")
        if not data.get("success", False):
            raise RuntimeError(f"3x-ui get_inbound failed: {data}")

        obj = data["obj"]
        # settings и streamSettings приходят как JSON-строки — распарсиваем для удобства
        obj["settings_parsed"] = json.loads(obj["settings"])
        obj["streamSettings_parsed"] = json.loads(obj["streamSettings"])
        return obj

    async def add_client(
        self,
        user_id: int,
        days: int,
        inbound_id: int = XUI_INBOUND_ID,
        flow: str = "xtls-rprx-vision",
    ) -> dict:
        """
        Создаёт нового VLESS-клиента в указанном inbound.
        Возвращает {"client_uuid": ..., "sub_id": ..., "email": ...}.
        days=0 означает "без ограничения по времени" (expiryTime=0).
        """
        client_uuid = str(uuid_lib.uuid4())
        sub_id = _gen_sub_id()
        email = f"tg{user_id}_{sub_id[:6]}"

        expiry_time_ms = 0
        if days > 0:
            import time
            expiry_time_ms = int((time.time() + days * 86400) * 1000)

        client_settings = {
            "clients": [
                {
                    "id": client_uuid,
                    "flow": flow,
                    "email": email,
                    "limitIp": 0,
                    "totalGB": 0,
                    "expiryTime": expiry_time_ms,
                    "enable": True,
                    "tgId": str(user_id),
                    "subId": sub_id,
                    "reset": 0,
                }
            ]
        }

        payload = {
            "id": inbound_id,
            "settings": json.dumps(client_settings),
        }

        data = await self._request(
            "POST",
            "/panel/api/inbounds/addClient",
            json=payload,
        )

        if not data.get("success", False):
            raise RuntimeError(f"3x-ui add_client failed: {data}")

        logging.info(f"3x-ui: создан клиент {email} (uuid={client_uuid}) на {days} дн.")
        return {"client_uuid": client_uuid, "sub_id": sub_id, "email": email}

    async def update_client_expiry(
        self, client_uuid: str, days: int, inbound_id: int = XUI_INBOUND_ID, extend: bool = True
    ) -> None:
        """
        Продлевает существующего клиента.
        Если extend=True (по умолчанию) — добавляет days к текущему expiryTime клиента
        (или к "сейчас", если клиент уже истёк/безлимитный). Это нужно, чтобы повторная
        покупка не обрезала уже оплаченные, но ещё не использованные дни.
        Если extend=False — жёстко устанавливает expiryTime = now + days (используется
        редко, например при ручном сбросе администратором).
        days=0 означает "сделать безлимитным" (expiryTime=0).
        """
        inbound = await self.get_inbound(inbound_id)
        clients = inbound["settings_parsed"]["clients"]
        target = next((c for c in clients if c["id"] == client_uuid), None)
        if target is None:
            raise RuntimeError(f"3x-ui: клиент {client_uuid} не найден в inbound {inbound_id}")

        import time
        now_ms = int(time.time() * 1000)

        if days <= 0:
            expiry_time_ms = 0
        elif extend:
            current_expiry = target.get("expiryTime", 0) or 0
            # Если клиент уже истёк (current_expiry в прошлом) или был безлимитным (0),
            # отталкиваемся от "сейчас", а не от прошлой/нулевой даты.
            base_ms = current_expiry if current_expiry > now_ms else now_ms
            expiry_time_ms = base_ms + days * 86400 * 1000
        else:
            expiry_time_ms = now_ms + days * 86400 * 1000

        target["expiryTime"] = expiry_time_ms
        target["enable"] = True

        payload = {
            "id": inbound_id,
            "settings": json.dumps({"clients": [target]}),
        }

        data = await self._request(
            "POST",
            f"/panel/api/inbounds/updateClient/{client_uuid}",
            json=payload,
        )
        if not data.get("success", False):
            raise RuntimeError(f"3x-ui update_client_expiry failed: {data}")

        logging.info(f"3x-ui: клиент {client_uuid} продлён на {days} дн. (новый expiryTime={expiry_time_ms})")

    async def build_vless_link(self, sub_id: str, client_uuid: str, remark: str = "VPNchik24") -> str:
        """
        Собирает прямую vless://-ссылку на основе параметров inbound (Reality).
        Используется как запасной вариант; основной способ для пользователя — subscription URL.
        """
        inbound = await self.get_inbound()
        stream = inbound["streamSettings_parsed"]
        reality = stream.get("realitySettings", {})
        reality_settings = reality.get("settings", {})

        public_key = reality_settings.get("publicKey", "")
        short_ids = reality.get("shortIds", [""])
        short_id = short_ids[0] if short_ids else ""
        sni = reality.get("serverNames", [""])[0] if reality.get("serverNames") else ""
        fingerprint = reality_settings.get("fingerprint", "chrome")

        params = (
            f"type=tcp&security=reality&pbk={public_key}&fp={fingerprint}"
            f"&sni={sni}&sid={short_id}&spx=%2F&flow=xtls-rprx-vision"
        )
        return f"vless://{client_uuid}@{XUI_PUBLIC_HOST}:{XUI_PUBLIC_PORT}?{params}#{remark}"

    def build_subscription_url(self, sub_id: str) -> str:
        """
        Subscription URL, который пользователь добавляет в Happ через "Добавить подписку".
        Настроено в панели: Panel Settings -> Subscription:
          - Subscription Service: включено
          - URI Path: /sub/
          - Listen Port: 2096 (внутренний, проксируется nginx-ом по /sub/ на 127.0.0.1:2096)
          - Reverse Proxy URI: https://<домен>/sub/
        """
        host = XUI_PUBLIC_HOST
        return f"https://{host}/sub/{sub_id}"

    async def delete_client(self, client_uuid: str, inbound_id: int = XUI_INBOUND_ID) -> None:
        """Удаляет клиента из inbound — например, при полном сбросе пользователя в админке."""
        data = await self._request(
            "POST",
            f"/panel/api/inbounds/{inbound_id}/delClient/{client_uuid}",
        )
        if not data.get("success", False):
            # Не критично, если клиента уже нет (например, удалили руками в панели) — просто логируем
            logging.warning(f"3x-ui delete_client: {data}")
        else:
            logging.info(f"3x-ui: клиент {client_uuid} удалён")

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


xui = XUIClient()
