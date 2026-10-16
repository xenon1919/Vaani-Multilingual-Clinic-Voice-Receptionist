"""check_availability: read-only, so no confirmation needed. Looks up one
specific slot, or lists a doctor's open slots for a day if no time is given."""
from pydantic import BaseModel, Field

from tools.clinic_data import find_doctor
from tools.confirmation import find_alternative_slots, is_slot_available
from tools.validation import ToolValidationError, parse_date, parse_time

NAME = "check_availability"
DESCRIPTION = (
    "Check whether a doctor has an open appointment slot on a given date and "
    "(optionally) time. If no time is given, lists the doctor's open slots "
    "for that date. Always use this before book_appointment or "
    "reschedule_appointment, with dates/times resolved to ISO format."
)


class CheckAvailabilityInput(BaseModel):
    doctor_name: str = Field(description="Doctor's name, e.g. 'Dr. Lakshmi Rao' or just 'Mehta'.")
    date: str = Field(description="ISO date, YYYY-MM-DD. Resolve relative terms yourself.")
    time: str | None = Field(
        default=None, description="24-hour HH:MM. Omit to list all open slots that day."
    )


async def run(pool, call_id: str, args: dict) -> dict:
    del call_id
    input_data = CheckAvailabilityInput.model_validate(args)

    async with pool.acquire() as conn:
        try:
            parsed_date = parse_date(input_data.date)
            doctor = await find_doctor(conn, input_data.doctor_name)
        except ToolValidationError as exc:
            return {"status": "error", "error": exc.error_code, "message": exc.message}

        if input_data.time is None:
            rows = await conn.fetch(
                """
                SELECT s.id, s.slot_time
                FROM slots s
                LEFT JOIN appointments a ON a.slot_id = s.id AND a.status = 'booked'
                WHERE s.doctor_id = $1 AND s.slot_date = $2 AND a.id IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM pending_confirmations pc
                      WHERE pc.slot_id = s.id AND pc.expires_at > now()
                  )
                ORDER BY s.slot_time
                """,
                doctor["id"],
                parsed_date,
            )
            open_times = [r["slot_time"].strftime("%H:%M") for r in rows]
            return {
                "status": "ok",
                "message": f"{doctor['name']} has {len(open_times)} open slot(s) on {input_data.date}.",
                "doctor_name": doctor["name"],
                "date": input_data.date,
                "open_times": open_times,
            }

        try:
            parsed_time = parse_time(input_data.time)
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
                "message": f"{doctor['name']} has no {input_data.time} appointment slot on {input_data.date}.",
            }

        available = await is_slot_available(conn, slot["id"])
        if not available:
            alternatives = await find_alternative_slots(conn, doctor["id"], parsed_date)
            return {
                "status": "ok",
                "available": False,
                "message": f"{doctor['name']} is not available at {input_data.time} on {input_data.date}.",
                "alternatives": alternatives,
            }
        return {
            "status": "ok",
            "available": True,
            "message": f"{doctor['name']} is available at {input_data.time} on {input_data.date}.",
        }
