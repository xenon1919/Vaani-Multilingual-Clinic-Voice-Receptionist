from datetime import date as date_cls

from tools import check_availability


async def test_available_slot(db, tomorrow):
    pool, doctor_a_id, _ = db
    result = await check_availability.run(
        pool, "call-1", {"doctor_name": "Test Alpha", "date": tomorrow, "time": "09:00"}
    )
    assert result["status"] == "ok"
    assert result["available"] is True


async def test_unavailable_slot_offers_alternatives(db, tomorrow):
    pool, doctor_a_id, _ = db
    row = await pool.fetchrow(
        "SELECT id FROM slots WHERE doctor_id=$1 AND slot_date=$2 AND slot_time='09:00'",
        doctor_a_id, date_cls.fromisoformat(tomorrow),
    )
    await pool.execute(
        "INSERT INTO appointments (slot_id, patient_name, phone_number, status) VALUES ($1,$2,$3,'booked')",
        row["id"], "Someone Else", "9876543210",
    )

    result = await check_availability.run(
        pool, "call-1", {"doctor_name": "Test Alpha", "date": tomorrow, "time": "09:00"}
    )
    assert result["status"] == "ok"
    assert result["available"] is False
    assert len(result["alternatives"]) > 0


async def test_lists_open_slots_when_no_time_given(db, tomorrow):
    pool, _, _ = db
    result = await check_availability.run(pool, "call-1", {"doctor_name": "Test Alpha", "date": tomorrow})
    assert result["status"] == "ok"
    assert set(result["open_times"]) == {"09:00", "09:30", "16:00", "16:30"}


async def test_past_date_rejected(db, yesterday):
    pool, _, _ = db
    result = await check_availability.run(
        pool, "call-1", {"doctor_name": "Test Alpha", "date": yesterday, "time": "09:00"}
    )
    assert result["status"] == "error"
    assert result["error"] == "past_date"


async def test_doctor_not_found(db, tomorrow):
    pool, _, _ = db
    result = await check_availability.run(
        pool, "call-1", {"doctor_name": "Nobody", "date": tomorrow, "time": "09:00"}
    )
    assert result["status"] == "error"
    assert result["error"] == "doctor_not_found"


async def test_slot_not_found_for_unscheduled_time(db, tomorrow):
    pool, _, _ = db
    result = await check_availability.run(
        pool, "call-1", {"doctor_name": "Test Alpha", "date": tomorrow, "time": "23:45"}
    )
    assert result["status"] == "error"
    assert result["error"] == "slot_not_found"
