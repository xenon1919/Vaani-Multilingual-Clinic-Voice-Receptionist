from datetime import date as date_cls, time as time_cls

from tools import cancel_appointment


async def _book_directly(pool, doctor_id, date_, time_, patient_name, phone):
    slot = await pool.fetchrow(
        "SELECT id FROM slots WHERE doctor_id = $1 AND slot_date = $2 AND slot_time = $3",
        doctor_id, date_cls.fromisoformat(date_), time_cls.fromisoformat(time_),
    )
    appointment_id = await pool.fetchval(
        "INSERT INTO appointments (slot_id, patient_name, phone_number, status) VALUES ($1,$2,$3,'booked') RETURNING id",
        slot["id"], patient_name, phone,
    )
    await pool.execute("UPDATE slots SET is_booked = TRUE WHERE id = $1", slot["id"])
    return appointment_id, slot["id"]


async def test_cancel_requires_confirmation_then_cancels(db, tomorrow):
    pool, doctor_a_id, _ = db
    await _book_directly(pool, doctor_a_id, tomorrow, "09:00", "Ramesh Kumar", "9876543210")

    first = await cancel_appointment.run(pool, "call-1", {"phone_number": "9876543210"})
    assert first["status"] == "needs_confirmation"

    second = await cancel_appointment.run(
        pool, "call-1",
        {"phone_number": "9876543210", "confirmed": True, "confirmation_token": first["confirmation_token"]},
    )
    assert second["status"] == "ok"

    row = await pool.fetchrow("SELECT status FROM appointments WHERE phone_number = '9876543210'")
    assert row["status"] == "cancelled"


async def test_cancel_frees_the_slot(db, tomorrow):
    pool, doctor_a_id, _ = db
    _, slot_id = await _book_directly(pool, doctor_a_id, tomorrow, "09:00", "Ramesh Kumar", "9876543210")

    first = await cancel_appointment.run(pool, "call-1", {"phone_number": "9876543210"})
    await cancel_appointment.run(
        pool, "call-1",
        {"phone_number": "9876543210", "confirmed": True, "confirmation_token": first["confirmation_token"]},
    )

    row = await pool.fetchrow("SELECT is_booked FROM slots WHERE id = $1", slot_id)
    assert row["is_booked"] is False


async def test_appointment_not_found(db):
    pool, _, _ = db
    result = await cancel_appointment.run(pool, "call-1", {"phone_number": "9876543210"})
    assert result["status"] == "error"
    assert result["error"] == "appointment_not_found"


async def test_multiple_appointments_found(db, tomorrow, day_after):
    pool, doctor_a_id, doctor_b_id = db
    await _book_directly(pool, doctor_a_id, tomorrow, "09:00", "Ramesh Kumar", "9876543210")
    await _book_directly(pool, doctor_b_id, day_after, "16:00", "Ramesh Kumar", "9876543210")

    result = await cancel_appointment.run(pool, "call-1", {"phone_number": "9876543210"})
    assert result["status"] == "error"
    assert result["error"] == "multiple_appointments_found"
    assert len(result["options"]) == 2


async def test_cancel_disambiguated_by_patient_name(db, tomorrow, day_after):
    pool, doctor_a_id, doctor_b_id = db
    await _book_directly(pool, doctor_a_id, tomorrow, "09:00", "Ramesh Kumar", "9876543210")
    await _book_directly(pool, doctor_b_id, day_after, "16:00", "Someone Else", "9876543210")

    result = await cancel_appointment.run(
        pool, "call-1", {"phone_number": "9876543210", "patient_name": "Ramesh"}
    )
    assert result["status"] == "needs_confirmation"
    assert result["readback"]["patient_name"] == "Ramesh Kumar"


async def test_idempotent_replay(db, tomorrow):
    pool, doctor_a_id, _ = db
    await _book_directly(pool, doctor_a_id, tomorrow, "09:00", "Ramesh Kumar", "9876543210")

    first = await cancel_appointment.run(pool, "call-1", {"phone_number": "9876543210"})
    confirm_args = {
        "phone_number": "9876543210",
        "confirmed": True,
        "confirmation_token": first["confirmation_token"],
    }

    result_1 = await cancel_appointment.run(pool, "call-1", confirm_args)
    result_2 = await cancel_appointment.run(pool, "call-1", confirm_args)
    assert result_1["status"] == "ok"
    assert result_2["status"] == "ok"
