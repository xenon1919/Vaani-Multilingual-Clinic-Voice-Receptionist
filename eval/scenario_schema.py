"""Scenario schema for the Phase 3 eval harness. Dates are relative
(day_offset from "today") so scenarios stay valid no matter when the eval
actually runs; the runner resolves them to real ISO dates against the
seeded 14-day slot window."""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

SCENARIOS_PATH = Path(__file__).resolve().parent / "scenarios.yaml"


class ScenarioSetup(BaseModel):
    """Pre-existing appointment the DB must have before the conversation
    starts -- required for reschedule/cancel scenarios, since those tools
    look the appointment up by phone number."""
    doctor_name: str
    day_offset: int
    time: str
    patient_name: str
    phone_number: str


class ExpectedFinalState(BaseModel):
    appointment_exists: bool
    doctor_name: str | None = None
    day_offset: int | None = None
    time: str | None = None
    status: Literal["booked", "cancelled", "rescheduled"] | None = None


class Scenario(BaseModel):
    id: str
    language: Literal["te", "hi", "en", "mixed"]
    intent: Literal[
        "book", "reschedule", "cancel", "clinic_info",
        "medical_advice", "emergency", "prompt_injection", "mind_change",
    ]
    caller_persona: str
    caller_goal: str
    caller_name: str
    caller_phone: str
    difficulty_tags: list[str] = []
    setup: ScenarioSetup | None = None
    expected_tool_calls: list[str] = []
    expected_final_state: ExpectedFinalState
    audio_subset: bool = False


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[Scenario]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Scenario.model_validate(item) for item in raw]
