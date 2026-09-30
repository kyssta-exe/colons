"""
Colons messaging subsystem: adapters + bridge.
"""
from .base import (
    BaseMessagingAdapter,
    IncomingMessage,
    chunk_text,
    markdown_to_telegram_html,
    strip_markdown,
)
from .bridge import MessagingBridge
from .discord import DiscordAdapter
from .slack import SlackAdapter
from .telegram import TelegramAdapter
from .webhook import WebhookAdapter

__all__ = [
    "BaseMessagingAdapter",
    "IncomingMessage",
    "chunk_text",
    "markdown_to_telegram_html",
    "strip_markdown",
    "TelegramAdapter",
    "DiscordAdapter",
    "SlackAdapter",
    "WebhookAdapter",
    "MessagingBridge",
]
