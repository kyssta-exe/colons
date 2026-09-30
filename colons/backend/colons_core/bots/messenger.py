"""
Bot-to-bot messaging: handle resolution, attributed DMs, silence tokens,
and cross-instance delivery over peers.
"""
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx

from .store import Bot, BotStore

logger = logging.getLogger(__name__)

# Turns that consist only of one of these mean "nothing to add".
SILENCE_TOKENS = {
    "[silent]", "no_reply", "no-reply", "[no-reply]", "no reply",
    "<silent>", "[silence]", "silence",
}

# (sender_bot_id, target_handle, message) -> result dict
LocalDelivery = Callable[[str, str, str], Awaitable[Dict[str, Any]]]


def is_silence(text: Optional[str]) -> bool:
    """True when the reply is intentionally empty."""
    if not text:
        return True
    cleaned = text.strip().strip("*_`").strip().lower()
    if cleaned in SILENCE_TOKENS:
        return True
    # Last non-empty line may carry the token after brief prose
    lines = [ln.strip().strip("*_`").strip().lower() for ln in text.strip().splitlines() if ln.strip()]
    return bool(lines) and lines[-1] in SILENCE_TOKENS and len(lines) == 1


class PeerError(Exception):
    """Peer delivery failed (carries a typed reason)."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class BotMessenger:
    """Resolves teammates and delivers bot-to-bot messages."""

    def __init__(
        self,
        store: BotStore,
        local_delivery: LocalDelivery,
        peers: Optional[Dict[str, Dict[str, str]]] = None,
        default_timeout: float = 120.0,
    ):
        self.store = store
        self.local_delivery = local_delivery
        self.peers = peers or {}
        self.default_timeout = default_timeout

    # ------------------------------------------------------------------ #
    # Roster
    # ------------------------------------------------------------------ #

    def roster(self, owner: str = "default", exclude: Optional[str] = None) -> List[Bot]:
        bots = [b for b in self.store.list(owner=owner, enabled_only=True) if b.id != exclude]
        return bots

    def roster_block(self, bot_id: str, owner: str = "default") -> str:
        """System-prompt block teaching a bot its teammates."""
        teammates = self.roster(owner=owner, exclude=bot_id)
        if not teammates and not self.peers:
            return ""
        lines = [
            "## Teammates",
            "You can message teammate agents with the `message_agent` tool.",
            "Attribution is added automatically; compose your own message, don't forward raw user text.",
            "If a teammate message needs no reply, answer with exactly [SILENT].",
            "",
        ]
        for bot in teammates:
            role = bot.title or bot.description or "teammate"
            lines.append(f"- {bot.handle} — {bot.name} ({role})")
        for name, cfg in self.peers.items():
            agents = cfg.get("agents") or []
            if agents:
                for agent in agents:
                    lines.append(f"- @{name}/{agent} — {agent} (on Colons instance '{name}')")
            else:
                lines.append(f"- @{name} — remote Colons instance '{name}'")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Delivery
    # ------------------------------------------------------------------ #

    async def send(
        self,
        sender: Bot,
        target: str,
        message: str,
        owner: str = "default",
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Deliver an attributed message to a teammate and return the result:

            {"status": "ok", "reply": str, "target": str}
            {"status": "queued", "target": str}         # timed out waiting
            {"status": "error", "reason": str, "message": str}
        """
        target = (target or "").strip()
        if not target:
            return {"status": "error", "reason": "invalid_target", "message": "Empty target"}
        if not (message or "").strip():
            return {"status": "error", "reason": "empty_message", "message": "Empty message"}

        # Peer form: peer[/agent]
        if "/" in target:
            peer_name, _, agent = target.partition("/")
            if peer_name in self.peers:
                return await self._send_peer(sender, peer_name, agent or None, message,
                                             timeout or self.default_timeout)

        # Local bot?
        try:
            bot = self.store.resolve(target, owner=owner)
        except KeyError as e:
            # Maybe it's a bare peer name
            if target.lstrip("@") in self.peers:
                return await self._send_peer(sender, target.lstrip("@"), None, message,
                                             timeout or self.default_timeout)
            return {"status": "error", "reason": "unknown_target", "message": str(e)}

        if bot.id == sender.id:
            return {"status": "error", "reason": "self_target",
                    "message": "Cannot message yourself"}

        attributed = self.format_attribution(sender, message)
        try:
            result = await self.local_delivery(sender.id, bot.id, attributed,
                                               timeout or self.default_timeout)
        except TimeoutError:
            return {"status": "queued", "target": bot.handle}
        except Exception as e:
            logger.warning(f"DM {sender.id} -> {bot.id} failed: {e}")
            return {"status": "error", "reason": "delivery_failed", "message": str(e)}

        reply = result.get("reply", "")
        if is_silence(reply):
            reply = ""
        return {
            "status": result.get("status", "ok"),
            "reply": reply,
            "target": bot.handle,
            "session_id": result.get("session_id"),
        }

    async def _send_peer(self, sender: Bot, peer_name: str, agent: Optional[str],
                         message: str, timeout: float) -> Dict[str, Any]:
        peer = self.peers.get(peer_name) or {}
        url = str(peer.get("url", "")).rstrip("/")
        if not url:
            return {"status": "error", "reason": "peer_not_configured",
                    "message": f"Peer '{peer_name}' has no url"}

        payload = {
            "sender": {"id": sender.id, "name": sender.name, "handle": sender.handle},
            "target": agent or "",
            "message": message,
        }
        headers = {}
        api_key = peer.get("api_key")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["x-api-key"] = api_key

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout + 15, connect=10.0)) as client:
                resp = await client.post(f"{url}/api/peer/message", json=payload, headers=headers)
        except httpx.HTTPError as e:
            return {"status": "error", "reason": "peer_unreachable",
                    "message": f"{peer_name}: {e}"}

        if resp.status_code == 401:
            return {"status": "error", "reason": "peer_unauthorized",
                    "message": f"{peer_name}: invalid API key"}
        if resp.status_code >= 400:
            return {"status": "error", "reason": "peer_error",
                    "message": f"{peer_name}: {resp.status_code} {resp.text[:200]}"}

        data = resp.json()
        reply = data.get("reply", "")
        if is_silence(reply):
            reply = ""
        return {"status": data.get("status", "ok"), "reply": reply,
                "target": f"{peer_name}/{agent}" if agent else peer_name}

    # ------------------------------------------------------------------ #
    # Formatting
    # ------------------------------------------------------------------ #

    @staticmethod
    def format_attribution(sender: Bot, message: str) -> str:
        return f"Message from 🤖 {sender.name} ({sender.handle}): {message}"

    @staticmethod
    def extract_reply(content: Optional[str]) -> str:
        return "" if is_silence(content) else (content or "")
