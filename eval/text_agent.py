"""Text-mode version of the receptionist: same system prompt and same
tool layer (tools/registry.py) as the real Live session, but driven
through plain (non-Live) Gemini chat with manual function calling.

Confirmed directly against the real API that the Live model only
supports AUDIO output, so the text-tier eval can't reuse the Live session
-- this is a separate, cheaper code path that still exercises the exact
same prompt and tool implementations.
"""
import time
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types

from app.config import settings
from eval.rate_limit import call_with_limit
from tools.registry import build_gemini_tools, dispatch

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def render_system_instruction(prompt_version: str) -> str:
    template = (PROMPTS_DIR / f"{prompt_version}.md").read_text(encoding="utf-8")
    now = datetime.now()
    return template.format(today_date=now.strftime("%Y-%m-%d"), today_weekday=now.strftime("%A"))


class TextAgent:
    """One instance per scenario run: owns one chat session, one call_id,
    and the log of every tool call it made."""

    def __init__(self, client: genai.Client, pool, call_id: str, prompt_version: str = "v1"):
        self.pool = pool
        self.call_id = call_id
        self.tool_log: list[dict] = []
        config = types.GenerateContentConfig(
            system_instruction=render_system_instruction(prompt_version),
            tools=build_gemini_tools(),
        )
        self.chat = client.aio.chats.create(model=settings.text_model_name, config=config)

    async def greet(self) -> str:
        """Triggers the agent's opening line, mirroring the Live
        session's "(Call connected.)" kickoff."""
        response = await call_with_limit(self.chat.send_message, "(Call connected.)")
        return await self._drain_tool_calls(response)

    async def send(self, caller_text: str) -> str:
        response = await call_with_limit(self.chat.send_message, caller_text)
        return await self._drain_tool_calls(response)

    async def _drain_tool_calls(self, response) -> str:
        while response.function_calls:
            parts = []
            for fc in response.function_calls:
                start = time.monotonic()
                result = await dispatch(self.pool, self.call_id, fc.name, fc.args or {})
                latency_ms = int((time.monotonic() - start) * 1000)
                self.tool_log.append(
                    {"name": fc.name, "args": fc.args or {}, "result": result, "latency_ms": latency_ms}
                )
                parts.append(types.Part.from_function_response(name=fc.name, response=result))
            response = await call_with_limit(self.chat.send_message, parts)
        return response.text or ""
