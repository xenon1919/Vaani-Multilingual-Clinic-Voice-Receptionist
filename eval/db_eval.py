"""Persists eval runs/results to Postgres. UNIQUE(run_id, scenario_id) is
what makes a run resumable: pass the same run_id and already-scored
scenarios are skipped instead of re-run."""
import asyncpg


async def start_run(pool: asyncpg.Pool, run_id: str, tier: str, prompt_version: str) -> None:
    await pool.execute(
        """
        INSERT INTO eval_runs (run_id, tier, prompt_version) VALUES ($1, $2, $3)
        ON CONFLICT (run_id) DO NOTHING
        """,
        run_id, tier, prompt_version,
    )


async def finish_run(pool: asyncpg.Pool, run_id: str) -> None:
    await pool.execute("UPDATE eval_runs SET finished_at = now() WHERE run_id = $1", run_id)


async def already_scored(pool: asyncpg.Pool, run_id: str, scenario_id: str) -> bool:
    row = await pool.fetchval(
        "SELECT 1 FROM eval_results WHERE run_id = $1 AND scenario_id = $2", run_id, scenario_id
    )
    return row is not None


async def save_result(
    pool: asyncpg.Pool,
    run_id: str,
    scenario_id: str,
    language: str,
    intent: str,
    difficulty_tags: list[str],
    metrics: dict,
    judge_scores: dict | None,
    transcript: list[dict],
    tool_calls: list[dict],
    error: str | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO eval_results
            (run_id, scenario_id, language, intent, difficulty_tags, metrics, judge_scores, transcript, tool_calls, error)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (run_id, scenario_id) DO UPDATE SET
            metrics = EXCLUDED.metrics, judge_scores = EXCLUDED.judge_scores,
            transcript = EXCLUDED.transcript, tool_calls = EXCLUDED.tool_calls, error = EXCLUDED.error
        """,
        run_id, scenario_id, language, intent, difficulty_tags,
        metrics, judge_scores, transcript, tool_calls, error,
    )
