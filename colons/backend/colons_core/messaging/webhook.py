"""
Generic webhook adapter.
- Outgoing: HTTP POST JSON to a configured URL
- Incoming: the API layer's `/api/messaging/webhook/{name}` endpoint feeds
  `handle_event`. Multiple named webhooks can be configured.
"""
import logging
import time
from typing import Any, Dict, Optional

import httpx

from .base import BaseMessagingAdapter, IncomingMessage

logger = logging.getLogger(__name__)


class WebhookAdapter(BaseMessagingAdapter):
    name = "webhook"
    max_message_length = 100_000
    supports_markdown = False
    supports_typing = False

    def __init__(self, name: str, config: Dict[str, Any], on_message):
        super().__init__(config, on_message)
        self.name = f"webhook:{name}"
        self.slug = name
        self.outgoing_url = str(config.get("outgoing_url", "") or "").strip()
        self.incoming_token = str(config.get("incoming_token", "") or "").strip()
        self._client: Optional[httpx.AsyncClient] = None

    def is_configured(self) -> bool:
        return bool(self.outgoing_url or self.incoming_token)

    async def start(self):
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
        self.connected = True
        self.started_at = time.time()
        logger.info(f"Webhook adapter '{self.slug}' ready "
                    f"(incoming: POST /api/messaging/webhook/{self.slug})")

    async def stop(self):
        self.connected = False
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    # Incoming (called by the API layer)
    # ------------------------------------------------------------------ #

    async def handle_event(self, payload: Dict, token: Optional[str] = None) -> bool:
        if self.incoming_token and token != self.incoming_token:
            raise PermissionError("invalid webhook token")

        text = str(payload.get("text") or payload.get("message") or "").strip()
        if not text:
            return False

        incoming = IncomingMessage(
            adapter=self.name,
            chat_id=str(payload.get("chat_id") or payload.get("channel") or "default"),
            user_id=str(payload.get("user_id") or payload.get("user") or "webhook"),
            user_name=str(payload.get("user_name") or "webhook"),
            text=text,
            raw=payload,
        )
        if not self.allowed(incoming.user_id, incoming.chat_id):
            return False
        await self.on_message(incoming)
        return True

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #

    async def send_message(self, chat_id: str, text: str, **kwargs) -> bool:
        if not self.outgoing_url or not self._client:
            return False
        try:
            resp = await self._client.post(self.outgoing_url, json={
                "text": text,
                "chat_id": chat_id,
                **{k: v for k, v in kwargs.items() if k in ("schedule_id", "title")},
            })
            return resp.status_code < 400
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Webhook send failed: {e}")
            return False
