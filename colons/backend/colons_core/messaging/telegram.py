"""
Telegram adapter - Bot API long polling (no public URL required).
"""
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from .base import BaseMessagingAdapter, IncomingMessage, markdown_to_telegram_html

logger = logging.getLogger(__name__)


class TelegramAdapter(BaseMessagingAdapter):
    name = "telegram"
    max_message_length = 4000
    supports_markdown = True
    supports_typing = True

    def __init__(self, config: Dict[str, Any], on_message):
        super().__init__(config, on_message)
        self.token = str(config.get("bot_token", "") or "").strip()
        base = str(config.get("api_base", "https://api.telegram.org")).rstrip("/")
        self.api_base = f"{base}/bot{self.token}" if self.token else base
        self.poll_timeout = int(config.get("poll_timeout", 30))
        self.offset: Optional[int] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._reply_typing = bool(config.get("typing_indicator", True))
        self.bot_username: str = ""
        # Persist the update offset so a restart never replays old messages
        state_dir = str(config.get("state_dir", "") or "")
        self._offset_path: Optional[Path] = (
            Path(state_dir) / "telegram_offset.json" if state_dir else None
        )

    def is_configured(self) -> bool:
        return bool(self.token)

    # ------------------------------------------------------------------ #
    # Offset persistence
    # ------------------------------------------------------------------ #

    def _load_offset(self):
        if not self._offset_path or not self._offset_path.exists():
            return
        try:
            data = json.loads(self._offset_path.read_text())
            if isinstance(data.get("offset"), int):
                self.offset = data["offset"]
                logger.info(f"Telegram resuming from update offset {self.offset}")
        except Exception as e:
            logger.warning(f"Could not read Telegram offset state: {e}")

    def _save_offset(self):
        if not self._offset_path:
            return
        try:
            self._offset_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._offset_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"offset": self.offset}))
            os.replace(tmp, self._offset_path)
        except Exception as e:
            logger.debug(f"Could not persist Telegram offset: {e}")

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self):
        if not self.is_configured():
            raise ValueError("telegram: bot_token is required")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.poll_timeout + 15, connect=15.0)
        )
        try:
            me = await self._get("getMe")
            self.bot_username = (me.get("result") or {}).get("username", "")
        except Exception as e:
            await self._client.aclose()
            self._client = None
            raise RuntimeError(f"telegram: getMe failed ({e}); check bot_token") from e

        self._load_offset()
        self.connected = True
        self.started_at = __import__("time").time()
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"Telegram connected as @{self.bot_username}")

    async def stop(self):
        await super().stop()
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    # Polling
    # ------------------------------------------------------------------ #

    async def _get(self, method: str, **params) -> Dict:
        assert self._client
        resp = await self._client.get(f"{self.api_base}/{method}", params=params)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method}: {data.get('description', resp.text[:200])}")
        return data

    async def _post(self, method: str, **payload) -> Dict:
        assert self._client
        resp = await self._client.post(f"{self.api_base}/{method}", json=payload)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method}: {data.get('description', resp.text[:200])}")
        return data

    async def _poll_loop(self):
        backoff = 1
        while not self._stopping:
            try:
                params: Dict[str, Any] = {"timeout": self.poll_timeout, "allowed_updates": '["message"]'}
                if self.offset is not None:
                    params["offset"] = self.offset
                data = await self._get("getUpdates", **params)
                backoff = 1
                handled = False
                for update in data.get("result", []):
                    self.offset = update.get("update_id", 0) + 1
                    handled = True
                    message = update.get("message")
                    if message:
                        asyncio.create_task(self._handle_update(message))
                if handled:
                    self._save_offset()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.last_error = str(e)
                logger.warning(f"Telegram poll error: {e} (retrying in {backoff}s)")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _handle_update(self, message: Dict):
        try:
            text = (message.get("text") or "").strip()
            chat = message.get("chat", {})
            sender = message.get("from", {})
            chat_id = str(chat.get("id", ""))
            user_id = str(sender.get("id", ""))
            if not text or not chat_id:
                return
            if not self.allowed(user_id, chat_id):
                logger.info(f"Telegram: ignoring message from non-allowed user {user_id}")
                return

            incoming = IncomingMessage(
                adapter=self.name,
                chat_id=chat_id,
                user_id=user_id,
                user_name=sender.get("username") or sender.get("first_name", ""),
                text=text,
                is_group=chat.get("type") in ("group", "supergroup"),
                raw=message,
            )
            await self.on_message(incoming)
        except Exception:
            logger.exception("Telegram message handling failed")

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #

    async def send_message(self, chat_id: str, text: str, **kwargs) -> bool:
        if not self._client:
            return False
        html_text = markdown_to_telegram_html(text)
        try:
            await self._post(
                "sendMessage",
                chat_id=chat_id,
                text=html_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return True
        except Exception as e:
            # Fall back to plain text if HTML parsing is rejected
            logger.warning(f"Telegram HTML send failed ({e}); retrying plain")
            try:
                await self._post("sendMessage", chat_id=chat_id, text=text)
                return True
            except Exception as e2:
                self.last_error = str(e2)
                return False

    async def send_typing(self, chat_id: str):
        if not self._client:
            return
        try:
            await self._post("sendChatAction", chat_id=chat_id, action="typing")
        except Exception:
            pass
