"""
Room orchestration: serial rounds of member turns with mentions, passes,
and hard caps (bounded so a room can never spin).
"""
import logging
import re
from typing import Dict, List, Optional, Set

from ..bots.messenger import is_silence
from .store import Room, RoomMessage, RoomStore

logger = logging.getLogger(__name__)

MAX_ROUNDS = 3
MAX_MESSAGES_PER_SEND = 10
MIN_MEMBERS = 2
MAX_MEMBERS = 6

_MENTION_RE = re.compile(r"@([a-zA-Z0-9][a-zA-Z0-9_-]*)")

ROOM_RULES = """\
You are in a group room with other agents and the user.
- Reply briefly (1-4 sentences) and only if you add something new.
- Address teammates with @handle and the user with @user.
- If you have nothing to add, reply with exactly [SILENT].
- Escalate real decisions or judgments to @user.
- Do not repeat what others already said."""


class RoomService:
    """Owns room lifecycle and the message orchestration loop."""

    def __init__(self, store: RoomStore, manager):
        self.store = store
        self.manager = manager

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    def create(self, name: str, members: List[str], owner: str = "default") -> Room:
        members = [m.lstrip("@") for m in members]
        if len(members) < MIN_MEMBERS:
            raise ValueError(f"A room needs at least {MIN_MEMBERS} bots")
        if len(members) > MAX_MEMBERS:
            raise ValueError(f"A room holds at most {MAX_MEMBERS} bots")
        for member in members:
            if not self.manager.bots.get(member):
                raise ValueError(f"Unknown bot: {member}")
        room = Room(name=name.strip() or "Room", owner=owner, members=members)
        return self.store.save(room)

    def update(self, room_id: str, **changes) -> Optional[Room]:
        room = self.store.get(room_id)
        if not room:
            return None
        if "name" in changes and changes["name"]:
            room.name = changes["name"].strip()
        if "members" in changes and changes["members"] is not None:
            members = [m.lstrip("@") for m in changes["members"]]
            if not (MIN_MEMBERS <= len(members) <= MAX_MEMBERS):
                raise ValueError(f"Members must be between {MIN_MEMBERS} and {MAX_MEMBERS}")
            for member in members:
                if not self.manager.bots.get(member):
                    raise ValueError(f"Unknown bot: {member}")
            room.members = members
        return self.store.save(room)

    def delete(self, room_id: str) -> bool:
        return self.store.delete(room_id)

    def get(self, room_id: str) -> Optional[Room]:
        return self.store.get(room_id)

    def list(self, owner: str = "default") -> List[Dict]:
        out = []
        for room in self.store.list(owner=owner):
            data = room.to_dict()
            data["message_count"] = self.store.message_count(room.id)
            data["needs_user"] = bool(room.metadata.get("needs_user"))
            members = []
            for bot_id in room.members:
                bot = self.manager.bots.get(bot_id)
                if bot:
                    members.append(bot.to_dict())
            data["member_defs"] = members
            out.append(data)
        return out

    def history(self, room_id: str, limit: int = 100, offset: int = 0) -> List[Dict]:
        return [m.to_dict() for m in self.store.messages(room_id, limit=limit, offset=offset)]

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #

    def _mentions(self, text: str) -> Set[str]:
        return {m.lower() for m in _MENTION_RE.findall(text or "")}

    def _member_ids(self, room: Room) -> List[str]:
        return [m for m in room.members if self.manager.bots.get(m)]

    def build_prompt(self, room: Room, bot_id: str, user_message: str,
                     round_no: int, transcript: List[RoomMessage]) -> str:
        bot = self.manager.bots.get(bot_id)
        name = bot.name if bot else bot_id
        lines = [
            f"[Room: {room.name}] Round {round_no}/{MAX_ROUNDS}",
            ROOM_RULES,
            "",
            "Members: " + ", ".join(
                f"@{m} ({self.manager.bots.get(m).name})" if self.manager.bots.get(m) else f"@{m}"
                for m in self._member_ids(room)
            ),
            "",
            "Conversation so far:",
        ]
        for msg in transcript[-30:]:
            speaker = "user" if msg.speaker == "user" else (
                f"@{msg.speaker} ({self.manager.bots.get(msg.speaker).name})"
                if self.manager.bots.get(msg.speaker) else f"@{msg.speaker}"
            )
            content = msg.content if len(msg.content) <= 1500 else msg.content[:1500] + "…"
            lines.append(f"{speaker}: {content}")
        lines.append("")
        lines.append(f"Your turn, {name}. Reply briefly or pass with [SILENT].")
        return "\n".join(lines)

    async def send(self, room_id: str, message: str, speaker: str = "user") -> List[RoomMessage]:
        """
        Post a user message and run up to MAX_ROUNDS serial rounds of member
        turns. Returns every room message added during this send.
        """
        room = self.store.get(room_id)
        if not room:
            raise KeyError(f"Unknown room: {room_id}")

        added: List[RoomMessage] = []
        user_msg = self.store.append(RoomMessage(room_id=room.id, speaker=speaker,
                                                 content=message))
        added.append(user_msg)

        members = self._member_ids(room)
        if not members:
            return added

        # Mentions scope the first round; everyone responds otherwise
        mentions = self._mentions(message) - {"user", "all", "everyone"}
        if mentions:
            targets = [m for m in members if m.lower() in mentions]
            if not targets:
                targets = members
        else:
            targets = list(members)

        current_targets = list(targets)

        for round_no in range(1, MAX_ROUNDS + 1):
            spoke = False
            next_targets: List[str] = []
            for bot_id in list(current_targets):
                if len(added) >= MAX_MESSAGES_PER_SEND:
                    break
                bot = self.manager.bots.get(bot_id)
                if not bot or not bot.enabled:
                    continue

                agent = self.manager.get_bot_agent(bot_id)
                await agent.start()
                transcript = self.store.recent(room.id, limit=30)
                prompt = self.build_prompt(room, bot_id, message, round_no, transcript)

                reply = ""
                try:
                    async for event in agent.chat(
                        prompt, session_id=f"room-{room.id}-{bot_id}", stream=False
                    ):
                        etype = event.get("type")
                        if etype == "done":
                            reply = event.get("content") or reply
                        elif etype == "error":
                            logger.warning(f"Room {room.id}: {bot_id} failed: "
                                           f"{event.get('message')}")
                            break
                except Exception as e:
                    logger.warning(f"Room {room.id}: {bot_id} crashed: {e}")
                    continue

                if is_silence(reply):
                    continue

                # Mentions in a reply pull teammates into the next round
                for mentioned in self._mentions(reply):
                    if mentioned in members and mentioned != bot_id and mentioned not in next_targets:
                        next_targets.append(mentioned)

                msg = self.store.append(RoomMessage(
                    room_id=room.id, speaker=bot_id, content=reply,
                    metadata={"mentions": sorted(self._mentions(reply)),
                              "round": round_no},
                ))
                added.append(msg)
                spoke = True

                if "@user" in reply.lower():
                    room.metadata["needs_user"] = True
                    self.store.save(room)

            if not spoke or len(added) >= MAX_MESSAGES_PER_SEND:
                break
            if not next_targets:
                break
            current_targets = next_targets

        return added

    def mark_seen(self, room_id: str):
        room = self.store.get(room_id)
        if room and room.metadata.get("needs_user"):
            room.metadata["needs_user"] = False
            self.store.save(room)
