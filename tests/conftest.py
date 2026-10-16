"""Shared test fixtures. Tests run against the real local Postgres
(docker compose), not a mock — truncated and reseeded with a small
deterministic fixture before every test."""
import json
from datetime import date, time, timedelta

import asyncpg
import pytest
import pytest_asyncio

from app.config import settings

DOCTOR_A = {"name": "Dr. Test Alpha", "specialty": "General Physician", "consultation_fee": 300.00}
DOCTOR_B = {"name": "Dr. Test Beta", "specialty": "Pediatrician", "consultation_fee": 400.00}
SLOT_HOURS = ["09:00", "09:30", "16:00", "16:30"]


@pytest_asyncio.fixture
async def pool():
    async def _init_connection(conn: asyncpg.Connection) -> None:
        await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")

    p = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10, init=_init_connection)
    yield p
    await p.close()


@pytest_asyncio.fixture
async def db(pool):
    """Truncates everything and seeds 2 doctors with a few slots on
    tomorrow and the day after. Returns the pool plus the fixture's
    doctor ids for tests that need them directly."""
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE pending_confirmations, appointments, slots, doctors RESTART IDENTITY CASCADE"
        )
        doctor_a_id = await conn.fetchval(
            "INSERT INTO doctors (name, specialty, consultation_fee) VALUES ($1,$2,$3) RETURNING id",
            DOCTOR_A["name"], DOCTOR_A["specialty"], DOCTOR_A["consultation_fee"],
        )
        doctor_b_id = await conn.fetchval(
            "INSERT INTO doctors (name, specialty, consultation_fee) VALUES ($1,$2,$3) RETURNING id",
            DOCTOR_B["name"], DOCTOR_B["specialty"], DOCTOR_B["consultation_fee"],
        )
        for doctor_id in (doctor_a_id, doctor_b_id):
            for day_offset in (1, 2):
                slot_date = date.today() + timedelta(days=day_offset)
                for hour_str in SLOT_HOURS:
                    await conn.execute(
                        "INSERT INTO slots (doctor_id, slot_date, slot_time) VALUES ($1, $2, $3)",
                        doctor_id, slot_date, time.fromisoformat(hour_str),
                    )
    return pool, doctor_a_id, doctor_b_id


@pytest.fixture
def tomorrow() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


@pytest.fixture
def day_after() -> str:
    return (date.today() + timedelta(days=2)).isoformat()


@pytest.fixture
def yesterday() -> str:
    return (date.today() - timedelta(days=1)).isoformat()
