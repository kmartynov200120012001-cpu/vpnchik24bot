# xui_client.py
"""
Обёртка над API панели 3x-ui (v3.4.x — новый "Clients" API).

Авторизация в этой версии панели использует CSRF-синхронизатор:
  1. GET {base}/  — отдаёт HTML с <meta name="csrf-token" content="..."> и ставит cookie "3x-ui"
  2. POST {base}/login — username/password + заголовок X-CSRF-Token (из шага 1) + та же cookie
  3. Все последующие POST-запросы тоже требуют X-CSRF-Token из ШАГА 1 (GET-запросы — нет)

Эндпоинты (см. {base}/panel/api/openapi.json на самой панели):
  GET  /panel/api/inbounds/get/{id}        — детали inbound (Reality-параметры, список клиентов)
  POST /panel/api/clients/add              — создать клиента; {"client": {...}, "inboundIds": [id]}
  GET  /panel/api/clients/get/{email}       — клиент по email
  POST /panel/api/clients/update/{email}    — обновить клиента (полная замена строки)
  POST /panel/api/clients/del/{email}       — удалить клиента
  GET  /panel/api/clients/subLinks/{subId}  — JSON-массив готовых ссылок (vless://...) для subId
"""

import logging
import re
import secrets
import string

import aiohttp

from config import (
    XUI_BASE_URL,
    XUI_WEB_BASE_PATH,
    XUI_USERNAME,
    XUI_PASSWORD,
    XUI_INBOUND_ID,
    XUI_PUBLIC_HOST,
)

CSRF_TOKEN_RE = re.compile(r'<meta name="csrf-token" content="([^"]+)"')


def _panel_url(path: str) -> str:
    """Склеивает базовый URL панели + webBasePath + путь API."""
    base = XUI_BASE_URL.rstrip("/")
    web_base = XUI_WEB_BASE_PATH.strip("/")
    path = path.lstrip("/")
    if web_base:
        return f"{base}/{web_base}/{path}"
    return f"{base}/{path}"


