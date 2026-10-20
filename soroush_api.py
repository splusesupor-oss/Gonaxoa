"""Small async HTTPX client for the official Soroush Plus Bot HTTP API."""
from __future__ import annotations

from typing import Any

import httpx


class SoroushAPIError(Exception):
    """An API or network error safe to show without exposing the bot token."""


class SoroushAPI:
    def __init__(self, token: str, *, timeout: float = 20.0) -> None:
        self._base_url = f"https://api.splus.ir/bot{token}"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"User-Agent": "soroush-anonymous-inbox/1.0"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, **params: Any) -> Any:
        # getUpdates is a long-poll operation; allow its server-side wait plus network overhead.
        timeout = httpx.Timeout(40.0, connect=10.0) if method == "getUpdates" else httpx.Timeout(20.0, connect=10.0)
        try:
            response = await self._client.post(
                f"{self._base_url}/{method}", json=params, timeout=timeout
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Do not include exception text: HTTPX exceptions can contain the tokenized URL.
            raise SoroushAPIError(f"خطای ارتباط با API سروش‌پلاس در {method}") from exc

        if not isinstance(payload, dict):
            raise SoroushAPIError(f"پاسخ نامعتبر از API سروش‌پلاس در {method}")
        ok = payload.get("ok", payload.get("OK", False))
        if not ok:
            description = payload.get("description", "خطای نامشخص")
            raise SoroushAPIError(f"API در {method} پاسخ ناموفق داد: {description}")
        return payload.get("result")

    async def get_updates(self, offset: int, timeout: int = 25) -> list[dict[str, Any]]:
        result = await self.call(
            "getUpdates", offset=offset, timeout=timeout, limit=100,
            allowed_updates=["message"],
        )
        if not isinstance(result, list):
            raise SoroushAPIError("قالب پاسخ getUpdates نامعتبر است")
        return result

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> dict[str, Any]:
        result = await self.call("sendMessage", chat_id=chat_id, text=text, **kwargs)
        if not isinstance(result, dict):
            raise SoroushAPIError("پاسخ sendMessage نامعتبر است")
        return result

    async def copy_message(
        self, chat_id: int, from_chat_id: int, message_id: int
    ) -> dict[str, Any]:
        result = await self.call(
            "copyMessage", chat_id=chat_id, from_chat_id=from_chat_id,
            message_id=message_id,
        )
        # Official API normally returns MessageId; tolerate a full Message response too.
        if not isinstance(result, dict) or "message_id" not in result:
            raise SoroushAPIError("پاسخ copyMessage فاقد message_id است")
        return result
