"""Prints results tables (per language, per prompt version) and exports a
flat per-scenario CSV for Power BI. Can be run standalone:
    python -m eval.report --run-id text-20261015T120000
    python -m eval.report --csv eval/results.csv
With no --run-id, tables/CSV cover every run stored so far (useful for
comparing prompt versions in Phase 4).
"""
import argparse
import asyncio
import csv
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def fetch_results(pool: asyncpg.Pool, run_id: str | None = None) -> list[dict]:
    query = """
        SELECT er.scenario_id, er.language, er.intent, er.difficulty_tags, er.metrics,
               er.judge_scores, er.error, r.run_id, r.tier, r.prompt_version
        FROM eval_results er
        JOIN eval_runs r ON r.run_id = er.run_id
    """
    params = []
    if run_id:
        query += " WHERE er.run_id = $1"
        params.append(run_id)
    rows = await pool.fetch(query, *params)
    return [dict(r) for r in rows]


def _rate(rows: list[dict], key: str) -> float | None:
    values = [r["metrics"].get(key) for r in rows if r["metrics"].get(key) is not None]
    return sum(1 for v in values if v) / len(values) if values else None


def _entity_error_rate(rows: list[dict]) -> float | None:
    values = []
    for r in rows:
        m = r["metrics"]
        if m.get("phone_correct") is None and m.get("name_correct") is None:
            continue
        values.append(0 if (m.get("phone_correct") and m.get("name_correct")) else 1)
    return sum(values) / len(values) if values else None


def _avg_judge(rows: list[dict], key: str) -> float | None:
    values = [
        r["judge_scores"].get(key)
        for r in rows
        if r.get("judge_scores") and r["judge_scores"].get(key) is not None
    ]
    return sum(values) / len(values) if values else None


def _avg_metric(rows: list[dict], key: str) -> float | None:
    values = [r["metrics"].get(key) for r in rows if r["metrics"].get(key) is not None]
    return sum(values) / len(values) if values else None


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


def aggregate(rows: list[dict]) -> dict:
    latencies = [ms for r in rows for ms in (r["metrics"].get("tool_latencies_ms") or [])]
    return {
        "n": len(rows),
        "errors": sum(1 for r in rows if r.get("error")),
        "task_success": _rate(rows, "task_success"),
        "wrong_booking": _rate(rows, "wrong_booking"),
        "slot_accuracy": _rate(rows, "slot_accuracy"),
        "tool_call_ok": _rate(rows, "tool_call_correct"),
        "confirm_ok": _rate(rows, "confirmation_compliant"),
        "refusal_ok": _rate(rows, "correct_refusal"),
        "entity_err": _entity_error_rate(rows),
        "conv_quality": _avg_judge(rows, "conversation_quality"),
        "refusal_quality": _avg_judge(rows, "refusal_quality"),
        "latency_p50": _percentile(latencies, 0.5),
        "latency_p95": _percentile(latencies, 0.95),
        "ttfa_ms": _avg_metric(rows, "time_to_first_audio_ms"),
    }


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.0%}" if 0 <= value <= 1 else f"{value:.0f}"
    return str(value)


COLUMNS = [
    ("n", "N"), ("errors", "Err"), ("task_success", "Success"), ("wrong_booking", "WrongBook"),
    ("slot_accuracy", "SlotAcc"), ("tool_call_ok", "ToolOK"), ("confirm_ok", "ConfirmOK"),
    ("refusal_ok", "RefusalOK"), ("entity_err", "EntityErr"), ("conv_quality", "ConvQ"),
    ("refusal_quality", "RefQ"), ("latency_p50", "p50ms"), ("latency_p95", "p95ms"), ("ttfa_ms", "TTFAms"),
]


def _print_table(title: str, grouped: dict[str, list[dict]]) -> None:
    print(f"\n{title}")
    header = ["Group"] + [label for _, label in COLUMNS]
    widths = [max(len(h), 10) for h in header]
    print(" | ".join(h.ljust(w) for h, w in zip(header, widths)))
    print("-" * (sum(widths) + 3 * (len(widths) - 1)))
    for group_name, rows in sorted(grouped.items()):
        stats = aggregate(rows)
        cells = [group_name] + [_fmt(stats[key]) for key, _ in COLUMNS]
        print(" | ".join(c.ljust(w) for c, w in zip(cells, widths)))


async def print_report(pool: asyncpg.Pool, run_id: str | None = None) -> None:
    rows = await fetch_results(pool, run_id)
    if not rows:
        print("No results found.")
        return

    by_language: dict[str, list[dict]] = {}
    by_prompt_version: dict[str, list[dict]] = {}
    for r in rows:
        by_language.setdefault(r["language"], []).append(r)
        by_prompt_version.setdefault(r["prompt_version"], []).append(r)

    scope = f"run '{run_id}'" if run_id else "all runs"
    print(f"=== Results by language ({scope}) ===")
    _print_table(f"By language ({scope})", by_language)
    if len(by_prompt_version) > 1 or not run_id:
        _print_table(f"By prompt version ({scope})", by_prompt_version)
    overall = aggregate(rows)
    print(f"\nOverall: {overall}")


async def export_csv(pool: asyncpg.Pool, path: Path, run_id: str | None = None) -> None:
    rows = await fetch_results(pool, run_id)
    fieldnames = [
        "run_id", "tier", "prompt_version", "scenario_id", "language", "intent", "difficulty_tags",
        "error", "task_success", "wrong_booking", "slot_accuracy", "tool_call_correct",
        "confirmation_compliant", "correct_refusal", "phone_correct", "name_correct",
        "conversation_quality", "refusal_quality", "tool_latencies_ms", "time_to_first_audio_ms",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            m = r["metrics"] or {}
            j = r["judge_scores"] or {}
            writer.writerow({
                "run_id": r["run_id"], "tier": r["tier"], "prompt_version": r["prompt_version"],
                "scenario_id": r["scenario_id"], "language": r["language"], "intent": r["intent"],
                "difficulty_tags": ";".join(r["difficulty_tags"] or []), "error": r["error"],
                "task_success": m.get("task_success"), "wrong_booking": m.get("wrong_booking"),
                "slot_accuracy": m.get("slot_accuracy"), "tool_call_correct": m.get("tool_call_correct"),
                "confirmation_compliant": m.get("confirmation_compliant"),
                "correct_refusal": m.get("correct_refusal"), "phone_correct": m.get("phone_correct"),
                "name_correct": m.get("name_correct"),
                "conversation_quality": j.get("conversation_quality"),
                "refusal_quality": j.get("refusal_quality"),
                "tool_latencies_ms": ";".join(str(x) for x in (m.get("tool_latencies_ms") or [])),
                "time_to_first_audio_ms": m.get("time_to_first_audio_ms"),
            })
    print(f"Exported {len(rows)} rows to {path}")


async def _main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    from app.db import close_pool, get_pool, init_pool

    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--csv", default=None, help="Path to export a flat per-scenario CSV.")
    args = parser.parse_args()

    await init_pool()
    pool = get_pool()
    await print_report(pool, args.run_id)
    if args.csv:
        await export_csv(pool, Path(args.csv), args.run_id)
    await close_pool()


if __name__ == "__main__":
    asyncio.run(_main())
