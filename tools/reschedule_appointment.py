"""reschedule_appointment: find the existing booking (by phone), validate
the new slot, confirm, then atomically free the old slot and book the new
one. Same two-step confirmation protocol as the other write tools."""
import asyncpg
from pydantic import BaseModel, Field

from app.config import settings
from tools.appointment_lookup import find_booked_appointment
from tools.clinic_data import find_doctor
from tools.confirmation import (
    create_pending_confirmation,
    find_alternative_slots,
    get_valid_pending_confirmation,
    is_slot_available,
    mark_confirmation_consumed,
)
from tools.validation import ToolValidationError, parse_date, parse_time

NAME = "reschedule_appointment"
DESCRIPTION = (
    "Move an existing appointment (looked up by phone number) to a new "
    "date/time, optionally with a different doctor. First call with "
    "confirmed=false to get a confirmation_token and read the old and new "
    "times back to the caller. Only call again with confirmed=true and "
    "that same token after the caller has agreed."
)


class RescheduleAppointmentInput(BaseModel):
    phone_number: str = Field(description="Caller's phone number, digits however spoken.")
    patient_name: str | None = Field(
        default=None, description="Patient name, to disambiguate if the phone number has multiple appointments."
    )
    new_doctor_name: str | None = Field(
        default=None, description="New doctor, if changing. Omit to keep the same doctor."
    )
    new_date: str = Field(description="ISO date, YYYY-MM-DD. Resolve relative terms yourself.")
    new_time: str = Field(description="24-hour HH:MM.")
    confirmed: bool = Field(
        default=False, description="True only after reading back old and new date/time and the caller agreed."
    )
    confirmation_token: str | None = Field(
        default=None, description="The token from the first (confirmed=false) call."
    )


async def _apply_reschedule(conn, old_appointment_id: int, old_slot_id: int, new_slot_id: int, patient_name: str, phone: str) -> int:
    async with conn.transaction():
        await conn.execute(
            "UPDATE appointments SET status = 'rescheduled', updated_at = now() WHERE id = $1 AND status = 'booked'",
            old_appointment_id,
        )
        await conn.execute("UPDATE slots SET is_booked = FALSE WHERE id = $1", old_slot_id)
        try:
            new_appointment_id = await conn.fetchval(
                """
                INSERT INTO appointments (slot_id, patient_name, phone_number, status)
                VALUES ($1, $2, $3, 'booked')
                RETURNING id
                """,
                new_slot_id,
                patient_name,
                phone,
            )
        except asyncpg.UniqueViolationError:
            return None
        await conn.execute("UPDATE slots SET is_booked = TRUE WHERE id = $1", new_slot_id)
    return new_appointment_id


async def run(pool, call_id: str, args: dict) -> dict:
    input_data = RescheduleAppointmentInput.model_validate(args)
    skip_confirmation = not settings.require_confirmation

    async with pool.acquire() as conn:
        if input_data.confirmed and input_data.confirmation_token and not skip_confirmation:
            pending = await get_valid_pending_confirmation(conn, input_data.confirmation_token, NAME)
            if pending is None:
                return {
                    "status": "error",
                    "error": "confirmation_expired",
                    "message": "That confirmation has expired. Please start the reschedule again.",
                }
            if pending["result_appointment_id"] is not None:
                return await _success_payload(conn, pending["result_appointment_id"])

            payload = pending["payload"]
            if not await is_slot_available(conn, payload["new_slot_id"], excluding_call_id=call_id):
                alternatives = await find_alternative_slots(conn, payload["new_doctor_id"], parse_date(payload["new_date"]))
                return {
                    "status": "error",
                    "error": "slot_taken",
                    "message": "That new slot was just taken. Here are other options.",
                    "alternatives": alternatives,
                }

            new_id = await _apply_reschedule(
                conn,
                payload["old_appointment_id"],
                payload["old_slot_id"],
                payload["new_slot_id"],
                payload["patient_name"],
                payload["phone"],
            )
            if new_id is None:
                return {"status": "error", "error": "slot_taken", "message": "That new slot was just taken."}
            await mark_confirmation_consumed(conn, input_data.confirmation_token, new_id)
            return await _success_payload(conn, new_id)

        try:
            existing = await find_booked_appointment(conn, input_data.phone_number, input_data.patient_name)
            new_date = parse_date(input_data.new_date)
            new_time = parse_time(input_data.new_time)
            new_doctor = (
                await find_doctor(conn, input_data.new_doctor_name)
                if input_data.new_doctor_name
                else {"id": existing["doctor_id"], "name": existing["doctor_name"]}
            )
        except ToolValidationError as exc:
            return {"status": "error", "error": exc.error_code, "message": exc.message, **exc.data}

        new_slot = await conn.fetchrow(
            "SELECT id FROM slots WHERE doctor_id = $1 AND slot_date = $2 AND slot_time = $3",
            new_doctor["id"],
            new_date,
            new_time,
        )
        if new_slot is None:
            return {
                "status": "error",
                "error": "slot_not_found",
                "message": f"{new_doctor['name']} has no {input_data.new_time} slot on {input_data.new_date}.",
            }

        if not await is_slot_available(conn, new_slot["id"], excluding_call_id=call_id):
            alternatives = await find_alternative_slots(conn, new_doctor["id"], new_date)
            return {
                "status": "error",
                "error": "slot_taken",
                "message": f"{new_doctor['name']} is already booked at {input_data.new_time} on {input_data.new_date}.",
                "alternatives": alternatives,
            }

        payload = {
            "old_appointment_id": existing["appointment_id"],
            "old_slot_id": existing["slot_id"],
            "old_doctor_name": existing["doctor_name"],
            "old_date": existing["slot_date"].isoformat(),
            "old_time": existing["slot_time"].strftime("%H:%M"),
            "new_slot_id": new_slot["id"],
            "new_doctor_id": new_doctor["id"],
            "new_doctor_name": new_doctor["name"],
            "new_date": input_data.new_date,
            "new_time": input_data.new_time,
            "patient_name": existing["patient_name"],
            "phone": existing["phone_number"],
        }

        if skip_confirmation:
            new_id = await _apply_reschedule(
                conn, payload["old_appointment_id"], payload["old_slot_id"], payload["new_slot_id"],
                payload["patient_name"], payload["phone"],
            )
            if new_id is None:
                return {"status": "error", "error": "slot_taken", "message": "That new slot was just taken."}
            return await _success_payload(conn, new_id)

        token = await create_pending_confirmation(conn, call_id, NAME, payload, slot_id=new_slot["id"])
        return {
            "status": "needs_confirmation",
            "confirmation_token": token,
            "message": "Read back the old and new date/time/doctor, and ask the caller to confirm the change.",
            "readback": {
                "patient_name": payload["patient_name"],
                "from": {"doctor_name": payload["old_doctor_name"], "date": payload["old_date"], "time": payload["old_time"]},
                "to": {"doctor_name": payload["new_doctor_name"], "date": payload["new_date"], "time": payload["new_time"]},
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
        "message": f"Rescheduled {row['patient_name']} to {row['doctor_name']} on {row['slot_date']} at {row['slot_time']}.",
        "appointment": {
            "appointment_id": row["id"],
            "patient_name": row["patient_name"],
            "doctor_name": row["doctor_name"],
            "date": row["slot_date"].isoformat(),
            "time": row["slot_time"].strftime("%H:%M"),
        },
    }
