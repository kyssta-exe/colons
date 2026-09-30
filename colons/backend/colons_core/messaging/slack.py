"""
Slack adapter.
- Outgoing: incoming webhook URL (simple) or bot token (chat.postMessage)
- Incoming: Events API payloads delivered to the API layer, which verifies the
  signing secret and calls `handle_event`.
"""
import hashlib
import hmac
import logging
import time
from typing import Any, Dict, Optional

import httpx

from .base import BaseMessagingAdapter, IncomingMessage, strip_markdown

logger = logging.getLogger(__name__)


class SlackAdapter(BaseMessagingAdapter):
    name = "slack"
    max_message_length = 3800
    supports_markdown = True
    supports_typing = False

    def __init__(self, config: Dict[str, Any], on_message):
        super().__init__(config, on_message)
        self.webhook_url = str(config.get("webhook_url", "") or "").strip()
        self.bot_token = str(config.get("bot_token", "") or "").strip()
        self.signing_secret = str(config.get("signing_secret", "") or "").strip()
        self.default_channel = str(config.get("default_channel", "") or "").strip()
        self._client: Optional[httpx.AsyncClient] = None

    def is_configured(self) -> bool:
        return bool(self.webhook_url or self.bot_token)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self):
        if not self.is_configured():
            raise ValueError("slack: webhook_url or bot_token is required")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
        if self.bot_token:
            try:
                await self._post_api("auth.test", {})
            except Exception as e:
                raise RuntimeError(f"slack: auth.test failed ({e}); check bot_token") from e
        self.connected = True
        self.started_at = time.time()
        logger.info("Slack adapter ready (Events API: POST /api/messaging/slack/events)")

    async def stop(self):
        self.connected = False
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    # Incoming (called by the API layer)
    # ------------------------------------------------------------------ #

    def verify_signature(self, body: bytes, timestamp: str, signature: str) -> bool:
        if not self.signing_secret:
            return True  # signature verification disabled
        try:
            if abs(time.time() - int(timestamp)) > 300:
                return False
        except (TypeError, ValueError):
            return False
        base = f"v0:{timestamp}:{body.decode('utf-8')}".encode()
        expected = "v0=" + hmac.new(self.signing_secret.encode(), base, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    async def handle_event(self, payload: Dict) -> Optional[str]:
        """
        Handle a Slack Events API payload.
        Returns a challenge response if this is a url_verification request.
        """
        if payload.get("type") == "url_verification":
            return payload.get("challenge", "")

        if payload.get("type") != "event_callback":
            return None

        event = payload.get("event", {})
        if event.get("type") != "message":
            return None
        if event.get("bot_id") or event.get("subtype"):  # ignore bots & edits
            return None

        text = (event.get("text") or "").strip()
        channel = str(event.get("channel", ""))
        user = str(event.get("user", ""))
        if not text or not channel:
            return None

        # Strip a leading bot mention (<@U12345>)
        if self.bot_user_id:
            text = text.replace(f"<@{self.bot_user_id}>", "").strip()

        incoming = IncomingMessage(
            adapter=self.name,
            chat_id=channel,
            user_id=user,
            user_name=user,
            text=text,
            is_group=event.get("channel_type") != "im",
            raw=event,
        )
        if not self.allowed(user, channel):
            return None
        await self.on_message(incoming)
        return None

    bot_user_id: str = ""

    async def resolve_identity(self):
        if self.bot_token:
            try:
                data = await self._post_api("auth.test", {})
                self.bot_user_id = data.get("user_id", "")
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #

    async def _post_api(self, method: str, payload: Dict) -> Dict:
        assert self._client
        resp = await self._client.post(
            f"https://slack.com/api/{method}",
            headers={"Authorization": f"Bearer {self.bot_token}"},
            json=payload,
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"slack {method}: {data.get('error', resp.text[:200])}")
        return data

    async def send_message(self, chat_id: str, text: str, **kwargs) -> bool:
        channel = chat_id or self.default_channel
        try:
            if self.webhook_url:
                assert self._client
                resp = await self._client.post(self.webhook_url, json={"text": text})
                if resp.status_code < 400:
                    return True
                raise RuntimeError(f"webhook {resp.status_code}: {resp.text[:150]}")
            if self.bot_token:
                await self._post_api("chat.postMessage", {
                    "channel": channel,
                    "text": text,
                    "mrkdwn": True,
                })
                return True
        except Exception as e:
            # Retry plainly
            try:
                if self.bot_token:
                    await self._post_api("chat.postMessage",
                                         {"channel": channel, "text": strip_markdown(text)})
                    return True
            except Exception:
                pass
            self.last_error = str(e)
            logger.error(f"Slack send failed: {e}")
            return False
        return False

    async def send_typing(self, chat_id: str):
        return  # Slack bots can't show typing indicators
