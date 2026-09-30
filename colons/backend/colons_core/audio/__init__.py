"""
Colons audio subsystem: TTS, speech-to-text hooks, voice session helpers.
"""
from .tts import (
    POPULAR_VOICES,
    BaseTTSEngine,
    EdgeTTSEngine,
    NullTTSEngine,
    Pyttsx3Engine,
    TTSConfig,
    TTSService,
)

__all__ = [
    "BaseTTSEngine",
    "EdgeTTSEngine",
    "Pyttsx3Engine",
    "NullTTSEngine",
    "TTSService",
    "TTSConfig",
    "POPULAR_VOICES",
]
