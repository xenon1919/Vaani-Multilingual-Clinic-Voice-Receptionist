"""Seeds 3 synthetic doctors and 2 weeks of slots. Run after the DB is up:
    python db/seed.py
Safe to re-run: clears and re-inserts rather than erroring on conflict.
"""
import asyncio
from datetime import date, time, timedelta

import asyncpg
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://clinic:clinic@localhost:5432/clinic")

DOCTORS = [
    {"name": "Dr. Lakshmi Rao", "specialty": "General Physician", "consultation_fee": 300.00},
    {"name": "Dr. Arjun Mehta", "specialty": "Pediatrician", "consultation_fee": 400.00},
    {"name": "Dr. Fatima Sheikh", "specialty": "Dermatologist", "consultation_fee": 500.00},
]

MORNING_SLOTS = [time(hour=h, minute=m) for h in range(9, 13) for m in (0, 30)]
EVENING_SLOTS = [time(hour=h, minute=m) for h in range(14, 18) for m in (0, 30)]
DAYS_AHEAD = 14


async def seed() -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            "TRUNCATE pending_confirmations, appointments, slots, doctors RESTART IDENTITY CASCADE"
        )

        doctor_ids = []
        for doc in DOCTORS:
            doctor_id = await conn.fetchval(
                "INSERT INTO doctors (name, specialty, consultation_fee) VALUES ($1, $2, $3) RETURNING id",
                doc["name"], doc["specialty"], doc["consultation_fee"],
            )
            doctor_ids.append(doctor_id)

        today = date.today()
        rows = []
        for doctor_id in doctor_ids:
            for day_offset in range(DAYS_AHEAD):
                slot_date = today + timedelta(days=day_offset)
                if slot_date.weekday() == 6:  # skip Sunday
                    continue
                for slot_time in MORNING_SLOTS + EVENING_SLOTS:
                    rows.append((doctor_id, slot_date, slot_time))

        await conn.executemany(
            "INSERT INTO slots (doctor_id, slot_date, slot_time) VALUES ($1, $2, $3)",
            rows,
        )
        print(f"Seeded {len(doctor_ids)} doctors and {len(rows)} slots over {DAYS_AHEAD} days.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed())
