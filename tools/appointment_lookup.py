"""Shared 'find the caller's existing appointment by phone (+ optional
name)' logic used by cancel_appointment and reschedule_appointment —
there's no login/appointment-ID system, so phone number is the lookup key."""
import asyncpg

from tools.validation import ToolValidationError, normalize_phone


async def find_booked_appointment(
    conn: asyncpg.Connection, phone_number: str, patient_name: str | None
) -> asyncpg.Record:
    """Returns the single matching booked appointment (joined with slot/doctor).
    Raises ToolValidationError(appointment_not_found) if none match, or
    ToolValidationError(multiple_appointments_found, data={"options": [...]})
    if the phone number alone doesn't disambiguate."""
    phone = normalize_phone(phone_number)
    query = """
        SELECT a.id AS appointment_id, a.patient_name, a.phone_number, a.slot_id,
               s.slot_date, s.slot_time, d.id AS doctor_id, d.name AS doctor_name
        FROM appointments a
        JOIN slots s ON s.id = a.slot_id
        JOIN doctors d ON d.id = s.doctor_id
        WHERE a.phone_number = $1 AND a.status = 'booked'
    """
    params: list = [phone]
    if patient_name:
        query += " AND a.patient_name ILIKE $2"
        params.append(f"%{patient_name}%")
    query += " ORDER BY s.slot_date, s.slot_time"

    rows = await conn.fetch(query, *params)
    if not rows:
        suffix = f" and name '{patient_name}'" if patient_name else ""
        raise ToolValidationError(
            "appointment_not_found", f"No booked appointment found for that phone number{suffix}."
        )
    if len(rows) > 1:
        options = [
            {
                "appointment_id": r["appointment_id"],
                "patient_name": r["patient_name"],
                "doctor_name": r["doctor_name"],
                "date": r["slot_date"].isoformat(),
                "time": r["slot_time"].strftime("%H:%M"),
            }
            for r in rows
        ]
        raise ToolValidationError(
            "multiple_appointments_found",
            "That phone number has more than one appointment. Ask the caller which one "
            "(by doctor and date/time) and include their name next time to disambiguate.",
            data={"options": options},
        )
    return rows[0]
