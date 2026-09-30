"""
Messaging bridge: connects messaging adapters to agents.

Each conversation (adapter + chat_id) gets its own agent session, so
context carries over per chat, and each platform user maps to a user_id.
"""
import asyncio
import logging
from typing import Dict, List, Optional

from .base import BaseMessagingAdapter, IncomingMessage
from .discord import DiscordAdapter
from .slack import SlackAdapter
from .telegram import TelegramAdapter
from .webhook import WebhookAdapter

logger = logging.getLogger(__name__)

MESSAGING_SYSTEM_PROMPT = """You are responding over {platform}. Keep replies clear and
conversational; use short paragraphs. Markdown is fine where the platform supports it.
If a task will take a while, say what you're doing before you do it.
"""


class MessagingBridge:
    """Owns all adapters and routes messages through the agent manager."""

    def __init__(self, manager, config):
        self.manager = manager
        self.config = config
        self.adapters: Dict[str, BaseMessagingAdapter] = {}
        self._registered = False

    # ------------------------------------------------------------------ #
    # Setup
    # ------------------------------------------------------------------ #

    def build_adapters(self):
        cfg = self.config.messaging
        state_dir = getattr(self.config, "data_dir", "") or "."
        adapters: Dict[str, BaseMessagingAdapter] = {}

        if cfg.telegram and cfg.telegram.get("enabled"):
            telegram_config = {**cfg.telegram, "state_dir": cfg.telegram.get("state_dir", state_dir)}
            adapters["telegram"] = TelegramAdapter(telegram_config, self.handle_incoming)

        if cfg.discord and cfg.discord.get("enabled"):
            adapters["discord"] = DiscordAdapter(cfg.discord, self.handle_incoming)

        if cfg.slack and cfg.slack.get("enabled"):
            slack = SlackAdapter(cfg.slack, self.handle_incoming)
            adapters["slack"] = slack

        for name, wh_config in (cfg.webhooks or {}).items():
            if wh_config and wh_config.get("enabled"):
                adapters[f"webhook:{name}"] = WebhookAdapter(name, wh_config, self.handle_incoming)

        self.adapters = adapters
        return adapters

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self):
        self.build_adapters()
        if not self.adapters:
            logger.info("Messaging: no adapters enabled")
            return

        results = await asyncio.gather(
            *[self._start_adapter(a) for a in self.adapters.values()],
            return_exceptions=True,
        )
        for adapter, result in zip(self.adapters.values(), results, strict=False):
            if isinstance(result, Exception):
                settings = getattr(self.manager, 'messaging_settings', None)
                error = settings.redact(result) if settings else str(result)
                logger.error(f"Messaging adapter '{adapter.name}' failed to start: {error}")
                await adapter.stop()
                adapter.last_error = error

    async def _start_adapter(self, adapter: BaseMessagingAdapter):
        await adapter.start()
        # Post-start identity resolution for Slack
        resolver = getattr(adapter, "resolve_identity", None)
        if resolver:
            try:
                await resolver()
            except Exception:
                pass

    async def stop(self):
        await asyncio.gather(
            *[a.stop() for a in self.adapters.values()],
            return_exceptions=True,
        )

    # ------------------------------------------------------------------ #
    # Routing
    # ------------------------------------------------------------------ #

    async def handle_incoming(self, message: IncomingMessage):
        """Route an incoming platform message through the agent and reply."""
        adapter = self.adapters.get(message.adapter)
        if adapter is None:
            logger.warning(f"No adapter registered for '{message.adapter}'")
            return

        # Slash-style commands every adapter understands
        command = message.text.strip().lower()
        if command in ("/new", "/reset"):
            agent = self._agent_for(message)
            agent.delete_session(message.session_key)
            await adapter.send_message(message.chat_id, "Started a fresh conversation.")
            return
        if command in ("/help", "/start"):
            agent = self._agent_for(message)
            await adapter.send_message(
                message.chat_id,
                f"Hi! I'm {agent.name}, your Colons agent.\n\n"
                "Just send me a message. Commands:\n"
                "/new - start a fresh conversation\n"
                "/status - show agent and usage status\n"
                "/help - this message",
            )
            return
        if command == "/status":
            agent = self._agent_for(message)
            status = agent.status()
            usage = status["usage"]
            await adapter.send_message(
                message.chat_id,
                f"state: {status['state']}\n"
                f"model: {status['model']} ({status['provider']})\n"
                f"tools: {status['tools']}\n"
                f"memory: {status['memory_entries']} entries\n"
                f"tokens: {usage['total_tokens']:,}",
            )
            return

        agent = self._agent_for(message)
        await agent.start()

        # Typing indicator while we work (cancelled as soon as we reply)
        typing_task: Optional[asyncio.Task] = None
        if adapter.supports_typing:
            typing_task = asyncio.create_task(self._typing_loop(adapter, message.chat_id))

        logger.info(
            f"[{message.adapter}] {message.user_name or message.user_id}: "
            f"{message.text[:100]}"
        )

        response = ""
        try:
            async for event in agent.chat(
                message.text,
                session_id=message.session_key,
                stream=False,
            ):
                etype = event.get("type")
                if etype == "done":
                    response = event.get("content") or response
                elif etype == "error":
                    response = response or f"Sorry, something went wrong: {event.get('message')}"
        except Exception as e:
            logger.exception("Agent failed while handling a messaging message")
            response = f"Sorry, I hit an error: {e}"
        finally:
            if typing_task and not typing_task.done():
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass

        if not response:
            response = "(no response)"

        await adapter.send_chunked(message.chat_id, response)

    def _agent_for(self, message: IncomingMessage):
        """Map a platform user to a Colons user/agent."""
        prefix = self.config.messaging.user_prefix or "msg"
        user_id = f"{prefix}-{message.adapter}-{message.user_id}"
        return self.manager.get_or_create(user_id=user_id)

    async def _typing_loop(self, adapter: BaseMessagingAdapter, chat_id: str,
                           interval: float = 5.0, max_seconds: float = 300):
        """Repeatedly send typing indicators while the agent works."""
        import time
        deadline = time.time() + max_seconds
        try:
            while time.time() < deadline:
                await adapter.send_typing(chat_id)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Outbound (schedules, notifications)
    # ------------------------------------------------------------------ #

    async def notify(self, adapter_name: str, chat_id: str, text: str) -> bool:
        """Send a message to a specific adapter/chat (used by schedules)."""
        adapter = self.adapters.get(adapter_name)
        if adapter is None:
            logger.warning(f"notify: no adapter '{adapter_name}'")
            return False
        return bool(await adapter.send_chunked(chat_id, text))

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    def status(self) -> List[Dict]:
        return [a.status() for a in self.adapters.values()]

    def get(self, name: str) -> Optional[BaseMessagingAdapter]:
        return self.adapters.get(name)