def _gen_sub_id(length: int = 16) -> str:
    """Генерирует случайный subId для клиента."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class XUIClient:
    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._csrf_token: str | None = None
        self._logged_in = False

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # unsafe=True обязателен: по умолчанию aiohttp (следуя RFC 2109) не сохраняет
            # cookies для URL с IP-адресом вместо DNS-имени (например, http://127.0.0.1:1221).
            # Без этого панель логинит успешно, но cookie сессии теряется, и все
            # последующие запросы (включая повторный /login) проваливаются с 403.
            jar = aiohttp.CookieJar(unsafe=True)
            self._session = aiohttp.ClientSession(cookie_jar=jar)
        return self._session

    async def _fetch_csrf_token(self) -> str:
        """GET корень панели — получает cookie сессии и csrf-token из HTML."""
        session = await self._ensure_session()
        url = _panel_url("/")
        async with session.get(url) as resp:
            html = await resp.text()
            cookies_received = resp.cookies
            logging.info(
                f"3x-ui GET / response: status={resp.status}, "
                f"cookies={ {k: v.value for k, v in cookies_received.items()} }, "
                f"session_cookies={ {c.key: c.value for c in session.cookie_jar} }"
            )
        match = CSRF_TOKEN_RE.search(html)
        if not match:
            raise RuntimeError(f"3x-ui: не удалось найти csrf-token на странице логина. HTML[:300]={html[:300]!r}")
        token = match.group(1)
        logging.info(f"3x-ui: csrf-token получен: {token[:20]}...")
        return token

    async def login(self) -> None:
        self._csrf_token = await self._fetch_csrf_token()
        session = await self._ensure_session()
        url = _panel_url("/login")

        async with session.post(
            url,
            data={"username": XUI_USERNAME, "password": XUI_PASSWORD},
            headers={"X-CSRF-Token": self._csrf_token},
        ) as resp:
            raw_text = await resp.text()
            logging.info(
                f"3x-ui login response: status={resp.status}, "
                f"headers={dict(resp.headers)}, body={raw_text[:500]!r}"
            )
            try:
                import json as _json
                data = _json.loads(raw_text) if raw_text else None
            except Exception:
                data = None

            if not data or not data.get("success", False):
                raise RuntimeError(f"3x-ui login failed: status={resp.status}, body={raw_text[:300]!r}")
            self._logged_in = True
            logging.info("3x-ui: успешный логин в панель")

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """
        Делает запрос к API. Для POST добавляет X-CSRF-Token.
        При первой неудаче (протухшая сессия/токен) логинится заново и повторяет один раз.
        """
        if not self._logged_in:
            await self.login()

        session = await self._ensure_session()
        url = _panel_url(path)

        headers = kwargs.pop("headers", {}) or {}
        if method.upper() == "POST":
            headers["X-CSRF-Token"] = self._csrf_token

        async def _do_request():
            async with session.request(method, url, headers=headers, **kwargs) as resp:
                status = resp.status
                try:
                    body = await resp.json(content_type=None)
                except Exception:
                    body = None
                return status, body

        status, data = await _do_request()

        if data is None or status in (401, 403):
            # Сессия/csrf протухли — логинимся заново и повторяем запрос один раз
            self._logged_in = False
            await self.login()
            headers["X-CSRF-Token"] = self._csrf_token
            status, data = await _do_request()

        if data is None:
            raise RuntimeError(f"3x-ui: пустой/невалидный ответ от {path} (status={status})")

        return data

    async def get_inbound(self, inbound_id: int = XUI_INBOUND_ID) -> dict:
        """Возвращает детали inbound, включая распарсенные settings и streamSettings."""
        import json as _json

        data = await self._request("GET", f"/panel/api/inbounds/get/{inbound_id}")
        if not data.get("success", False):
            raise RuntimeError(f"3x-ui get_inbound failed: {data}")

        obj = data["obj"]
        if isinstance(obj.get("settings"), str):
            obj["settings_parsed"] = _json.loads(obj["settings"])
        else:
            obj["settings_parsed"] = obj.get("settings", {})
        if isinstance(obj.get("streamSettings"), str):
            obj["streamSettings_parsed"] = _json.loads(obj["streamSettings"])
        else:
            obj["streamSettings_parsed"] = obj.get("streamSettings", {})
        return obj

    async def add_client(
        self,
        user_id: int,
        days: int,
        inbound_id: int = XUI_INBOUND_ID,
        flow: str = "xtls-rprx-vision",
    ) -> dict:
        """
        Создаёт нового клиента через новый Clients API.
        UUID генерируется панелью автоматически (не передаём "id").
        flow="xtls-rprx-vision" обязателен для VLESS+Reality на TCP — без него
        клиент проходит TLS-handshake, но реальный трафик не проксируется.
        Возвращает {"sub_id": ..., "email": ...}.
        days<=0 означает "без ограничения по времени" (expiryTime=0).
        """
        sub_id = _gen_sub_id()
        email = f"tg{user_id}_{sub_id[:6]}"

        expiry_time_ms = 0
        if days > 0:
            import time
            expiry_time_ms = int((time.time() + days * 86400) * 1000)

        payload = {
            "client": {
                "email": email,
                "subId": sub_id,
                "flow": flow,
                "totalGB": 0,
                "expiryTime": expiry_time_ms,
                "tgId": user_id,
                "limitIp": 0,
                "enable": True,
            },
            "inboundIds": [inbound_id],
        }

        data = await self._request("POST", "/panel/api/clients/add", json=payload)
        if not data.get("success", False):
            raise RuntimeError(f"3x-ui add_client failed: {data}")

        logging.info(f"3x-ui: создан клиент {email} (subId={sub_id}, flow={flow}) на {days} дн.")
        return {"sub_id": sub_id, "email": email}

    async def get_client(self, email: str) -> dict | None:
        """Возвращает клиента по email, либо None, если не найден."""
        data = await self._request("GET", f"/panel/api/clients/get/{email}")
        if not data.get("success", False):
            return None
        return data.get("obj")

    async def update_client_expiry(self, email: str, days: int, extend: bool = True) -> None:
        """
        Продлевает существующего клиента (по email).
        extend=True — добавляет days к текущему expiryTime (или к "сейчас", если клиент
        истёк/был безлимитным) — так повторная покупка не обрезает уже оплаченные дни.
        days<=0 — делает клиента безлимитным (expiryTime=0).
        """
        current = await self.get_client(email)
        if current is None:
            raise RuntimeError(f"3x-ui: клиент с email {email} не найден")

        import time
        now_ms = int(time.time() * 1000)

        if days <= 0:
            expiry_time_ms = 0
        elif extend:
            current_expiry = current.get("expiryTime", 0) or 0
            base_ms = current_expiry if current_expiry > now_ms else now_ms
            expiry_time_ms = base_ms + days * 86400 * 1000
        else:
            expiry_time_ms = now_ms + days * 86400 * 1000

        current["expiryTime"] = expiry_time_ms
        current["enable"] = True

        data = await self._request("POST", f"/panel/api/clients/update/{email}", json=current)
        if not data.get("success", False):
            raise RuntimeError(f"3x-ui update_client_expiry failed: {data}")

        logging.info(f"3x-ui: клиент {email} продлён на {days} дн. (новый expiryTime={expiry_time_ms})")

    async def set_client_flow(self, email: str, flow: str = "xtls-rprx-vision") -> None:
        """
        Принудительно выставляет flow существующему клиенту, не трогая остальные поля.
        Нужен для разовой миграции клиентов, созданных до того, как flow стало
        обязательным полем в add_client (см. историю/коммит, где это было исправлено).
        """
        current = await self.get_client(email)
        if current is None:
            raise RuntimeError(f"3x-ui: клиент с email {email} не найден")
        current["flow"] = flow
        data = await self._request("POST", f"/panel/api/clients/update/{email}", json=current)
        if not data.get("success", False):
            raise RuntimeError(f"3x-ui set_client_flow failed: {data}")
        logging.info(f"3x-ui: клиенту {email} установлен flow={flow}")

    async def delete_client(self, email: str) -> None:
        """Удаляет клиента — например, при полном сбросе пользователя в админке."""
        data = await self._request("POST", f"/panel/api/clients/del/{email}")
        if not data.get("success", False):
            logging.warning(f"3x-ui delete_client: {data}")
        else:
            logging.info(f"3x-ui: клиент {email} удалён")

    async def get_sub_links(self, sub_id: str) -> list[str]:
        """Возвращает список готовых протокольных ссылок (vless://...) для данного subId."""
        data = await self._request("GET", f"/panel/api/clients/subLinks/{sub_id}")
        if not data.get("success", False):
            raise RuntimeError(f"3x-ui get_sub_links failed: {data}")
        return data.get("obj") or []

    def build_subscription_url(self, sub_id: str) -> str:
        """
        Subscription URL для Happ через "Добавить подписку".
        Настроено в панели: Panel Settings -> Subscription:
          URI Path: /sub/, Listen Port: 2096 (проксируется nginx-ом по /sub/ -> 127.0.0.1:2096),
          Reverse Proxy URI: https://<домен>/sub/
        """
        return f"https://{XUI_PUBLIC_HOST}/sub/{sub_id}"

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


xui = XUIClient()
