"""cancel_appointment: same two-step confirmation protocol as
book_appointment, but frees a slot rather than claiming one, so no
pending_confirmations.slot_id reservation is needed."""
from pydantic import BaseModel, Field

from app.config import settings
from tools.appointment_lookup import find_booked_appointment
from tools.confirmation import create_pending_confirmation, get_valid_pending_confirmation, mark_confirmation_consumed
from tools.validation import ToolValidationError

NAME = "cancel_appointment"
DESCRIPTION = (
    "Cancel an existing appointment, looked up by the caller's phone number. "
    "First call with confirmed=false to get a confirmation_token and read "
    "the appointment back to the caller. Only call again with "
    "confirmed=true and that same token after the caller has agreed."
)


class CancelAppointmentInput(BaseModel):
    phone_number: str = Field(description="Caller's phone number, digits however spoken.")
    patient_name: str | None = Field(
        default=None, description="Patient name, to disambiguate if the phone number has multiple appointments."
    )
    confirmed: bool = Field(
        default=False, description="True only after reading back the appointment and the caller agreed."
    )
    confirmation_token: str | None = Field(
        default=None, description="The token from the first (confirmed=false) call."
    )


async def _cancel(conn, appointment_id: int, slot_id: int) -> None:
    await conn.execute(
        "UPDATE appointments SET status = 'cancelled', updated_at = now() WHERE id = $1 AND status = 'booked'",
        appointment_id,
    )
    await conn.execute("UPDATE slots SET is_booked = FALSE WHERE id = $1", slot_id)


async def run(pool, call_id: str, args: dict) -> dict:
    input_data = CancelAppointmentInput.model_validate(args)
    skip_confirmation = not settings.require_confirmation

    async with pool.acquire() as conn:
        if input_data.confirmed and input_data.confirmation_token and not skip_confirmation:
            pending = await get_valid_pending_confirmation(conn, input_data.confirmation_token, NAME)
            if pending is None:
                return {
                    "status": "error",
                    "error": "confirmation_expired",
                    "message": "That confirmation has expired. Please look up the appointment again.",
                }
            if pending["result_appointment_id"] is not None:
                return _success_payload(pending["payload"], pending["result_appointment_id"])

            payload = pending["payload"]
            async with conn.transaction():
                await _cancel(conn, payload["appointment_id"], payload["slot_id"])
            await mark_confirmation_consumed(conn, input_data.confirmation_token, payload["appointment_id"])
            return _success_payload(payload, payload["appointment_id"])

        try:
            appointment = await find_booked_appointment(conn, input_data.phone_number, input_data.patient_name)
        except ToolValidationError as exc:
            return {"status": "error", "error": exc.error_code, "message": exc.message, **exc.data}

        payload = {
            "appointment_id": appointment["appointment_id"],
            "slot_id": appointment["slot_id"],
            "patient_name": appointment["patient_name"],
            "doctor_name": appointment["doctor_name"],
            "date": appointment["slot_date"].isoformat(),
            "time": appointment["slot_time"].strftime("%H:%M"),
        }

        if skip_confirmation:
            async with conn.transaction():
                await _cancel(conn, payload["appointment_id"], payload["slot_id"])
            return _success_payload(payload, payload["appointment_id"])

        token = await create_pending_confirmation(conn, call_id, NAME, payload, slot_id=None)
        return {
            "status": "needs_confirmation",
            "confirmation_token": token,
            "message": "Read back the patient name, doctor, date, and time, and ask the caller to confirm the cancellation.",
            "readback": {
                "patient_name": payload["patient_name"],
                "doctor_name": payload["doctor_name"],
                "date": payload["date"],
                "time": payload["time"],
            },
        }


def _success_payload(payload: dict, appointment_id: int) -> dict:
    return {
        "status": "ok",
        "message": f"Cancelled {payload['patient_name']}'s appointment with {payload['doctor_name']} on {payload['date']} at {payload['time']}.",
        "appointment": {"appointment_id": appointment_id, **payload},
    }
