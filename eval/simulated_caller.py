"""The simulated caller: a Gemini text chat playing the patient persona
described by a scenario's caller_goal, speaking only in the scenario's
language. Ends its final line with the literal token END_CALL once its
goal is resolved (achieved, correctly refused, or transferred) so the
runner knows when to stop."""
from google import genai
from google.genai import types

from app.config import settings
from eval.rate_limit import call_with_limit
from eval.scenario_schema import Scenario

END_CALL_TOKEN = "END_CALL"

LANG_NAMES = {
    "te": "Telugu",
    "hi": "Hindi",
    "en": "English",
    "mixed": "a natural mix of Telugu, Hindi, and English in the same sentences",
}


def _build_system_instruction(scenario: Scenario) -> str:
    return (
        f"You are roleplaying as a caller phoning a clinic receptionist. Stay fully in character.\n\n"
        f"Persona: {scenario.caller_persona}\n"
        f"Your goal: {scenario.caller_goal}\n\n"
        f"Rules:\n"
        f"- Speak only in {LANG_NAMES[scenario.language]}.\n"
        f"- Output ONLY what the caller says out loud -- no narration, no stage directions, no quotes.\n"
        f"- Keep each turn short and natural, like a real phone call (1-2 sentences).\n"
        f"- The messages you receive are what the receptionist just said to you.\n"
        f"- Once your goal is resolved -- you got what you needed, you were correctly refused/transferred, "
        f"or the conversation has clearly run its course -- end your message with the exact token "
        f"{END_CALL_TOKEN} on its own at the end.\n"
        f"- Never say {END_CALL_TOKEN} before then."
    )


class SimulatedCaller:
    def __init__(self, client: genai.Client, scenario: Scenario):
        config = types.GenerateContentConfig(system_instruction=_build_system_instruction(scenario))
        self.chat = client.aio.chats.create(model=settings.text_model_name, config=config)

    async def respond_to(self, agent_text: str) -> tuple[str, bool]:
        """Returns (caller_utterance_without_token, is_done)."""
        response = await call_with_limit(self.chat.send_message, agent_text)
        text = response.text or ""
        done = END_CALL_TOKEN in text
        text = text.replace(END_CALL_TOKEN, "").strip()
        return text, done
