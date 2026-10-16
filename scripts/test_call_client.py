"""Manual Phase 1 smoke test: connects to the running server's /ws/call like
a browser would, confirms the greeting/consent notice fires automatically,
then streams a real synthesized utterance (paced in real time, with trailing
silence, so the server's own VAD can detect end-of-speech naturally) and
saves whatever audio comes back so it can be listened to.

A single continuous reader task runs for the whole test -- like a real
browser's onmessage handler, which never stops listening -- rather than
alternating separate read loops, which can stall the server's relay if the
client briefly stops draining its socket.

Run (with `uvicorn app.main:app` already running):
    python scripts/test_call_client.py
"""
import asyncio
import sys
import wave
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.tts_audio import synthesize_pcm16, VOICES  # noqa: E402

WS_URL = "ws://127.0.0.1:8000/ws/call"
OUTPUT_SAMPLE_RATE = 24000
INPUT_SAMPLE_RATE = 16000
CHUNK_BYTES = 3200  # 100ms at 16kHz/16-bit/mono
OUT_WAV = Path(__file__).resolve().parent / "test_call_output.wav"
TEST_DURATION_S = 25


async def main() -> None:
    received_audio = bytearray()
    control_messages: list[str] = []

    async with websockets.connect(WS_URL, max_size=None) as ws:

        async def reader():
            async for message in ws:
                if isinstance(message, bytes):
                    received_audio.extend(message)
                else:
                    control_messages.append(message)
                    print("control:", message)

        async def sender():
            print("waiting 4s for the automatic greeting to play out...")
            await asyncio.sleep(4)
            print(f"audio bytes received so far (greeting): {len(received_audio)}")

            print("synthesizing + streaming a real utterance with real-time pacing...")
            pcm = await synthesize_pcm16(
                "Hi, my name is Anita, I want to book an appointment for tomorrow.",
                VOICES["en"],
                INPUT_SAMPLE_RATE,
            )
            pcm += b"\x00" * INPUT_SAMPLE_RATE * 2  # 1s trailing silence for VAD end-of-speech
            for i in range(0, len(pcm), CHUNK_BYTES):
                await ws.send(pcm[i : i + CHUNK_BYTES])
                await asyncio.sleep(0.1)
            print("finished sending utterance, waiting for reply...")

        reader_task = asyncio.create_task(reader())
        sender_task = asyncio.create_task(sender())
        await asyncio.wait([sender_task], timeout=TEST_DURATION_S)
        await asyncio.sleep(5)  # let the reply keep streaming in after sending finishes
        reader_task.cancel()

    with wave.open(str(OUT_WAV), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(OUTPUT_SAMPLE_RATE)
        w.writeframes(bytes(received_audio))

    print(f"\ntotal audio bytes received: {len(received_audio)}")
    print(f"control messages: {control_messages}")
    print(f"saved combined audio to {OUT_WAV}")


if __name__ == "__main__":
    asyncio.run(main())
