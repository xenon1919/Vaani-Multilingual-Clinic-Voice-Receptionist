"""Phase 3 audio-tier eval: the smaller scenario subset (audio_subset:
true in scenarios.yaml) run through a real Gemini Live session with
synthesized caller speech, instead of the cheaper text-only tier. Adds
time-to-first-audio on top of everything the text tier measures.

Connects directly to the Live API -- no need to run `uvicorn app.main:app`
for this, since there's no browser involved.

Run:
    python -m eval.run_audio_eval
    python -m eval.run_audio_eval --run-id my-audio-run --limit 2
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
from app.genai_client import get_live_client, get_text_client  # noqa: E402
from eval.audio_agent import AudioAgent, voice_for_language  # noqa: E402
from eval.db_eval import already_scored, finish_run, start_run  # noqa: E402
from eval.report import print_report  # noqa: E402
from eval.runner import run_scenario  # noqa: E402
from eval.scenario_schema import load_scenarios  # noqa: E402
from eval.simulated_caller import SimulatedCaller  # noqa: E402
from google import genai  # noqa: E402

MAX_TURNS = 12
SCENARIO_CONCURRENCY = 1  # Vertex AI's trial-project quota is much tighter than AI Studio's free tier was


def make_audio_driver(live_client: genai.Client, text_client: genai.Client, pool, prompt_version: str):
    async def drive(scenario):
        call_id = f"eval-audio-{scenario.id}-{int(time.time() * 1000)}"
        voice = voice_for_language(scenario.language)
        caller = SimulatedCaller(text_client, scenario)
        ttfa_list: list[int] = []
        transcript: list[dict] = []

        async with AudioAgent(live_client, pool, call_id, prompt_version) as agent:
            agent_text, ttfa = await agent.greet()
            if ttfa is not None:
                ttfa_list.append(ttfa)
            agent_text = agent_text or "(no audible response)"
            transcript.append({"role": "agent", "text": agent_text})

            for _ in range(MAX_TURNS):
                caller_text, done = await caller.respond_to(agent_text)
                transcript.append({"role": "caller", "text": caller_text})

                _, agent_text, ttfa = await agent.send_speech(caller_text, voice)
                if ttfa is not None:
                    ttfa_list.append(ttfa)
                agent_text = agent_text or "(no audible response)"
                transcript.append({"role": "agent", "text": agent_text})
                if done:
                    break

            tool_log = agent.tool_log

        extra_metrics = {
            "time_to_first_audio_ms": ttfa_list[0] if ttfa_list else None,
            "avg_time_to_first_audio_ms": sum(ttfa_list) / len(ttfa_list) if ttfa_list else None,
        }
        return transcript, tool_log, extra_metrics

    return drive


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--prompt-version", default="v1")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    run_id = args.run_id or f"audio-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    if not settings.use_vertex and not settings.gemini_api_key:
        print("GEMINI_API_KEY is not set.")
        return

    await init_pool()
    pool = get_pool()
    live_client = get_live_client()
    text_client = get_text_client()

    await start_run(pool, run_id, "audio", args.prompt_version)

    scenarios = [s for s in load_scenarios() if s.audio_subset]
    if args.limit:
        scenarios = scenarios[: args.limit]
    print(f"Running {len(scenarios)} audio-subset scenarios.")

    drive = make_audio_driver(live_client, text_client, pool, args.prompt_version)
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
            metrics = await run_scenario(text_client, pool, scenario, run_id, drive)
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
