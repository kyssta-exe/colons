"""
Text-to-speech for Colons.
Primary engine: Microsoft Edge TTS (edge-tts) - free, high quality, many voices.
Fallbacks: pyttsx3 (offline), and a null engine.
"""
import asyncio
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional

logger = logging.getLogger(__name__)


# A curated subset of popular Edge TTS voices (the service has 300+)
POPULAR_VOICES: List[Dict[str, str]] = [
    {"id": "en-US-AriaNeural", "name": "Aria", "locale": "en-US", "gender": "Female"},
    {"id": "en-US-GuyNeural", "name": "Guy", "locale": "en-US", "gender": "Male"},
    {"id": "en-US-JennyNeural", "name": "Jenny", "locale": "en-US", "gender": "Female"},
    {"id": "en-US-ChristopherNeural", "name": "Christopher", "locale": "en-US", "gender": "Male"},
    {"id": "en-US-EricNeural", "name": "Eric", "locale": "en-US", "gender": "Male"},
    {"id": "en-US-MichelleNeural", "name": "Michelle", "locale": "en-US", "gender": "Female"},
    {"id": "en-GB-SoniaNeural", "name": "Sonia", "locale": "en-GB", "gender": "Female"},
    {"id": "en-GB-RyanNeural", "name": "Ryan", "locale": "en-GB", "gender": "Male"},
    {"id": "en-AU-NatashaNeural", "name": "Natasha", "locale": "en-AU", "gender": "Female"},
    {"id": "en-IN-NeerjaNeural", "name": "Neerja", "locale": "en-IN", "gender": "Female"},
    {"id": "hi-IN-SwaraNeural", "name": "Swara", "locale": "hi-IN", "gender": "Female"},
    {"id": "es-ES-ElviraNeural", "name": "Elvira", "locale": "es-ES", "gender": "Female"},
    {"id": "fr-FR-DeniseNeural", "name": "Denise", "locale": "fr-FR", "gender": "Female"},
    {"id": "de-DE-KatjaNeural", "name": "Katja", "locale": "de-DE", "gender": "Female"},
    {"id": "ja-JP-NanamiNeural", "name": "Nanami", "locale": "ja-JP", "gender": "Female"},
    {"id": "zh-CN-XiaoxiaoNeural", "name": "Xiaoxiao", "locale": "zh-CN", "gender": "Female"},
]


@dataclass
class TTSConfig:
    voice: str = "en-US-AriaNeural"
    rate: str = "+0%"
    volume: str = "+0%"
    pitch: str = "+0Hz"
    format: str = "mp3"


class BaseTTSEngine:
    name = "base"

    async def synthesize(self, text: str, config: TTSConfig) -> bytes:
        raise NotImplementedError

    async def stream(self, text: str, config: TTSConfig) -> AsyncGenerator[bytes, None]:
        data = await self.synthesize(text, config)
        yield data

    async def list_voices(self) -> List[Dict]:
        return POPULAR_VOICES

    @property
    def available(self) -> bool:
        return True


class EdgeTTSEngine(BaseTTSEngine):
    """
    Microsoft Edge TTS via the `edge-tts` package.
    Free, no API key, 300+ neural voices, streaming support.
    """

    name = "edge"

    def __init__(self):
        try:
            import edge_tts  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False
            logger.warning("edge-tts not installed; install with `pip install edge-tts`")

    @property
    def available(self) -> bool:
        return self._available

    async def synthesize(self, text: str, config: TTSConfig) -> bytes:
        if not self._available:
            raise RuntimeError("edge-tts is not available")
        import edge_tts

        communicate = edge_tts.Communicate(
            text,
            config.voice,
            rate=config.rate,
            volume=config.volume,
            pitch=config.pitch,
        )
        chunks: List[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    async def stream(self, text: str, config: TTSConfig) -> AsyncGenerator[bytes, None]:
        if not self._available:
            raise RuntimeError("edge-tts is not available")
        import edge_tts

        communicate = edge_tts.Communicate(
            text, config.voice,
            rate=config.rate, volume=config.volume, pitch=config.pitch,
        )
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]

    async def list_voices(self, locale: Optional[str] = None) -> List[Dict]:
        if not self._available:
            return POPULAR_VOICES
        try:
            import edge_tts
            voices = await edge_tts.list_voices()
            out = [
                {
                    "id": v["ShortName"],
                    "name": v["ShortName"].split("-")[-1].replace("Neural", ""),
                    "locale": v["Locale"],
                    "gender": v.get("Gender", ""),
                }
                for v in voices
            ]
            if locale:
                out = [v for v in out if v["locale"].lower().startswith(locale.lower())]
            return out
        except Exception as e:
            logger.warning(f"Could not list Edge voices: {e}")
            return POPULAR_VOICES


class Pyttsx3Engine(BaseTTSEngine):
    """Offline fallback using pyttsx3 (writes to a temp wav)."""

    name = "pyttsx3"

    def __init__(self):
        try:
            import pyttsx3
            self._engine = pyttsx3.init()
            self._available = True
        except Exception:
            self._engine = None
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    async def synthesize(self, text: str, config: TTSConfig) -> bytes:
        if not self._available:
            raise RuntimeError("pyttsx3 is not available")

        def _run() -> bytes:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                path = f.name
            self._engine.save_to_file(text, path)
            self._engine.runAndWait()
            data = Path(path).read_bytes()
            Path(path).unlink(missing_ok=True)
            return data

        return await asyncio.get_event_loop().run_in_executor(None, _run)


class NullTTSEngine(BaseTTSEngine):
    name = "null"

    @property
    def available(self) -> bool:
        return False


class TTSService:
    """Picks the best available engine and exposes a simple API."""

    def __init__(self, preferred: str = "edge", config: Optional[TTSConfig] = None):
        self.config = config or TTSConfig()
        engines = {
            "edge": EdgeTTSEngine,
            "pyttsx3": Pyttsx3Engine,
            "null": NullTTSEngine,
        }
        self.engine: BaseTTSEngine = NullTTSEngine()
        order = [preferred] + [k for k in engines if k != preferred]
        for key in order:
            engine = engines[key]()
            if engine.available:
                self.engine = engine
                break
        logger.info(f"TTS engine: {self.engine.name}")

    async def speak_to_bytes(self, text: str, **overrides) -> bytes:
        cfg = self._merge(overrides)
        return await self.engine.synthesize(text, cfg)

    async def speak_stream(self, text: str, **overrides) -> AsyncGenerator[bytes, None]:
        cfg = self._merge(overrides)
        async for chunk in self.engine.stream(text, cfg):
            yield chunk

    async def speak_to_file(self, text: str, path: str, **overrides) -> str:
        data = await self.speak_to_bytes(text, **overrides)
        Path(path).write_bytes(data)
        return path

    async def list_voices(self, locale: Optional[str] = None) -> List[Dict]:
        try:
            return await self.engine.list_voices(locale)  # type: ignore[call-arg]
        except TypeError:
            return await self.engine.list_voices()

    def _merge(self, overrides: Dict) -> TTSConfig:
        return TTSConfig(
            voice=overrides.get("voice", self.config.voice),
            rate=overrides.get("rate", self.config.rate),
            volume=overrides.get("volume", self.config.volume),
            pitch=overrides.get("pitch", self.config.pitch),
            format=overrides.get("format", self.config.format),
        )

    @property
    def available(self) -> bool:
        return self.engine.available

    @property
    def engine_name(self) -> str:
        return self.engine.name
