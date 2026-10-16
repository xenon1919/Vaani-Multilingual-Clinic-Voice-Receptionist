"""Owns one Gemini Live session per phone call: relays audio between it
and the browser WebSocket, dispatches tool calls to the tool layer, and
logs every turn (transcript, tool calls, latency, outcome) to Postgres.

Unlike the Phase 0 spike (which disables automatic VAD because synthesized
TTS clips have no natural trailing silence), real microphone audio has
natural pauses, so automatic voice-activity detection is left enabled here.
That is what gives us caller interruption/barge-in for free: the server
detects speech during the agent's turn and sends `interrupted`, which we
forward to the browser so it can stop playback immediately.
"""
import asyncio
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import WebSocket
from google.genai import types

from app.config import settings
from app.db import get_pool
from app.genai_client import get_live_client
from app.turn_logging import end_call, log_turn, start_call
from tools.registry import build_gemini_tools, dispatch

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "v1.md"
_SYSTEM_INSTRUCTION_TEMPLATE = PROMPT_PATH.read_text(encoding="utf-8")


def _render_system_instruction() -> str:
    now = datetime.now()
    return _SYSTEM_INSTRUCTION_TEMPLATE.format(
        today_date=now.strftime("%Y-%m-%d"), today_weekday=now.strftime("%A")
    )


def _build_config() -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=_render_system_instruction(),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        tools=build_gemini_tools(),
    )


async def run_call(websocket: WebSocket) -> None:
    """Runs for the lifetime of one call: one Gemini Live session, relayed
    to/from one browser WebSocket connection."""
    call_id = uuid.uuid4().hex
    pool = get_pool()
    await start_call(pool, call_id)
    outcome = "completed"
    turn_index = 0

    def next_turn_index() -> int:
        nonlocal turn_index
        turn_index += 1
        return turn_index

    client = get_live_client()
    config = _build_config()

    try:
        async with client.aio.live.connect(model=settings.live_model_name, config=config) as session:
            # Trigger the agent's opening line (greeting + consent notice,
            # per prompts/v1.md) without waiting for the caller to speak first.
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text="(Call connected.)")]),
                turn_complete=True,
            )

            async def browser_to_live() -> None:
                while True:
                    chunk = await websocket.receive_bytes()
                    await session.send_realtime_input(
                        audio=types.Blob(data=chunk, mime_type=settings.live_input_mime_type)
                    )

            async def handle_tool_call(tool_call) -> None:
                await websocket.send_json({"type": "state", "value": "checking"})
                function_responses = []
                for fc in tool_call.function_calls:
                    start = time.monotonic()
                    result = await dispatch(pool, call_id, fc.name, fc.args or {})
                    latency_ms = int((time.monotonic() - start) * 1000)

                    await log_turn(
                        pool,
                        call_id,
                        next_turn_index(),
                        role="tool",
                        tool_name=fc.name,
                        tool_args=fc.args or {},
                        tool_result=result,
                        latency_ms=latency_ms,
                    )
                    if fc.name == "transfer_to_human" and result.get("status") == "ok":
                        nonlocal outcome
                        outcome = f"transferred_{result.get('reason', 'unknown')}"

                    function_responses.append(
                        types.FunctionResponse(id=fc.id, name=fc.name, response=result)
                    )
                await session.send_tool_response(function_responses=function_responses)

            async def live_to_browser() -> None:
                caller_text, agent_text = [], []
                agent_speaking = False
                # session.receive() is a per-turn generator: it ends naturally
                # once a turn completes, not when the call ends. Re-enter it
                # for each subsequent turn until the call itself ends.
                while True:
                    async for response in session.receive():
                        if response.tool_call:
                            await handle_tool_call(response.tool_call)

                        server_content = response.server_content
                        if server_content is None:
                            continue
                        if server_content.interrupted:
                            await websocket.send_json({"type": "interrupted"})
                            agent_speaking = False
                        if server_content.input_transcription and server_content.input_transcription.text:
                            caller_text.append(server_content.input_transcription.text)
                        if server_content.output_transcription and server_content.output_transcription.text:
                            agent_text.append(server_content.output_transcription.text)
                        if server_content.model_turn:
                            if not agent_speaking:
                                agent_speaking = True
                                await websocket.send_json({"type": "state", "value": "speaking"})
                            for part in server_content.model_turn.parts:
                                if part.inline_data and part.inline_data.data:
                                    await websocket.send_bytes(part.inline_data.data)
                        if server_content.turn_complete:
                            if caller_text:
                                text = "".join(caller_text)
                                await log_turn(
                                    pool, call_id, next_turn_index(), role="caller", transcript=text,
                                )
                                await websocket.send_json({"type": "transcript", "role": "caller", "text": text})
                                caller_text = []
                            if agent_text:
                                text = "".join(agent_text)
                                await log_turn(
                                    pool, call_id, next_turn_index(), role="agent", transcript=text,
                                )
                                await websocket.send_json({"type": "transcript", "role": "agent", "text": text})
                                agent_text = []
                            agent_speaking = False
                            await websocket.send_json({"type": "state", "value": "listening"})

            browser_task = asyncio.create_task(browser_to_live())
            live_task = asyncio.create_task(live_to_browser())
            try:
                await asyncio.wait({browser_task, live_task}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                browser_task.cancel()
                live_task.cancel()
                await asyncio.gather(browser_task, live_task, return_exceptions=True)
    except Exception:
        logger.exception("call %s ended with an error", call_id)
        outcome = "error"
        raise
    finally:
        await end_call(pool, call_id, outcome)
