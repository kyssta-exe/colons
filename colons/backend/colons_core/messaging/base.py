"""
Messaging adapter primitives: shared model, chunking, markdown conversion.
"""
import abc
import asyncio
import html
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

OnMessage = Callable[["IncomingMessage"], Awaitable[None]]


@dataclass
class IncomingMessage:
    adapter: str
    chat_id: str
    user_id: str
    user_name: str = ""
    text: str = ""
    is_group: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @property
    def session_key(self) -> str:
        """Stable session identifier for this conversation."""
        return f"msg-{self.adapter}-{self.chat_id}"


class BaseMessagingAdapter(abc.ABC):
    """Base class for messaging integrations."""

    name: str = "base"
    max_message_length: int = 4000
    supports_markdown: bool = False
    supports_typing: bool = False

    def __init__(self, config: Dict[str, Any], on_message: OnMessage):
        self.config = config or {}
        self.on_message = on_message
        self.connected = False
        self.last_error: Optional[str] = None
        self.started_at: Optional[float] = None
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled"))

    @abc.abstractmethod
    async def start(self):
        """Connect and begin receiving messages."""

    async def stop(self):
        self._stopping = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.connected = False

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #

    @abc.abstractmethod
    async def send_message(self, chat_id: str, text: str, **kwargs) -> bool:
        """Send a message, chunking as needed."""

    async def send_typing(self, chat_id: str):
        """Optional typing indicator. Default: no-op."""
        return

    async def send_chunked(self, chat_id: str, text: str, **kwargs) -> int:
        """Split long text and send each chunk. Returns chunk count."""
        chunks = chunk_text(text, self.max_message_length)
        sent = 0
        for chunk in chunks:
            try:
                if await self.send_message(chat_id, chunk, **kwargs):
                    sent += 1
            except Exception as e:
                logger.error(f"[{self.name}] send failed: {e}")
                self.last_error = str(e)
            if len(chunks) > 1:
                await asyncio.sleep(0.4)  # be gentle with rate limits
        return sent

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #

    def status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "configured": self.is_configured(),
            "connected": self.connected,
            "last_error": self.last_error,
            "uptime_seconds": round(time.time() - self.started_at, 1) if self.started_at else 0,
        }

    def is_configured(self) -> bool:
        return True

    def allowed(self, user_id: str, chat_id: str) -> bool:
        """Check allowlists if configured."""
        users = [str(u) for u in self.config.get("allowed_users", [])]
        chats = [str(c) for c in self.config.get("allowed_chats", [])]
        if users and str(user_id) not in users:
            return False
        if chats and str(chat_id) not in chats:
            return False
        return True


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def chunk_text(text: str, limit: int) -> List[str]:
    """Split text into chunks <= limit, preferring paragraph/line/word breaks."""
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        window = remaining[:limit]
        split_at = window.rfind("\n\n")
        if split_at < limit // 3:
            split_at = window.rfind("\n")
        if split_at < limit // 3:
            split_at = window.rfind(" ")
        if split_at < limit // 3:
            split_at = limit

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    return chunks


_MD_CODE_BLOCK = re.compile(r"```(\w*)\n([\s\S]*?)```", re.M)
_MD_INLINE_CODE = re.compile(r"`([^`]+)`")
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.M)


def markdown_to_telegram_html(text: str) -> str:
    """Convert common markdown to Telegram-safe HTML."""
    placeholders: List[str] = []

    def _stash(payload: str) -> str:
        placeholders.append(payload)
        return f"\x00{len(placeholders) - 1}\x00"

    # Fenced code blocks first
    def _code_block(m: re.Match) -> str:
        lang = m.group(1) or ""
        code = html.escape(m.group(2))
        cls = f' class="language-{html.escape(lang)}"' if lang else ""
        return _stash(f"<pre><code{cls}>{code}</code></pre>")

    text = _MD_CODE_BLOCK.sub(_code_block, text)

    # Escape everything else
    text = html.escape(text, quote=False)

    # Inline transforms on escaped text
    text = _MD_INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    text = _MD_BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _MD_ITALIC.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    text = _MD_LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)
    text = _MD_HEADING.sub(lambda m: f"<b>{m.group(1)}</b>", text)

    # Restore stashed blocks
    def _restore(m: re.Match) -> str:
        return placeholders[int(m.group(1))]

    text = re.sub(r"\x00(\d+)\x00", _restore, text)

    # Telegram disallows raw & < > outside our tags; they're already escaped
    return text


def strip_markdown(text: str) -> str:
    """Remove markdown formatting (for plain-text platforms)."""
    text = _MD_CODE_BLOCK.sub(lambda m: m.group(2), text)
    text = _MD_INLINE_CODE.sub(lambda m: m.group(1), text)
    text = _MD_BOLD.sub(lambda m: m.group(1), text)
    text = _MD_ITALIC.sub(lambda m: m.group(1), text)
    text = _MD_LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text)
    text = _MD_HEADING.sub(lambda m: m.group(1), text)
    return text
