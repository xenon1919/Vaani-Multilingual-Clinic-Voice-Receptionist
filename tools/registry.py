"""Maps tool name -> (Pydantic input model, async implementation) and
builds the Gemini Live function declarations from each model's own JSON
schema (confirmed working directly against the real API — no hand-written
schema conversion needed)."""
from google.genai import types

from tools import (
    book_appointment,
    cancel_appointment,
    check_availability,
    get_clinic_info,
    reschedule_appointment,
    transfer_to_human,
)

_MODULES = [
    check_availability,
    book_appointment,
    reschedule_appointment,
    cancel_appointment,
    get_clinic_info,
    transfer_to_human,
]

_INPUT_MODELS = {
    check_availability.NAME: check_availability.CheckAvailabilityInput,
    book_appointment.NAME: book_appointment.BookAppointmentInput,
    reschedule_appointment.NAME: reschedule_appointment.RescheduleAppointmentInput,
    cancel_appointment.NAME: cancel_appointment.CancelAppointmentInput,
    get_clinic_info.NAME: get_clinic_info.GetClinicInfoInput,
    transfer_to_human.NAME: transfer_to_human.TransferToHumanInput,
}

_RUNNERS = {module.NAME: module.run for module in _MODULES}


def build_gemini_tools() -> list[types.Tool]:
    declarations = [
        types.FunctionDeclaration(
            name=module.NAME,
            description=module.DESCRIPTION,
            parameters_json_schema=_INPUT_MODELS[module.NAME].model_json_schema(),
        )
        for module in _MODULES
    ]
    return [types.Tool(function_declarations=declarations)]


async def dispatch(pool, call_id: str, name: str, args: dict) -> dict:
    runner = _RUNNERS.get(name)
    if runner is None:
        return {"status": "error", "error": "unknown_tool", "message": f"No such tool: {name}"}
    try:
        return await runner(pool, call_id, args)
    except Exception as exc:  # noqa: BLE001 — a bad tool call must not crash the whole session
        return {"status": "error", "error": "internal_error", "message": str(exc)}
