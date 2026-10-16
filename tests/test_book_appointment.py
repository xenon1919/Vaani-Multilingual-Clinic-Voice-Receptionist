import asyncio
from datetime import date as date_cls

from app.config import settings
from tools import book_appointment, cancel_appointment

BASE_ARGS = {
    "doctor_name": "Test Alpha",
    "time": "09:00",
    "patient_name": "Ramesh Kumar",
    "phone_number": "9876543210",
}


async def test_book_requires_confirmation_then_succeeds(db, tomorrow):
    pool, _, _ = db
    args = {**BASE_ARGS, "date": tomorrow}

    first = await book_appointment.run(pool, "call-1", args)
    assert first["status"] == "needs_confirmation"
    token = first["confirmation_token"]

    second = await book_appointment.run(pool, "call-1", {**args, "confirmed": True, "confirmation_token": token})
    assert second["status"] == "ok"
    assert second["appointment"]["patient_name"] == "Ramesh Kumar"

    row = await pool.fetchrow("SELECT status FROM appointments WHERE phone_number='9876543210'")
    assert row["status"] == "booked"


async def test_confirmed_true_without_token_is_rejected(db, tomorrow):
    """confirmed=true with no token can't be trusted -- must not book."""
    pool, _, _ = db
    args = {**BASE_ARGS, "date": tomorrow, "confirmed": True}

    result = await book_appointment.run(pool, "call-1", args)
    assert result["status"] != "ok"

    count = await pool.fetchval("SELECT count(*) FROM appointments")
    assert count == 0


async def test_confirmation_token_from_another_tool_rejected(db, tomorrow):
    pool, _, _ = db
    first = await book_appointment.run(pool, "call-1", {**BASE_ARGS, "date": tomorrow})
    token = first["confirmation_token"]

    # A token minted for book_appointment must not work on cancel_appointment.
    result = await cancel_appointment.run(
        pool, "call-1", {"phone_number": "9876543210", "confirmed": True, "confirmation_token": token}
    )
    assert result["status"] == "error"
    assert result["error"] == "confirmation_expired"


async def test_idempotent_replay_of_confirmed_booking(db, tomorrow):
    pool, _, _ = db
    args = {**BASE_ARGS, "date": tomorrow}
    first = await book_appointment.run(pool, "call-1", args)
    confirm_args = {**args, "confirmed": True, "confirmation_token": first["confirmation_token"]}

    result_1 = await book_appointment.run(pool, "call-1", confirm_args)
    result_2 = await book_appointment.run(pool, "call-1", confirm_args)

    assert result_1["status"] == "ok"
    assert result_2["status"] == "ok"
    assert result_1["appointment"]["appointment_id"] == result_2["appointment"]["appointment_id"]

    count = await pool.fetchval("SELECT count(*) FROM appointments WHERE status = 'booked'")
    assert count == 1  # the replay did not create a duplicate


async def test_slot_taken_after_someone_else_books_first(db, tomorrow):
    pool, _, _ = db
    args_a = {**BASE_ARGS, "date": tomorrow}
    args_b = {**BASE_ARGS, "date": tomorrow, "patient_name": "Second Caller", "phone_number": "9123456780"}

    first = await book_appointment.run(pool, "call-a", args_a)
    await book_appointment.run(
        pool, "call-a", {**args_a, "confirmed": True, "confirmation_token": first["confirmation_token"]}
    )

    second = await book_appointment.run(pool, "call-b", args_b)
    assert second["status"] == "error"
    assert second["error"] == "slot_taken"
    assert len(second["alternatives"]) > 0


async def test_double_booking_race_unique_constraint_at_db_level(db, tomorrow):
    """The design rule this enforces: a unique constraint makes double
    booking impossible at the DB level, even under a real concurrent
    race -- not just "checked carefully" in application code."""
    pool, doctor_a_id, _ = db
    slot = await pool.fetchrow(
        "SELECT id FROM slots WHERE doctor_id = $1 AND slot_date = $2 AND slot_time = '09:00'",
        doctor_a_id,
        date_cls.fromisoformat(tomorrow),
    )
    slot_id = slot["id"]

    async def attempt(phone: str):
        async with pool.acquire() as conn:
            return await book_appointment._insert_appointment(conn, slot_id, "Racer", phone)

    results = await asyncio.gather(attempt("9111111111"), attempt("9222222222"))
    succeeded = [r for r in results if r is not None]
    failed = [r for r in results if r is None]
    assert len(succeeded) == 1
    assert len(failed) == 1

    count = await pool.fetchval(
        "SELECT count(*) FROM appointments WHERE slot_id = $1 AND status = 'booked'", slot_id
    )
    assert count == 1


async def test_invalid_phone(db, tomorrow):
    pool, _, _ = db
    result = await book_appointment.run(pool, "call-1", {**BASE_ARGS, "date": tomorrow, "phone_number": "123"})
    assert result["status"] == "error"
    assert result["error"] == "invalid_phone"


async def test_past_date(db, yesterday):
    pool, _, _ = db
    result = await book_appointment.run(pool, "call-1", {**BASE_ARGS, "date": yesterday})
    assert result["status"] == "error"
    assert result["error"] == "past_date"


async def test_doctor_not_found(db, tomorrow):
    pool, _, _ = db
    result = await book_appointment.run(pool, "call-1", {**BASE_ARGS, "date": tomorrow, "doctor_name": "Nobody"})
    assert result["status"] == "error"
    assert result["error"] == "doctor_not_found"


async def test_slot_not_found(db, tomorrow):
    pool, _, _ = db
    result = await book_appointment.run(pool, "call-1", {**BASE_ARGS, "date": tomorrow, "time": "23:45"})
    assert result["status"] == "error"
    assert result["error"] == "slot_not_found"


async def test_ablation_skip_confirmation(db, tomorrow, monkeypatch):
    monkeypatch.setattr(settings, "require_confirmation", False)
    pool, _, _ = db
    result = await book_appointment.run(pool, "call-1", {**BASE_ARGS, "date": tomorrow})
    assert result["status"] == "ok"  # booked directly, no confirmation round-trip
