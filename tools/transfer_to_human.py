"""transfer_to_human: no DB write of its own (the call outcome gets
recorded by the generic turn-logging wiring in app/live_session.py). Just
tells the model what to say while handing off."""
from typing import Literal

from pydantic import BaseModel, Field

NAME = "transfer_to_human"
DESCRIPTION = (
    "Hand off the call to a human — use immediately for a medical "
    "emergency, or when the caller explicitly asks for a human, or when "
    "the caller's request can't be resolved with the other tools."
)

_MESSAGES = {
    "emergency": (
        "This sounds like a medical emergency. Please call emergency services or go to the "
        "nearest hospital immediately. I'm transferring you to clinic staff now."
    ),
    "caller_request": "Sure, I'm transferring you to a staff member now. Please hold.",
    "unresolvable": "I'm not able to help with that myself, so I'm transferring you to a staff member now.",
}


class TransferToHumanInput(BaseModel):
    reason: Literal["emergency", "caller_request", "unresolvable"] = Field(
        default="caller_request", description="Why the call is being transferred."
    )
    note: str | None = Field(default=None, description="Brief context for the human who picks up.")


async def run(pool, call_id: str, args: dict) -> dict:
    del pool, call_id
    input_data = TransferToHumanInput.model_validate(args)
    return {
        "status": "ok",
        "message": _MESSAGES[input_data.reason],
        "reason": input_data.reason,
        "note": input_data.note,
    }
