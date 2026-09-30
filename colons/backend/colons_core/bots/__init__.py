"""
Bots: first-class named agents (the Colons take on Hermes Bot Mode).

A bot is a named agent with its own title, persona, avatar, model and chat
history. Bots message each other, deliberate in rooms, and can be reached
across Colons instances via peers.
"""
from .messenger import SILENCE_TOKENS, BotMessenger, is_silence
from .store import Bot, BotStore

__all__ = ["Bot", "BotStore", "BotMessenger", "SILENCE_TOKENS", "is_silence"]
