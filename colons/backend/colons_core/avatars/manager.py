"""
Avatar system for Colons - built-in characters and pets, with deterministic
SVG generation so no binary assets are required and everything is self-hostable.
"""
import hashlib
import random
from dataclasses import dataclass
from typing import Dict, List, Optional

# --------------------------------------------------------------------------- #
# Avatar catalog (characters + pets), echoing the Dots experience
# --------------------------------------------------------------------------- #

@dataclass
class Avatar:
    id: str
    name: str
    kind: str  # "character" | "pet"
    colors: List[str]
    personality: str = ""
    emoji: str = ""
    description: str = ""

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "colors": self.colors,
            "personality": self.personality,
            "emoji": self.emoji,
            "description": self.description,
        }


CHARACTERS: List[Avatar] = [
    Avatar("colons", "White", "character", ["#ffffff", "#ffffff"], "clear, helpful", ":",
           "The classic Colons mark on a white circle."),
    Avatar("red", "Red", "character", ["#f87171", "#f87171"], "bold, driven", ":",
           "The Colons mark on a red circle."),
    Avatar("iggy", "Iggy", "character", ["#ff7ac2", "#ff3d8b"], "warm, encouraging", "🌸",
           "A friendly companion that keeps you on track."),
    Avatar("felipe", "Felipe", "character", ["#5b8def", "#2f5fd0"], "precise, analytical", "🔵",
           "Calm and methodical; great for planning and reviews."),
    Avatar("todd", "Todd", "character", ["#3ddc97", "#12a86b"], "curious, thorough", "🟢",
           "Digs into details and never misses an edge case."),
    Avatar("alfred", "Alfred", "character", ["#ffd166", "#e0a800"], "professional, dependable", "🟡",
           "A reliable assistant for serious work."),
    Avatar("jojo", "Jojo", "character", ["#a78bfa", "#7c3aed"], "creative, playful", "🟣",
           "Brings imagination to every task."),
    Avatar("nova", "Nova", "character", ["#22d3ee", "#0891b2"], "fast, energetic", "💠",
           "Quick thinker for rapid iteration."),
    Avatar("sage", "Sage", "character", ["#84cc16", "#4d7c0f"], "wise, concise", "🌿",
           "Distills complexity into clarity."),
    Avatar("ember", "Ember", "character", ["#f97316", "#c2410c"], "bold, driven", "🔥",
           "Pushes projects across the finish line."),
]

PETS: List[Avatar] = [
    Avatar("mochi", "Mochi", "pet", ["#fda4af", "#f43f5e"], "cuddly, loyal", "🐹",
           "A tiny companion that celebrates your wins."),
    Avatar("biscuit", "Biscuit", "pet", ["#fcd34d", "#f59e0b"], "sleepy, comforting", "🐶",
           "Always happy to see you."),
    Avatar("pixel", "Pixel", "pet", ["#93c5fd", "#3b82f6"], "mischievous, clever", "🐱",
           "Knocks tasks off your desk."),
    Avatar("waddles", "Waddles", "pet", ["#a5b4fc", "#6366f1"], "chill, unflappable", "🐧",
           "Nothing fazes Waddles."),
    Avatar("sprout", "Sprout", "pet", ["#86efac", "#22c55e"], "gentle, growing", "🌱",
           "A little friend that grows with your projects."),
    Avatar("nimbus", "Nimbus", "pet", ["#c4b5fd", "#8b5cf6"], "dreamy, imaginative", "☁️",
           "Floats along on your ideas."),
]


class AvatarManager:
    """Manages avatar selection and generates deterministic SVG avatars."""

    def __init__(self, default: str = "colons"):
        self.catalog: Dict[str, Avatar] = {a.id: a for a in CHARACTERS + PETS}
        self._selected: Dict[str, str] = {}  # agent_id -> avatar_id
        self.default = default

    # ------------------------------------------------------------------ #
    # Selection
    # ------------------------------------------------------------------ #

    def list_avatars(self, kind: Optional[str] = None) -> List[Dict]:
        avatars = list(self.catalog.values())
        if kind:
            avatars = [a for a in avatars if a.kind == kind]
        return [a.to_dict() for a in avatars]

    def get(self, avatar_id: str) -> Optional[Avatar]:
        return self.catalog.get(avatar_id)

    def select(self, agent_id: str, avatar_id: str) -> Avatar:
        if avatar_id not in self.catalog:
            raise ValueError(f"Unknown avatar: {avatar_id}")
        self._selected[agent_id] = avatar_id
        return self.catalog[avatar_id]

    def current(self, agent_id: str) -> Avatar:
        return self.catalog.get(self._selected.get(agent_id, self.default),
                                self.catalog[self.default])

    def generate_for(self, seed: str) -> Avatar:
        """Deterministically assign an avatar for a user/agent (like Dots generating a pet)."""
        digest = hashlib.sha256(seed.encode()).hexdigest()
        rng = random.Random(digest)
        pool = CHARACTERS + PETS
        return rng.choice(pool)

    # ------------------------------------------------------------------ #
    # SVG generation (no image libraries needed)
    # ------------------------------------------------------------------ #

    def svg(self, avatar_id: str, size: int = 128) -> str:
        avatar = self.catalog.get(avatar_id)
        if not avatar:
            avatar = self.catalog[self.default]
        return self._render_svg(avatar, size)

    def _render_svg(self, avatar: Avatar, size: int) -> str:
        color = avatar.colors[0]
        return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 128 128">
  <circle cx="64" cy="64" r="60" fill="{color}" stroke="#dce1e8" stroke-width="1"/>
  <circle cx="64" cy="48" r="12" fill="#101827"/>
  <circle cx="64" cy="80" r="12" fill="#101827"/>
</svg>"""

    @staticmethod
    def _escape(text: str) -> str:
        return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def svg_data_uri(self, avatar_id: str, size: int = 128) -> str:
        import base64
        svg = self.svg(avatar_id, size)
        encoded = base64.b64encode(svg.encode()).decode()
        return f"data:image/svg+xml;base64,{encoded}"
