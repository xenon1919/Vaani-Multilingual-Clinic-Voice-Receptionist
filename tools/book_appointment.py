"""book_appointment: the two-step confirmation protocol in full.

Call 1 (confirmed=False): validate everything, reserve the slot with a
pending_confirmations row, return a token + readback for the agent to
speak back to the caller.

Call 2 (confirmed=True, confirmation_token=<token>): re-validate against
the token's stored payload (not whatever the model resends, to prevent
drift) and actually insert the appointment. The slots/appointments unique
constraints are the real race-safety net; a UniqueViolation here just
means someone else won the race, so we return slot_taken like normal.

If settings.require_confirmation is False (the Phase 4 ablation), this
behaves as if confirmed=True on the very first call.
"""
import asyncpg
from pydantic import BaseModel, Field

from app.config import settings
from tools.clinic_data import find_doctor
from tools.confirmation import (
    create_pending_confirmation,
    find_alternative_slots,
    get_valid_pending_confirmation,
    is_slot_available,
    mark_confirmation_consumed,
)
from tools.validation import ToolValidationError, normalize_phone, parse_date, parse_time

NAME = "book_appointment"
DESCRIPTION = (
    "Book a new appointment. First call with confirmed=false to get a "
    "confirmation_token and read the date/time/patient name back to the "
    "caller. Only call again with confirmed=true and that same token after "
    "the caller has explicitly agreed."
)


class BookAppointmentInput(BaseModel):
    doctor_name: str = Field(description="Doctor's name, e.g. 'Dr. Lakshmi Rao' or just 'Mehta'.")
    date: str = Field(description="ISO date, YYYY-MM-DD. Resolve relative terms yourself.")
    time: str = Field(description="24-hour HH:MM.")
    patient_name: str = Field(description="Patient's full name.")
    phone_number: str = Field(description="Caller's phone number, digits however spoken.")
    confirmed: bool = Field(
        default=False, description="True only after reading back the details and the caller agreed."
    )
    confirmation_token: str | None = Field(
        default=None, description="The token from the first (confirmed=false) call."
    )


async def _validate_and_resolve(conn, data: BookAppointmentInput):
    """Returns (doctor_row, parsed_date, parsed_time, phone) or raises ToolValidationError."""
    phone = normalize_phone(data.phone_number)
    parsed_date = parse_date(data.date)
    parsed_time = parse_time(data.time)
    doctor = await find_doctor(conn, data.doctor_name)
    return doctor, parsed_date, parsed_time, phone


async def _insert_appointment(conn, slot_id: int, patient_name: str, phone: str) -> int:
    try:
        async with conn.transaction():
            appointment_id = await conn.fetchval(
                """
                INSERT INTO appointments (slot_id, patient_name, phone_number, status)
                VALUES ($1, $2, $3, 'booked')
                RETURNING id
                """,
                slot_id,
                patient_name,
                phone,
            )
            await conn.execute("UPDATE slots SET is_booked = TRUE WHERE id = $1", slot_id)
    except asyncpg.UniqueViolationError:
        return None
    return appointment_id


async def run(pool, call_id: str, args: dict) -> dict:
    input_data = BookAppointmentInput.model_validate(args)
    skip_confirmation = not settings.require_confirmation

    async with pool.acquire() as conn:
        # --- Step 2: confirming a previously-held booking ---
        if input_data.confirmed and input_data.confirmation_token and not skip_confirmation:
            pending = await get_valid_pending_confirmation(conn, input_data.confirmation_token, NAME)
            if pending is None:
                return {
                    "status": "error",
                    "error": "confirmation_expired",
                    "message": "That confirmation has expired. Please collect the details again.",
                }
            if pending["result_appointment_id"] is not None:
                return await _success_payload(conn, pending["result_appointment_id"])

            payload = pending["payload"]
            slot_id = pending["slot_id"]
            if not await is_slot_available(conn, slot_id, excluding_call_id=call_id):
                doctor = await conn.fetchrow("SELECT id, name FROM doctors WHERE id = $1", payload["doctor_id"])
                alternatives = await find_alternative_slots(conn, doctor["id"], parse_date(payload["date"]))
                return {
                    "status": "error",
                    "error": "slot_taken",
                    "message": f"That slot was just taken. Here are other options for {doctor['name']}.",
                    "alternatives": alternatives,
                }

            appointment_id = await _insert_appointment(conn, slot_id, payload["patient_name"], payload["phone"])
            if appointment_id is None:
                return {
                    "status": "error",
                    "error": "slot_taken",
                    "message": "That slot was just taken by someone else.",
                }
            await mark_confirmation_consumed(conn, input_data.confirmation_token, appointment_id)
            return await _success_payload(conn, appointment_id)

        # --- Step 1 (or ablation: direct execution) ---
        try:
            doctor, parsed_date, parsed_time, phone = await _validate_and_resolve(conn, input_data)
        except ToolValidationError as exc:
            return {"status": "error", "error": exc.error_code, "message": exc.message}

        slot = await conn.fetchrow(
            "SELECT id FROM slots WHERE doctor_id = $1 AND slot_date = $2 AND slot_time = $3",
            doctor["id"],
            parsed_date,
            parsed_time,
        )
        if slot is None:
            return {
                "status": "error",
                "error": "slot_not_found",
                "message": f"{doctor['name']} has no {input_data.time} slot on {input_data.date}.",
            }

        if not await is_slot_available(conn, slot["id"], excluding_call_id=call_id):
            alternatives = await find_alternative_slots(conn, doctor["id"], parsed_date)
            return {
                "status": "error",
                "error": "slot_taken",
                "message": f"{doctor['name']} is already booked at {input_data.time} on {input_data.date}.",
                "alternatives": alternatives,
            }

        if skip_confirmation:
            appointment_id = await _insert_appointment(conn, slot["id"], input_data.patient_name, phone)
            if appointment_id is None:
                return {"status": "error", "error": "slot_taken", "message": "That slot was just taken."}
            return await _success_payload(conn, appointment_id)

        payload = {
            "doctor_id": doctor["id"],
            "date": input_data.date,
            "time": input_data.time,
            "patient_name": input_data.patient_name,
            "phone": phone,
        }
        token = await create_pending_confirmation(conn, call_id, NAME, payload, slot_id=slot["id"])
        return {
            "status": "needs_confirmation",
            "confirmation_token": token,
            "message": "Read back the patient name, doctor, date, and time, and ask the caller to confirm.",
            "readback": {
                "patient_name": input_data.patient_name,
                "doctor_name": doctor["name"],
                "date": input_data.date,
                "time": input_data.time,
            },
        }


async def _success_payload(conn, appointment_id: int) -> dict:
    row = await conn.fetchrow(
        """
        SELECT a.id, a.patient_name, a.phone_number, s.slot_date, s.slot_time, d.name AS doctor_name
        FROM appointments a
        JOIN slots s ON s.id = a.slot_id
        JOIN doctors d ON d.id = s.doctor_id
        WHERE a.id = $1
        """,
        appointment_id,
    )
    return {
        "status": "ok",
        "message": f"Booked {row['patient_name']} with {row['doctor_name']} on {row['slot_date']} at {row['slot_time']}.",
        "appointment": {
            "appointment_id": row["id"],
            "patient_name": row["patient_name"],
            "doctor_name": row["doctor_name"],
            "date": row["slot_date"].isoformat(),
            "time": row["slot_time"].strftime("%H:%M"),
        },
    }
