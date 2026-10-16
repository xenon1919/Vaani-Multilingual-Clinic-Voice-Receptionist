"""Shared per-scenario orchestration used by both the text and audio
eval tiers: reset DB state for the scenario, hand off to a tier-specific
`drive_conversation` callback to actually run the conversation, then
score against the DB/tool log and persist the result. One bad scenario
is caught and recorded as an error row rather than killing the batch.
"""
from datetime import time
from typing import Awaitable, Callable

import asyncpg
from google import genai

from eval.db_eval import save_result
from eval.judge import judge_conversation
from eval.scenario_schema import Scenario
from eval.scoring import resolve_date_obj, score_scenario

DriveFn = Callable[[Scenario], Awaitable[tuple[list[dict], list[dict], dict]]]


async def _reset_and_setup(pool: asyncpg.Pool, scenario: Scenario) -> None:
    async with pool.acquire() as conn:
        # pending_confirmations.result_appointment_id references appointments
        # with no cascade, so a rerun of the same scenario (same phone number)
        # must clear those first or the delete below violates the FK.
        await conn.execute(
            "DELETE FROM pending_confirmations WHERE result_appointment_id IN "
            "(SELECT id FROM appointments WHERE phone_number = $1)",
            scenario.caller_phone,
        )
        await conn.execute("DELETE FROM appointments WHERE phone_number = $1", scenario.caller_phone)
        if scenario.setup is not None:
            setup = scenario.setup
            slot_date = resolve_date_obj(setup.day_offset)
            slot_time = time.fromisoformat(setup.time)
            slot = await conn.fetchrow(
                """
                SELECT s.id FROM slots s JOIN doctors d ON d.id = s.doctor_id
                WHERE d.name = $1 AND s.slot_date = $2 AND s.slot_time = $3
                """,
                setup.doctor_name, slot_date, slot_time,
            )
            if slot is None:
                raise RuntimeError(f"setup slot not found for scenario {scenario.id}: {setup}")
            await conn.execute(
                "INSERT INTO appointments (slot_id, patient_name, phone_number, status) VALUES ($1,$2,$3,'booked')",
                slot["id"], setup.patient_name, setup.phone_number,
            )
            await conn.execute("UPDATE slots SET is_booked = TRUE WHERE id = $1", slot["id"])


async def _fetch_final_rows(pool: asyncpg.Pool, phone: str) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT a.patient_name, a.phone_number, a.status, s.slot_date, s.slot_time, d.name AS doctor_name
            FROM appointments a
            JOIN slots s ON s.id = a.slot_id
            JOIN doctors d ON d.id = s.doctor_id
            WHERE a.phone_number = $1
            ORDER BY a.created_at DESC
            """,
            phone,
        )
    return [dict(r) for r in rows]


async def run_scenario(
    client: genai.Client,
    pool: asyncpg.Pool,
    scenario: Scenario,
    run_id: str,
    drive_conversation: DriveFn,
) -> dict:
    try:
        await _reset_and_setup(pool, scenario)
        transcript, tool_log, extra_metrics = await drive_conversation(scenario)

        judge_result = await judge_conversation(client, scenario, transcript)
        final_rows = await _fetch_final_rows(pool, scenario.caller_phone)
        metrics = score_scenario(scenario, tool_log, final_rows)
        metrics.update(extra_metrics)

        await save_result(
            pool, run_id, scenario.id, scenario.language, scenario.intent, scenario.difficulty_tags,
            metrics, judge_result, transcript, tool_log,
        )
        return metrics
    except Exception as exc:  # noqa: BLE001 -- one bad scenario must not kill the batch
        await save_result(
            pool, run_id, scenario.id, scenario.language, scenario.intent, scenario.difficulty_tags,
            {}, None, [], [], error=f"{type(exc).__name__}: {exc}",
        )
        return {"error": str(exc)}
