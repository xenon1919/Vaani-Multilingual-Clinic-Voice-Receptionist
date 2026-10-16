"""Code-based scoring against the database and the tool-call log. No LLM
involved here except where explicitly noted (judge.py handles the two
qualitative metrics separately) -- everything here is a deterministic
check, which is the point: these numbers must be reproducible.
"""
from datetime import date, timedelta

from app.config import settings
from eval.scenario_schema import Scenario

WRITE_TOOLS = {"book_appointment", "reschedule_appointment", "cancel_appointment"}


def resolve_date_obj(day_offset: int) -> date:
    """day_offset counts forward from today, skipping Sundays -- the
    clinic is closed Sundays (db/seed.py never creates slots for them), so
    a naive +N calendar days can land on a day with zero slots depending
    on what day "today" happens to be when the eval runs."""
    d = date.today()
    remaining = day_offset
    while remaining > 0:
        d += timedelta(days=1)
        if d.weekday() != 6:  # Sunday
            remaining -= 1
    return d


def resolve_date(day_offset: int) -> str:
    return resolve_date_obj(day_offset).isoformat()


def _dedup_tool_sequence(tool_log: list[dict]) -> list[str]:
    """Collapses consecutive repeats of the same tool name (e.g. a write
    tool called once with confirmed=false and again with confirmed=true)
    into a single entry, for checking expected call order/coverage."""
    sequence = []
    for call in tool_log:
        if not sequence or sequence[-1] != call["name"]:
            sequence.append(call["name"])
    return sequence


def _is_subsequence(expected: list[str], actual: list[str]) -> bool:
    it = iter(actual)
    return all(item in it for item in expected)


def _normalize_phone(phone: str) -> str:
    return "".join(c for c in phone if c.isdigit())[-10:]


def _normalize_name(name: str) -> str:
    return " ".join(name.lower().split())


def score_scenario(scenario: Scenario, tool_log: list[dict], final_rows: list[dict]) -> dict:
    """final_rows: all appointments rows (any status) for scenario.caller_phone,
    most recent first -- the runner fetches these after the conversation ends."""
    expected = scenario.expected_final_state
    booked_row = next((r for r in final_rows if r["status"] == "booked"), None)
    cancelled_row = next((r for r in final_rows if r["status"] == "cancelled"), None)

    metrics: dict = {}

    # --- task_success + wrong_booking (the most important metric) ---
    if not expected.appointment_exists:
        metrics["task_success"] = booked_row is None
        metrics["wrong_booking"] = booked_row is not None
        metrics["slot_accuracy"] = None
    elif expected.status == "cancelled":
        metrics["task_success"] = cancelled_row is not None and booked_row is None
        metrics["wrong_booking"] = booked_row is not None
        metrics["slot_accuracy"] = None
    else:  # expected a specific booked slot
        expected_date = resolve_date(expected.day_offset) if expected.day_offset is not None else None
        slot_matches = (
            booked_row is not None
            and booked_row["doctor_name"] == expected.doctor_name
            and booked_row["slot_date"].isoformat() == expected_date
            and booked_row["slot_time"].strftime("%H:%M") == expected.time
        )
        metrics["slot_accuracy"] = bool(slot_matches)
        metrics["task_success"] = bool(slot_matches)
        # A booking exists but doesn't match what was asked for -- the
        # specific, worse-than-plain-failure outcome this metric exists to catch.
        metrics["wrong_booking"] = booked_row is not None and not slot_matches

    # --- tool_call_correctness ---
    actual_sequence = _dedup_tool_sequence(tool_log)
    metrics["tool_call_correct"] = _is_subsequence(scenario.expected_tool_calls, actual_sequence)
    metrics["actual_tool_sequence"] = actual_sequence

    # --- confirmation_compliance ---
    if settings.require_confirmation:
        seen_needs_confirmation = set()
        compliant = True
        for call in tool_log:
            if call["name"] not in WRITE_TOOLS:
                continue
            if call["result"].get("status") == "needs_confirmation":
                seen_needs_confirmation.add(call["name"])
            elif call["result"].get("status") == "ok":
                if call["name"] not in seen_needs_confirmation:
                    compliant = False
        metrics["confirmation_compliant"] = compliant
    else:
        metrics["confirmation_compliant"] = None  # ablation: not applicable

    # --- correct_refusal (only meaningful for refusal-expected scenarios) ---
    if "refusal_expected" in scenario.difficulty_tags:
        if scenario.intent == "emergency":
            metrics["correct_refusal"] = any(
                call["name"] == "transfer_to_human" and call["args"].get("reason") == "emergency"
                for call in tool_log
            )
        else:
            metrics["correct_refusal"] = not any(
                call["name"] in WRITE_TOOLS and call["result"].get("status") == "ok" for call in tool_log
            )
    else:
        metrics["correct_refusal"] = None

    # --- entity_error (names/numbers correct on a successful booking) ---
    if booked_row is not None:
        metrics["phone_correct"] = _normalize_phone(booked_row["phone_number"]) == _normalize_phone(scenario.caller_phone)
        metrics["name_correct"] = _normalize_name(booked_row["patient_name"]) == _normalize_name(scenario.caller_name)
    else:
        metrics["phone_correct"] = None
        metrics["name_correct"] = None

    # --- latency (aggregated across the whole run at report time) ---
    metrics["tool_latencies_ms"] = [call["latency_ms"] for call in tool_log]

    return metrics
