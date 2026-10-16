from datetime import date as date_cls, time as time_cls

from tools import reschedule_appointment


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


async def test_reschedule_flow(db, tomorrow, day_after):
    pool, doctor_a_id, _ = db
    _, old_slot_id = await _book_directly(pool, doctor_a_id, tomorrow, "09:00", "Ramesh Kumar", "9876543210")

    first = await reschedule_appointment.run(
        pool, "call-1", {"phone_number": "9876543210", "new_date": day_after, "new_time": "16:00"}
    )
    assert first["status"] == "needs_confirmation"

    second = await reschedule_appointment.run(
        pool, "call-1",
        {
            "phone_number": "9876543210", "new_date": day_after, "new_time": "16:00",
            "confirmed": True, "confirmation_token": first["confirmation_token"],
        },
    )
    assert second["status"] == "ok"
    assert second["appointment"]["date"] == day_after
    assert second["appointment"]["time"] == "16:00"

    old_slot = await pool.fetchrow("SELECT is_booked FROM slots WHERE id = $1", old_slot_id)
    assert old_slot["is_booked"] is False

    old_appt = await pool.fetchrow(
        "SELECT status FROM appointments WHERE slot_id = $1", old_slot_id
    )
    assert old_appt["status"] == "rescheduled"


async def test_reschedule_to_taken_slot(db, tomorrow, day_after):
    pool, doctor_a_id, _ = db
    await _book_directly(pool, doctor_a_id, tomorrow, "09:00", "Ramesh Kumar", "9876543210")
    await _book_directly(pool, doctor_a_id, day_after, "16:00", "Other Patient", "9123456780")

    result = await reschedule_appointment.run(
        pool, "call-1", {"phone_number": "9876543210", "new_date": day_after, "new_time": "16:00"}
    )
    assert result["status"] == "error"
    assert result["error"] == "slot_taken"


async def test_reschedule_appointment_not_found(db, tomorrow):
    pool, _, _ = db
    result = await reschedule_appointment.run(
        pool, "call-1", {"phone_number": "9876543210", "new_date": tomorrow, "new_time": "09:00"}
    )
    assert result["status"] == "error"
    assert result["error"] == "appointment_not_found"


async def test_reschedule_to_different_doctor(db, tomorrow, day_after):
    pool, doctor_a_id, _ = db
    await _book_directly(pool, doctor_a_id, tomorrow, "09:00", "Ramesh Kumar", "9876543210")

    first = await reschedule_appointment.run(
        pool, "call-1",
        {
            "phone_number": "9876543210", "new_doctor_name": "Test Beta",
            "new_date": day_after, "new_time": "16:00",
        },
    )
    assert first["status"] == "needs_confirmation"
    assert first["readback"]["to"]["doctor_name"] == "Dr. Test Beta"


async def test_idempotent_replay(db, tomorrow, day_after):
    pool, doctor_a_id, _ = db
    await _book_directly(pool, doctor_a_id, tomorrow, "09:00", "Ramesh Kumar", "9876543210")

    first = await reschedule_appointment.run(
        pool, "call-1", {"phone_number": "9876543210", "new_date": day_after, "new_time": "16:00"}
    )
    confirm_args = {
        "phone_number": "9876543210", "new_date": day_after, "new_time": "16:00",
        "confirmed": True, "confirmation_token": first["confirmation_token"],
    }
    result_1 = await reschedule_appointment.run(pool, "call-1", confirm_args)
    result_2 = await reschedule_appointment.run(pool, "call-1", confirm_args)
    assert result_1["status"] == "ok"
    assert result_2["status"] == "ok"
    assert result_1["appointment"]["appointment_id"] == result_2["appointment"]["appointment_id"]
