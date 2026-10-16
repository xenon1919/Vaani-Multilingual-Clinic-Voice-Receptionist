"""Phase 0 feasibility spike: sends ~30 synthetic Telugu/Hindi/English/mixed
utterances as real audio to the Gemini Live API and records what it heard
(input transcription) and how it replied (output transcription + text).

This is a feasibility probe, not a scored eval — read the saved results by
eye to judge language quality before building the rest of the agent.

Run:
    python scripts/run_spike.py
"""
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from app.config import settings  # noqa: E402
from app.genai_client import get_live_client  # noqa: E402
from scripts.spike_utterances import UTTERANCES  # noqa: E402
from scripts.tts_audio import VOICES, synthesize_pcm16  # noqa: E402

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "v1.md"
RESULTS_PATH = Path(__file__).resolve().parent / "spike_results.json"
TTS_TIMEOUT_S = 15
LIVE_TIMEOUT_S = 20
CHUNK_BYTES = 3200  # ~100ms of 16kHz 16-bit mono audio per chunk


async def run_one(client: genai.Client, system_instruction: str, utterance: dict) -> dict:
    voice = VOICES[utterance["voice"]]
    try:
        pcm = await asyncio.wait_for(
            synthesize_pcm16(utterance["text"], voice, settings.live_input_sample_rate_hz),
            timeout=TTS_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 — spike must keep going on a single bad utterance
        return {**utterance, "heard": "", "replied": "", "error": f"TTS {type(exc).__name__}: {exc}"}

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=system_instruction,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        # Automatic VAD never detects end-of-speech on a pre-synthesized TTS
        # clip with no natural trailing silence, so turns never complete and
        # the session hangs. Manual activity_start/activity_end sidesteps
        # that — confirmed by direct testing against the real API.
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True),
        ),
    )

    heard_parts: list[str] = []
    replied_parts: list[str] = []

    async def converse():
        async with client.aio.live.connect(model=settings.live_model_name, config=config) as session:
            await session.send_realtime_input(activity_start=types.ActivityStart())
            for i in range(0, len(pcm), CHUNK_BYTES):
                await session.send_realtime_input(
                    audio=types.Blob(
                        data=pcm[i : i + CHUNK_BYTES],
                        mime_type=settings.live_input_mime_type,
                    )
                )
            await session.send_realtime_input(activity_end=types.ActivityEnd())

            async for response in session.receive():
                sc = response.server_content
                if sc is None:
                    continue
                if sc.input_transcription and sc.input_transcription.text:
                    heard_parts.append(sc.input_transcription.text)
                if sc.output_transcription and sc.output_transcription.text:
                    replied_parts.append(sc.output_transcription.text)
                if sc.turn_complete:
                    break

    try:
        await asyncio.wait_for(converse(), timeout=LIVE_TIMEOUT_S)
        return {
            **utterance,
            "heard": "".join(heard_parts).strip(),
            "replied": "".join(replied_parts).strip(),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — spike must keep going on a single bad utterance
        return {**utterance, "heard": "", "replied": "", "error": f"{type(exc).__name__}: {exc}"}


async def main() -> None:
    if not settings.use_vertex and not settings.gemini_api_key:
        print("GEMINI_API_KEY is not set. Copy .env.example to .env and fill it in.")
        return

    system_instruction = PROMPT_PATH.read_text(encoding="utf-8")
    client = get_live_client()

    results = []
    for utterance in UTTERANCES:
        print(f"[{utterance['id']}] sending ({utterance['lang']}): {utterance['text']}", flush=True)
        result = await run_one(client, system_instruction, utterance)
        if result["error"]:
            print(f"    ERROR: {result['error']}", flush=True)
        else:
            print(f"    heard:   {result['heard']}", flush=True)
            print(f"    replied: {result['replied']}", flush=True)
        results.append(result)
        # Write after every utterance so a crash/interrupt doesn't lose progress.
        RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(1 for r in results if not r["error"])
    print(f"\n{ok}/{len(results)} utterances completed without error.")
    print(f"Full results saved to {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
