"""Phase 2 end-to-end smoke test: drives a real booking conversation
through the real Live API and the real tool layer, then checks Postgres
directly to confirm an appointment actually got created. Useful as a
regression check any time prompts/tools/confirmation logic change.

Run (with `uvicorn app.main:app` already running and the DB seeded):
    python scripts/test_booking_flow.py
"""
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

import asyncpg
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings  # noqa: E402
from scripts.tts_audio import synthesize_pcm16, VOICES  # noqa: E402

WS_URL = "ws://127.0.0.1:8000/ws/call"
INPUT_SAMPLE_RATE = 16000
CHUNK_BYTES = 3200
TEST_PHONE = "9988776633"
TOMORROW = (date.today() + timedelta(days=1)).isoformat()


async def speak(ws, text: str) -> None:
    pcm = await synthesize_pcm16(text, VOICES["en"], INPUT_SAMPLE_RATE)
    pcm += b"\x00" * INPUT_SAMPLE_RATE * 2  # 1s trailing silence for VAD end-of-speech
    for i in range(0, len(pcm), CHUNK_BYTES):
        await ws.send(pcm[i : i + CHUNK_BYTES])
        await asyncio.sleep(0.1)


async def main() -> None:
    async with websockets.connect(WS_URL, max_size=None) as ws:

        async def reader():
            async for message in ws:
                if not isinstance(message, bytes):
                    print("control:", message)

        reader_task = asyncio.create_task(reader())

        print("waiting for greeting...")
        await asyncio.sleep(10)

        print("speaking booking request...")
        await speak(
            ws,
            "Hi, I'd like to book an appointment with Dr. Lakshmi Rao for tomorrow at 9 AM. "
            "My name is Anita Test Patient and my phone number is 9 9 8 8 7 7 6 6 3 3.",
        )
        print("waiting for agent to read back and ask to confirm...")
        await asyncio.sleep(15)

        print("confirming...")
        await speak(ws, "Yes, that's correct, please confirm it.")
        print("waiting for booking to complete...")
        await asyncio.sleep(15)

        reader_task.cancel()

    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=1)
    row = await pool.fetchrow(
        """
        SELECT a.patient_name, a.status, s.slot_date, s.slot_time, d.name AS doctor_name
        FROM appointments a
        JOIN slots s ON s.id = a.slot_id
        JOIN doctors d ON d.id = s.doctor_id
        WHERE a.phone_number = $1
        ORDER BY a.created_at DESC
        LIMIT 1
        """,
        TEST_PHONE,
    )
    await pool.close()

    if row is None:
        print("\nFAIL: no appointment found for the test phone number.")
    else:
        print(f"\nPASS: appointment found -> {dict(row)}")


if __name__ == "__main__":
    asyncio.run(main())
