# Gemini Live API notes (checked 2026-10-14)

Recorded here because the project rule is: verify against current docs before
writing Live API code, and flag anything uncertain rather than silently
guessing.

## Model
- Current default: `gemini-3.8-live` — "default Live API model for most
  low-latency voice agent experiences" per the models page.
- Legacy preview still listed: `gemini-3.1-flash-live-preview`.
- `gemini-2.5-flash-native-audio-preview-12-2025` exists but new projects are
  steered to the newer models; access may be restricted for new API keys.
- Set in `.env` as `LIVE_MODEL_NAME`, read only via `app/config.py`. If
  `gemini-3.8-live` turns out to be unavailable on your key, swap this one
  value — nothing else should need to change.

## Audio format
- Input: 16-bit PCM, 16kHz, little-endian, mono. MIME type
  `audio/pcm;rate=16000`.
- Output: 16-bit PCM, 24kHz (consistent across the Live model family in all
  sources checked, though not confirmed on a `gemini-3.8-live`-specific page).
- Both values live in `.env` / `app/config.py`, not hardcoded.

## SDK shapes (verified by introspecting the installed `google-genai==2.30.1`
package directly, not just reading docs — docs for a couple of fields were
stale/incomplete)
- `types.LiveConnectConfig` has `response_modalities`, `system_instruction`,
  `tools`, `speech_config`, `input_audio_transcription`,
  `output_audio_transcription`, among others.
- `types.AudioTranscriptionConfig()` enables transcription of caller audio
  (`input_audio_transcription`) and of the model's spoken reply
  (`output_audio_transcription`) — this is how the Phase 0 spike captures
  "what the model understood" without needing an LLM judge.
- Sending audio: `session.send_realtime_input(audio=types.Blob(data=pcm_bytes,
  mime_type="audio/pcm;rate=16000"))`, end of turn signaled explicitly with
  `session.send_realtime_input(audio_stream_end=True)`.
- Tool calls arrive as `response.tool_call.function_calls` (each with `id`,
  `name`, `args`) and must be answered manually — the SDK does not auto-run
  tools. Reply with
  `session.send_tool_response(function_responses=[types.FunctionResponse(id=fc.id, name=fc.name, response={...})])`.
  `FunctionResponse` also supports `scheduling` (`INTERRUPT` / `WHEN_IDLE` /
  `SILENT`) for non-blocking tools, not used yet.

## `session.receive()` is per-turn, not per-call

Found by debugging a real hang in Phase 1: `session.receive()` is an async
generator that ends naturally once the current turn completes
(`turn_complete=True`) — it does **not** stay open for the whole call. The
first implementation called it once and iterated with a single `async for`,
assuming it would keep streaming for the call's entire lifetime; it looked
fine for the agent's first reply, then the connection appeared to silently
die on the next turn. The fix is the documented pattern: wrap it in an outer
`while True:`, re-entering `session.receive()` for every new turn:

```python
while True:
    async for response in session.receive():
        ...  # handle one turn's events
```

See `app/live_session.py`'s `live_to_browser()`.

## Automatic VAD vs. manual activity control

- **Phase 0 spike** (pre-synthesized TTS clips, no natural pacing): automatic
  VAD never detects end-of-speech, so turns never complete and the session
  hangs. Fixed by disabling automatic VAD and sending explicit
  `activity_start` / `activity_end` around the audio.
- **Phase 1** (real browser microphone, continuously streamed): automatic VAD
  is left enabled. Real speech has natural pauses, and leaving VAD on is what
  gives interruption/barge-in for free — the server emits
  `server_content.interrupted=True` when it detects the caller speaking over
  the agent, which the backend forwards to the browser so it can clear its
  playback queue instantly.

## Tool declarations accept raw Pydantic JSON schema directly

`types.FunctionDeclaration` has a `parameters_json_schema` field that takes
a plain JSON Schema dict — confirmed by passing a Pydantic model's own
`.model_json_schema()` straight through and watching the real API accept it
and call the tool with correctly structured args. No hand-written
conversion to Gemini's custom `Schema` type was needed. See
`tools/registry.py`.

## Free-tier risk materialized: switched to Vertex AI (Phase 4)

The "open risk" below turned out to be real, just not in the way expected:
the AI Studio API key in use had a **Prepay billing account** attached
(not the free tier) and was charged ₹500 before this was noticed. The fix
was switching to **Vertex AI**, which the same `google-genai` package talks
to via a different client construction — no API key at all:

```python
genai.Client(vertexai=True, project="your-project-id", location="us-central1")
```

Auth is via Application Default Credentials, set up once with
`gcloud auth application-default login` (not interactive-flow-friendly
from an automated session — needs a human to complete the browser OAuth
step). See `app/genai_client.py`.

**The region split (found by testing directly, not documented anywhere
I could find):** the *same* models are only reachable at *different*
Vertex regions depending on call type, for the same project:
- `gemini-3.8-live` (Live API) → only works at `location="us-central1"`;
  404s at `"global"`.
- `gemini-3.8-flash` (plain `generate_content`/chat) → only works at
  `location="global"`; 404s at `"us-central1"`.

This is why `app/genai_client.py` exposes two functions,
`get_live_client()` and `get_text_client()`, each pinned to the region
that actually works for that call type, instead of one client/one
location for the whole app.

**Quota is tighter than AI Studio's free tier was.** Running eval
scenarios with any real concurrency (3-4 parallel requests, same as
Phase 3) produced sustained `429 RESOURCE_EXHAUSTED` throttling on the
Vertex trial project. Fixed by dropping to fully serialized requests
(`eval/rate_limit.py`: `MAX_CONCURRENT_REQUESTS = 1`,
`SCENARIO_CONCURRENCY = 1` in both runner scripts) — reliable, but
noticeably slower (a 40-scenario run went from ~15 minutes to ~1h45m).

**Billing note**: Vertex AI usage draws from the project's Google Cloud
billing account, which may carry an active free-trial credit (check
Console → Billing → Credits — "Available" with 100% remaining and an end
date means it's genuinely free up to that amount/date) or may not. Verify
this for your own project before relying on it — see the README's "A note
on cost" section.

## Original open risk (resolved above)
Official rate-limit docs didn't state whether Live models were included on
the free AI Studio tier, or what the concurrency/session limits were. The
workaround considered at the time — the Phase 3 **text-tier** evaluation
doesn't touch the Live API at all, so could proceed regardless — remains
true and was useful; the actual blocker that materialized was a billing
account attached to the key, not a quota limit as such.
