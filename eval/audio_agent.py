"""Audio-tier eval driver: one real Gemini Live session per scenario,
talking directly to the Live API with the exact same prompt and tools as
the production session (app/live_session.py) -- but driving a scripted
multi-turn conversation and needing transcripts back, which is different
enough from "relay a browser's audio indefinitely" that it's its own
small implementation rather than a forced shared abstraction with the
production WebSocket relay.

Real microphone-like pacing (sending each ~100ms chunk roughly 100ms
apart, with trailing silence) lets the server's own automatic
voice-activity detection work the same way it does for a real caller --
confirmed against the real API in scripts/test_booking_flow.py.
"""
import asyncio
import time
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types

from app.config import settings
from eval.rate_limit import call_with_limit
from scripts.tts_audio import VOICES, synthesize_pcm16
from tools.registry import build_gemini_tools, dispatch

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
CHUNK_BYTES = 3200  # ~100ms of 16kHz 16-bit mono audio
TRAILING_SILENCE_S = 1


def render_system_instruction(prompt_version: str) -> str:
    template = (PROMPTS_DIR / f"{prompt_version}.md").read_text(encoding="utf-8")
    now = datetime.now()
    return template.format(today_date=now.strftime("%Y-%m-%d"), today_weekday=now.strftime("%A"))


def voice_for_language(language: str) -> str:
    return VOICES.get(language, VOICES["en"])  # "mixed" has no dedicated voice -- default to English


class AudioAgent:
    def __init__(self, client: genai.Client, pool, call_id: str, prompt_version: str = "v1"):
        self.pool = pool
        self.call_id = call_id
        self.tool_log: list[dict] = []
        self._config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=render_system_instruction(prompt_version),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            tools=build_gemini_tools(),
        )
        self._connect_cm = client.aio.live.connect(model=settings.live_model_name, config=self._config)
        self.session = None

    async def __aenter__(self) -> "AudioAgent":
        self.session = await self._connect_cm.__aenter__()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._connect_cm.__aexit__(*exc_info)

    async def _handle_tool_call(self, tool_call) -> None:
        function_responses = []
        for fc in tool_call.function_calls:
            start = time.monotonic()
            result = await dispatch(self.pool, self.call_id, fc.name, fc.args or {})
            latency_ms = int((time.monotonic() - start) * 1000)
            self.tool_log.append({"name": fc.name, "args": fc.args or {}, "result": result, "latency_ms": latency_ms})
            function_responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response=result))
        await self.session.send_tool_response(function_responses=function_responses)

    async def _drain_turn(self, t_start: float) -> tuple[str, str, int | None]:
        heard, replied = [], []
        t_first_audio = None
        # A turn that only made a tool call can turn_complete with nothing
        # spoken yet -- the actual spoken reply arrives as a SEPARATE
        # subsequent turn. Keep draining turns until something was said,
        # capped so a genuinely silent response can't loop forever.
        empty_turns_seen = 0
        empty_turn_limit = 3
        while True:
            turn_done = False
            async for response in self.session.receive():
                if response.tool_call:
                    await self._handle_tool_call(response.tool_call)

                server_content = response.server_content
                if server_content is None:
                    continue
                if server_content.input_transcription and server_content.input_transcription.text:
                    heard.append(server_content.input_transcription.text)
                if server_content.output_transcription and server_content.output_transcription.text:
                    replied.append(server_content.output_transcription.text)
                if server_content.model_turn and t_first_audio is None:
                    for part in server_content.model_turn.parts:
                        if part.inline_data and part.inline_data.data:
                            t_first_audio = time.monotonic()
                            break
                if server_content.turn_complete:
                    if replied or empty_turns_seen >= empty_turn_limit:
                        ttfa_ms = int((t_first_audio - t_start) * 1000) if t_first_audio else None
                        return "".join(heard), "".join(replied), ttfa_ms
                    empty_turns_seen += 1
                    turn_done = True
                    break
            if not turn_done:
                return "".join(heard), "".join(replied), None

    async def greet(self) -> tuple[str, int | None]:
        t_start = time.monotonic()
        await call_with_limit(
            self.session.send_client_content,
            turns=types.Content(role="user", parts=[types.Part(text="(Call connected.)")]),
            turn_complete=True,
        )
        _, replied, ttfa_ms = await self._drain_turn(t_start)
        return replied, ttfa_ms

    async def send_speech(self, text: str, voice: str) -> tuple[str, str, int | None]:
        pcm = await synthesize_pcm16(text, voice, settings.live_input_sample_rate_hz)
        pcm += b"\x00" * settings.live_input_sample_rate_hz * 2 * TRAILING_SILENCE_S

        t_start = time.monotonic()
        for i in range(0, len(pcm), CHUNK_BYTES):
            await self.session.send_realtime_input(
                audio=types.Blob(data=pcm[i : i + CHUNK_BYTES], mime_type=settings.live_input_mime_type)
            )
            await asyncio.sleep(0.1)
        return await self._drain_turn(t_start)
