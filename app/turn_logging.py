"""Logs every call and turn to Postgres. Transcript/tool args/results are
masked (names, phone numbers) before they're written — the real values are
only ever used in-memory for the actual tool logic."""
import asyncpg

from tools.masking import mask_dict, mask_transcript


async def start_call(pool: asyncpg.Pool, call_id: str) -> None:
    await pool.execute("INSERT INTO call_logs (call_id) VALUES ($1)", call_id)


async def end_call(pool: asyncpg.Pool, call_id: str, outcome: str) -> None:
    await pool.execute(
        "UPDATE call_logs SET ended_at = now(), outcome = $2 WHERE call_id = $1", call_id, outcome
    )


async def log_turn(
    pool: asyncpg.Pool,
    call_id: str,
    turn_index: int,
    role: str,
    transcript: str | None = None,
    tool_name: str | None = None,
    tool_args: dict | None = None,
    tool_result: dict | None = None,
    latency_ms: int | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO turn_logs (call_id, turn_index, role, transcript, tool_name, tool_args, tool_result, latency_ms)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        call_id,
        turn_index,
        role,
        mask_transcript(transcript) if transcript else None,
        tool_name,
        mask_dict(tool_args) if tool_args else None,
        mask_dict(tool_result) if tool_result else None,
        latency_ms,
    )
