"""get_clinic_info: structured version of the facts already in the system
prompt (prompts/v1.md). Having it as a real tool means answers are
measurable/loggable in the Phase 3 eval, and doctor/fee data stays backed
by the DB (one source of truth with db/seed.py) instead of duplicated."""
from typing import Literal

from pydantic import BaseModel, Field

from tools.clinic_data import CLINIC_HOURS, CLINIC_LOCATION, CLINIC_NAME

NAME = "get_clinic_info"
DESCRIPTION = "Get clinic hours, location, doctor list, or consultation fees."


class GetClinicInfoInput(BaseModel):
    topic: Literal["hours", "location", "doctors", "fees", "general"] = Field(
        default="general", description="Which fact to look up."
    )


async def run(pool, call_id: str, args: dict) -> dict:
    del call_id
    input_data = GetClinicInfoInput.model_validate(args)

    async with pool.acquire() as conn:
        doctors = await conn.fetch("SELECT name, specialty, consultation_fee FROM doctors ORDER BY id")

    doctor_list = [
        {"name": d["name"], "specialty": d["specialty"], "consultation_fee": float(d["consultation_fee"])}
        for d in doctors
    ]

    all_data = {
        "clinic_name": CLINIC_NAME,
        "hours": CLINIC_HOURS,
        "location": CLINIC_LOCATION,
        "doctors": doctor_list,
        "fees": [{"doctor_name": d["name"], "consultation_fee": d["consultation_fee"]} for d in doctor_list],
    }
    data = all_data if input_data.topic == "general" else {input_data.topic: all_data[input_data.topic]}

    return {"status": "ok", "message": "Clinic info retrieved.", "data": data}
