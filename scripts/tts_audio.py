"""Synthesizes caller speech with Edge TTS (free) and converts it to raw
PCM16 at the Live API's required input rate. Shared by the Phase 0 spike
and the Phase 3 audio evaluation tier."""
import asyncio
import io

import edge_tts
from pydub import AudioSegment

VOICES = {
    "te": "te-IN-ShrutiNeural",
    "hi": "hi-IN-SwaraNeural",
    "en": "en-IN-NeerjaNeural",
}


async def synthesize_mp3(text: str, voice: str) -> bytes:
    """Synthesizes text to MP3 bytes via Edge TTS."""
    communicate = edge_tts.Communicate(text, voice)
    chunks = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.extend(chunk["data"])
    return bytes(chunks)


def mp3_to_pcm16(mp3_bytes: bytes, sample_rate_hz: int) -> bytes:
    """Converts MP3 bytes to mono 16-bit little-endian PCM at the given rate."""
    audio = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
    audio = audio.set_frame_rate(sample_rate_hz).set_channels(1).set_sample_width(2)
    return audio.raw_data


async def synthesize_pcm16(text: str, voice: str, sample_rate_hz: int) -> bytes:
    mp3_bytes = await synthesize_mp3(text, voice)
    return mp3_to_pcm16(mp3_bytes, sample_rate_hz)
