"""
Discord adapter - raw Gateway v10 implementation (no discord.py required).

Requires the MESSAGE CONTENT privileged intent to be enabled in the
Discord Developer Portal for the bot.
"""
import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

import httpx

from .base import BaseMessagingAdapter, IncomingMessage, strip_markdown

logger = logging.getLogger(__name__)

API_BASE = "https://discord.com/api/v10"

# Gateway opcodes
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# Intents: GUILDS | GUILD_MESSAGES | DIRECT_MESSAGES | MESSAGE_CONTENT
DEFAULT_INTENTS = (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)

FATAL_CLOSE_CODES = {4004, 4010, 4011, 4012, 4013, 4014}


class DiscordAdapter(BaseMessagingAdapter):
    name = "discord"
    max_message_length = 1900  # Discord limit is 2000
    supports_markdown = True
    supports_typing = True

    def __init__(self, config: Dict[str, Any], on_message):
        super().__init__(config, on_message)
        self.token = str(config.get("bot_token", "") or "").strip()
        self.intents = int(config.get("intents", DEFAULT_INTENTS))
        self.respond_to_all_guild_messages = bool(config.get("respond_to_all", False))
        self._client: Optional[httpx.AsyncClient] = None
        self._ws = None
        self.bot_user_id: str = ""
        self.bot_username: str = ""
        self._seq: Optional[int] = None
        self._session_id: Optional[str] = None
        self._resume_url: Optional[str] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._heartbeat_interval: float = 41.25

    def is_configured(self) -> bool:
        return bool(self.token)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self):
        if not self.is_configured():
            raise ValueError("discord: bot_token is required")
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=httpx.Timeout(60.0, connect=15.0),
            headers={"Authorization": f"Bot {self.token}",
                     "User-Agent": "Colons (https://github.com/colons, 0.1.0)"},
        )
        # Validate the token and learn our identity
        me = await self._rest("GET", "/users/@me")
        self.bot_user_id = me.get("id", "")
        self.bot_username = me.get("username", "")
        self.connected = True
        self.started_at = time.time()
        self._task = asyncio.create_task(self._run_gateway())
        logger.info(f"Discord connected as {self.bot_username}#{me.get('discriminator', '')}")

    async def stop(self):
        await super().stop()
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    # REST
    # ------------------------------------------------------------------ #

    async def _rest(self, method: str, path: str, **kwargs) -> Any:
        assert self._client
        resp = await self._client.request(method, path, **kwargs)
        if resp.status_code == 429:
            retry_after = 1.0
            try:
                retry_after = float(resp.json().get("retry_after", 1.0))
            except Exception:
                pass
            await asyncio.sleep(retry_after + 0.2)
            resp = await self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            raise RuntimeError(f"discord {method} {path}: {resp.status_code} {resp.text[:200]}")
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # ------------------------------------------------------------------ #
    # Gateway
    # ------------------------------------------------------------------ #

    async def _run_gateway(self):
        backoff = 2
        while not self._stopping:
            try:
                await self._connect_once()
                backoff = 2
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.last_error = str(e)
                logger.warning(f"Discord gateway error: {e} (reconnecting in {backoff}s)")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            self.connected = False

    async def _connect_once(self):
        import websockets

        # Get the gateway URL
        gateway = await self._rest("GET", "/gateway/bot")
        url = self._resume_url or gateway.get("url", "wss://gateway.discord.gg")
        ws_url = f"{url}/?v=10&encoding=json"

        async with websockets.connect(ws_url, max_size=8 * 1024 * 1024) as ws:
            self._ws = ws
            hello = json.loads(await ws.recv())
            if hello.get("op") != OP_HELLO:
                raise RuntimeError(f"unexpected first gateway message: {hello.get('op')}")
            self._heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000.0

            if self._session_id and self._seq is not None and self._resume_url:
                await ws.send(json.dumps({
                    "op": OP_RESUME,
                    "d": {"token": self.token, "session_id": self._session_id, "seq": self._seq},
                }))
            else:
                await ws.send(json.dumps({
                    "op": OP_IDENTIFY,
                    "d": {
                        "token": self.token,
                        "intents": self.intents,
                        "properties": {"os": "linux", "browser": "colons", "device": "colons"},
                    },
                }))

            self._heartbeat_task = asyncio.create_task(self._heartbeat(ws))

            async for raw in ws:
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle_gateway_event(event)

    async def _heartbeat(self, ws):
        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval)
                await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": self._seq}))
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _handle_gateway_event(self, event: Dict):
        op = event.get("op")
        if op == OP_DISPATCH:
            t = event.get("t")
            self._seq = event.get("s", self._seq)
            if t == "READY":
                data = event.get("d", {})
                self._session_id = data.get("session_id")
                self._resume_url = (data.get("resume_gateway_url") or "").rstrip("/") or None
                user = data.get("user", {})
                self.bot_user_id = user.get("id", self.bot_user_id)
                self.bot_username = user.get("username", self.bot_username)
                self.connected = True
                logger.info(f"Discord gateway READY ({self.bot_username})")
            elif t == "RESUMED":
                self.connected = True
                logger.info("Discord gateway RESUMED")
            elif t == "MESSAGE_CREATE":
                asyncio.create_task(self._handle_message_create(event.get("d", {})))
        elif op == OP_HEARTBEAT:
            await self._ws.send(json.dumps({"op": OP_HEARTBEAT, "d": self._seq}))
        elif op == OP_RECONNECT:
            raise RuntimeError("gateway requested reconnect")
        elif op == OP_INVALID_SESSION:
            logger.warning("Discord invalid session; re-identifying")
            self._session_id = None
            self._seq = None
            self._resume_url = None
            raise RuntimeError("invalid session")
        # OP_HEARTBEAT_ACK intentionally ignored

    async def _handle_message_create(self, msg: Dict):
        try:
            author = msg.get("author", {})
            if author.get("bot") or author.get("id") == self.bot_user_id:
                return

            content = msg.get("content", "") or ""
            channel_id = str(msg.get("channel_id", ""))
            guild_id = msg.get("guild_id")
            is_dm = guild_id is None

            # In guilds, only respond when mentioned (unless configured otherwise)
            if not is_dm and not self.respond_to_all_guild_messages:
                mention_a = f"<@{self.bot_user_id}>"
                mention_b = f"<@!{self.bot_user_id}>"
                if mention_a not in content and mention_b not in content:
                    return
                content = content.replace(mention_a, "").replace(mention_b, "").strip()

            if not content:
                return

            incoming = IncomingMessage(
                adapter=self.name,
                chat_id=channel_id,
                user_id=str(author.get("id", "")),
                user_name=author.get("global_name") or author.get("username", ""),
                text=content,
                is_group=not is_dm,
                raw=msg,
            )
            if not self.allowed(incoming.user_id, incoming.chat_id):
                return
            await self.on_message(incoming)
        except Exception:
            logger.exception("Discord message handling failed")

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #

    async def send_message(self, chat_id: str, text: str, **kwargs) -> bool:
        try:
            # Discord markdown is close enough to standard markdown
            await self._rest("POST", f"/channels/{chat_id}/messages",
                             json={"content": text[:2000]})
            return True
        except Exception:
            # Fallback to a fully stripped body on formatting errors
            try:
                await self._rest("POST", f"/channels/{chat_id}/messages",
                                 json={"content": strip_markdown(text)[:2000]})
                return True
            except Exception as e2:
                self.last_error = str(e2)
                logger.error(f"Discord send failed: {e2}")
                return False

    async def send_typing(self, chat_id: str):
        try:
            await self._rest("POST", f"/channels/{chat_id}/typing")
        except Exception:
            pass
