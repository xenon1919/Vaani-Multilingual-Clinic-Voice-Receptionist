"""The two-step confirmation protocol shared by book/reschedule/cancel:
the first call creates a pending_confirmations row and returns its token
for the agent to read back; the second call (confirmed=True) must present
a valid, unexpired token. Also the slot-availability checks that back
check_availability and the slot_taken error's alternatives.

A plain row with expires_at, not Redis, per the project's constraints.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import asyncpg

CONFIRMATION_TTL_SECONDS = 120


async def is_slot_available(
    conn: asyncpg.Connection, slot_id: int, excluding_call_id: str | None = None
) -> bool:
    booked = await conn.fetchval(
        "SELECT 1 FROM appointments WHERE slot_id = $1 AND status = 'booked'", slot_id
    )
    if booked:
        return False
    if excluding_call_id:
        held = await conn.fetchval(
            "SELECT 1 FROM pending_confirmations WHERE slot_id = $1 AND expires_at > now() AND call_id != $2",
            slot_id,
            excluding_call_id,
        )
    else:
        held = await conn.fetchval(
            "SELECT 1 FROM pending_confirmations WHERE slot_id = $1 AND expires_at > now()",
            slot_id,
        )
    return held is None


async def find_alternative_slots(
    conn: asyncpg.Connection, doctor_id: int, from_date: date, limit: int = 3
) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT s.id, s.slot_date, s.slot_time
        FROM slots s
        LEFT JOIN appointments a ON a.slot_id = s.id AND a.status = 'booked'
        WHERE s.doctor_id = $1
          AND s.slot_date >= $2
          AND a.id IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM pending_confirmations pc
              WHERE pc.slot_id = s.id AND pc.expires_at > now()
          )
        ORDER BY s.slot_date, s.slot_time
        LIMIT $3
        """,
        doctor_id,
        from_date,
        limit,
    )
    return [{"date": r["slot_date"].isoformat(), "time": r["slot_time"].strftime("%H:%M")} for r in rows]


async def create_pending_confirmation(
    conn: asyncpg.Connection,
    call_id: str,
    tool_name: str,
    payload: dict,
    slot_id: int | None = None,
) -> str:
    token = uuid.uuid4().hex
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=CONFIRMATION_TTL_SECONDS)
    await conn.execute(
        """
        INSERT INTO pending_confirmations (token, call_id, tool_name, slot_id, payload, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        token,
        call_id,
        tool_name,
        slot_id,
        payload,
        expires_at,
    )
    return token


async def get_valid_pending_confirmation(
    conn: asyncpg.Connection, token: str, tool_name: str
) -> asyncpg.Record | None:
    """Returns the row if the token exists for this tool and is either
    already consumed (idempotent replay) or not yet expired. Returns None
    if the token doesn't exist or has expired unconsumed."""
    row = await conn.fetchrow(
        "SELECT * FROM pending_confirmations WHERE token = $1 AND tool_name = $2", token, tool_name
    )
    if row is None:
        return None
    if row["result_appointment_id"] is None and row["expires_at"] < datetime.now(timezone.utc):
        return None
    return row


async def mark_confirmation_consumed(
    conn: asyncpg.Connection, token: str, appointment_id: int
) -> None:
    await conn.execute(
        "UPDATE pending_confirmations SET result_appointment_id = $1 WHERE token = $2",
        appointment_id,
        token,
    )
