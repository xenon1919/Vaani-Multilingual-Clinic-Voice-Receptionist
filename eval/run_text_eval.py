"""Phase 3 text-tier eval: runs every scenario as a text-only conversation
between a simulated caller LLM and a text-mode version of the agent
(same prompt, same tools, no audio). Resumable via --run-id.

Run:
    python -m eval.run_text_eval
    python -m eval.run_text_eval --run-id my-run --limit 5
    python -m eval.run_text_eval --prompt-version v2
"""
import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from app.config import settings  # noqa: E402
from app.db import close_pool, get_pool, init_pool  # noqa: E402
from app.genai_client import get_text_client  # noqa: E402
from eval.db_eval import already_scored, finish_run, start_run  # noqa: E402
from eval.report import print_report  # noqa: E402
from eval.runner import run_scenario  # noqa: E402
from eval.scenario_schema import load_scenarios  # noqa: E402
from eval.simulated_caller import SimulatedCaller  # noqa: E402
from eval.text_agent import TextAgent  # noqa: E402
from google import genai  # noqa: E402

MAX_TURNS = 12
SCENARIO_CONCURRENCY = 1  # Vertex AI's trial-project quota is much tighter than AI Studio's free tier was


def make_text_driver(client: genai.Client, pool, prompt_version: str):
    async def drive(scenario):
        call_id = f"eval-text-{scenario.id}-{int(time.time() * 1000)}"
        agent = TextAgent(client, pool, call_id, prompt_version)
        caller = SimulatedCaller(client, scenario)

        transcript: list[dict] = []
        agent_text = await agent.greet()
        transcript.append({"role": "agent", "text": agent_text})

        for _ in range(MAX_TURNS):
            caller_text, done = await caller.respond_to(agent_text)
            transcript.append({"role": "caller", "text": caller_text})
            agent_text = await agent.send(caller_text)
            transcript.append({"role": "agent", "text": agent_text})
            if done:
                break

        return transcript, agent.tool_log, {}

    return drive


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None, help="Resume an existing run by reusing its id.")
    parser.add_argument("--prompt-version", default="v1")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N scenarios.")
    args = parser.parse_args()

    run_id = args.run_id or f"text-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    if not settings.use_vertex and not settings.gemini_api_key:
        print("GEMINI_API_KEY is not set.")
        return

    await init_pool()
    pool = get_pool()
    client = get_text_client()

    await start_run(pool, run_id, "text", args.prompt_version)

    scenarios = load_scenarios()
    if args.limit:
        scenarios = scenarios[: args.limit]

    drive = make_text_driver(client, pool, args.prompt_version)
    semaphore = asyncio.Semaphore(SCENARIO_CONCURRENCY)
    completed = 0
    total = len(scenarios)

    async def run_one(scenario):
        nonlocal completed
        if await already_scored(pool, run_id, scenario.id):
            completed += 1
            print(f"[{completed}/{total}] {scenario.id} -- already scored, skipping")
            return
        async with semaphore:
            metrics = await run_scenario(client, pool, scenario, run_id, drive)
        completed += 1
        status = "ERROR" if "error" in metrics else ("PASS" if metrics.get("task_success") else "FAIL")
        print(f"[{completed}/{total}] {scenario.id} -- {status}")

    await asyncio.gather(*(run_one(s) for s in scenarios))

    await finish_run(pool, run_id)
    print(f"\nRun '{run_id}' complete.\n")
    await print_report(pool, run_id)
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
