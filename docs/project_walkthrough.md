# Project walkthrough: Vaani (Aarogya Clinic's multilingual voice agent)

A complete, chronological record of how this project was built — from an
empty folder to a live, deployed voice agent. Written as a step-by-step
reference, not a sales pitch: it includes what broke and how it got
fixed, not just what the finished product does.

## What this project is

A voice agent that answers phone-style calls for a clinic, in Telugu,
Hindi, English, or a mix of all three, and books/reschedules/cancels
doctor appointments by voice — built on Google's Gemini Live API, with a
rigorous automated evaluation harness as a first-class part of the
project, not an afterthought.

## Tech stack

| Piece | Choice |
|---|---|
| Voice AI | Gemini Live API, via the `google-genai` SDK |
| Backend | FastAPI + `asyncio`, Python 3.12 |
| Database | Postgres (Docker locally, Cloud SQL when deployed) |
| Frontend | Plain HTML/JS, no framework (uses the Web Audio API directly) |
| Tests | pytest, against a real local Postgres, no mocks |
| Deployment | Docker container on Google Cloud Run |

---

## Phase 0 — Setup and feasibility spike

**Goal:** prove the core idea (multilingual voice understanding) works
before building anything else on top of it.

1. Scaffolded the folder structure: `app/`, `tools/`, `prompts/`, `db/`,
   `eval/`, `scripts/`, `tests/`, `frontend/`, `docs/`.
2. Wrote `db/docker-compose.yml` and `db/schema.sql` for a local Postgres,
   plus `db/seed.py` to generate 3 synthetic doctors and two weeks of
   appointment slots.
3. Before writing any Live API code, verified the actual current API
   shape against the real installed SDK (`google-genai`) by introspecting
   it directly in Python, rather than trusting documentation that turned
   out to be stale in places. Recorded every finding in
   `docs/live_api_notes.md` as it was discovered.
4. Built `scripts/run_spike.py`: synthesized ~30 short utterances in
   Telugu, Hindi, English, and mixed speech (names, phone numbers,
   informal dates like "repu"/"naalugu") using free Edge TTS, and sent
   them through a real Gemini Live session to see what it understood.
5. **Bug found and fixed:** the spike hung on every single utterance.
   Root cause: Gemini's automatic voice-activity detection (VAD) relies
   on detecting a pause after speech to know the caller is done talking —
   but synthesized TTS clips have no natural trailing silence, so VAD
   never fired and every session just hung. Fixed by disabling automatic
   VAD for this spike and manually marking `activity_start`/`activity_end`
   around each audio clip.
6. Ran the fixed spike: 27 of 30 utterances understood correctly,
   including spoken digit-by-digit phone numbers and informal
   Telugu/Hindi date phrases. This was the go/no-go signal to proceed.

---

## Phase 1 — Minimal voice loop

**Goal:** a real-time, two-way voice conversation in the browser, no
appointment logic yet.

1. Built `app/live_session.py`: opens one Gemini Live session per phone
   call, relays audio in both directions between the browser and Gemini.
2. Built the browser side: `frontend/mic-worklet.js` captures microphone
   audio and streams it as PCM16 chunks; `frontend/player-worklet.js`
   plays back the agent's replies and can be instantly cleared (for
   interruption/barge-in support).
3. **Design decision, confirmed by testing:** unlike the Phase 0 spike,
   real microphone audio has natural pauses, so automatic VAD was left
   *enabled* here — that's what makes it possible for a caller to
   interrupt the agent mid-sentence and have it stop talking immediately.
4. **Bug found and fixed:** after the agent's first reply, the connection
   would appear to silently die on the next turn. Root cause:
   `session.receive()` is a *per-turn* generator — it ends naturally once
   one turn completes, it doesn't stay open for the whole call. Fixed by
   wrapping it in an outer loop that re-enters `receive()` for every new
   turn.
5. Verified with a real browser conversation in Telugu and Hindi, and
   with a scripted no-browser test client that drives the same WebSocket
   protocol.

---

## Phase 2 — Tools and database

**Goal:** the agent can actually book, reschedule, and cancel
appointments — safely.

1. Implemented 6 tools in `tools/`: `check_availability`,
   `book_appointment`, `reschedule_appointment`, `cancel_appointment`,
   `get_clinic_info`, `transfer_to_human`. Each has a typed Pydantic input
   schema.
2. **Core design rule:** the AI model only *collects* information (name,
   date, time, phone number) — it never decides whether a slot is free or
   confirms a booking itself. All of that deterministic logic
   (`tools/validation.py`, `tools/confirmation.py`) lives in the backend.
3. **Double-booking prevention, at the database level, not just in code:**
   `slots` has a `UNIQUE(doctor_id, slot_date, slot_time)` constraint, and
   `appointments.slot_id` is also `UNIQUE`. Two simultaneous booking
   attempts for the same slot: Postgres lets exactly one through.
4. Built the two-step confirmation protocol (`tools/confirmation.py`): a
   write tool's first call validates everything and returns a token; the
   agent must read the details back to the caller and get explicit
   agreement before a second call (with that same token) actually
   executes the booking.
5. Added PII masking (`tools/masking.py`) so names and phone numbers never
   land in the audit logs in plain text — including inside free-text
   message fields, which took a second pass to get right after the first
   version only masked specifically-keyed fields.
6. Verified a complete 53-test pytest suite against the *real* local
   Postgres (no mocks), including a test that fires two concurrent
   database inserts at the same slot and asserts exactly one wins — the
   literal proof that the double-booking guarantee holds under real
   concurrency, not just "looks right in the code."
7. Verified a full real voice booking end to end: spoke a request, the
   agent checked availability, read back the details, took a spoken
   confirmation, and the appointment landed correctly in Postgres.

---

## Phase 3 — Evaluation harness

**Goal:** measure reliability systematically, not just "try it a few
times and see."

1. Designed a scenario schema (`eval/scenario_schema.py`) and generated
   40 scenarios (`eval/scenarios.yaml`) — 10 per language (Telugu, Hindi,
   English, mixed) across 8 categories: booking, rescheduling,
   cancelling, clinic-info questions, medical-advice requests
   (should refuse), emergencies (should transfer to a human), prompt
   injection attempts (should resist), and callers who change their mind
   mid-conversation.
2. Built two evaluation tiers:
   - **Text tier** (`eval/run_text_eval.py`): a simulated caller LLM
     plays the patient persona and talks to a text-mode version of the
     agent (same prompt, same tools, no audio) — fast and cheap.
   - **Audio tier** (`eval/run_audio_eval.py`): a smaller subset, with the
     caller's lines synthesized to real speech and sent through an actual
     Gemini Live session.
3. Built code-based scoring (`eval/scoring.py`): task success, the
   **wrong-booking rate** (the single most important metric — did it book
   the *correct* thing, not just *something*), tool-call correctness,
   confirmation compliance, correct-refusal rate, entity accuracy on
   names/numbers, and tool latency — plus an LLM judge for the two
   genuinely qualitative metrics (conversation quality, refusal quality).
4. Added resilience for running many real API calls: `eval/rate_limit.py`
   caps concurrency and retries with exponential backoff on rate-limit
   errors, and every run is resumable by `--run-id` (a rerun skips
   scenarios that already have a scored result).
5. **Three real bugs found while running the full suite, not just writing
   it:**
   - All 4 language variants of each scenario category had been targeting
     the *identical* doctor/date/time slot, so they fought over the same
     appointment the moment more than one language ran together — a test
     design bug producing fake failures, not an agent problem. Fixed by
     staggering the target slot per language.
   - A hardcoded relative date happened to land on a Sunday (the clinic's
     closed day) depending on what day the eval was actually run on.
     Fixed by making date resolution skip Sundays, matching the real
     schedule.
   - In the audio tier, a turn that only made a tool call could complete
     with nothing spoken yet (the actual reply arrives in a *following*
     turn) — the driver returned an empty reply, which then broke the
     simulated caller. Fixed by draining additional turns until real
     content arrives.
6. Final clean run: 40/40 text-tier scenarios, 0 errors, 0% wrong
   bookings, across every language.

---

## Phase 4 — Iteration and ablations

**Goal:** use the Phase 3 data to actually improve the system, and
measure what each change did.

1. Built an error taxonomy directly from real Phase 3 failures (not
   hypothetical ones): name transliteration mismatches (agent books
   correctly but stores the name in native script instead of the
   caller's Latin-script spelling), redundant re-confirmation (asking
   "is that correct?" twice), ignoring a direct question, and an abrupt
   call ending.
2. Wrote `prompts/v2.md` with four targeted fixes, one per taxonomy
   category.
3. Ran three ablations side by side on the identical scenario set:
   - **Prompt v1 vs v2** — entity error rate dropped from ~20% to **0%**,
     a clean, validated improvement.
   - **Confirmation on vs off** — wrong-booking rate stayed at 0% either
     way. Reported honestly as a negative/inconclusive finding specific
     to this scenario set (it doesn't yet include a scenario that
     deliberately injects a misheard detail for confirmation to catch),
     not spun as "confirmation doesn't matter."
   - **Model-version ablation** — started, then deliberately stopped and
     excluded (the spec marks this one optional, and a cloud quota issue,
     see below, made it impractical to finish in time).
4. **A real incident, handled transparently rather than hidden:**
   partway through this phase, the AI Studio API key in use turned out to
   have a Prepay billing account attached and was charged ₹500 — breaking
   the project's original zero-cost constraint. The fix was migrating
   from the AI Studio API key to **Vertex AI**, authenticated via
   `gcloud auth application-default login` against a Google Cloud project
   instead of an API key. This required real engineering work, documented
   in `docs/live_api_notes.md`:
   - The *same* Gemini models are only reachable at *different* Vertex
     regions depending on call type — the Live model needs
     `us-central1`, the plain text model needs `global`, for the same
     project. Found by testing directly, not documented anywhere clearly.
     `app/genai_client.py` exists specifically to hide this split behind
     two functions, `get_live_client()` and `get_text_client()`.
   - Vertex's trial-project rate limits turned out to be much stricter
     than AI Studio's free tier, requiring eval runs to be fully
     serialized (one request at a time) instead of running several in
     parallel.
5. Wrote `docs/results.md` with the full comparison tables and three
   honest failure write-ups.

---

## Phase 5 — Safety, privacy, documentation

**Goal:** make sure the safety behavior is actually tested, and be
upfront about privacy tradeoffs instead of just claiming compliance.

1. Built `tests/test_safety.py` — a dedicated suite that drives the real
   model (not a mock) through medical-advice requests, emergency
   descriptions, and prompt-injection attempts, asserting the correct
   tool behavior each time. All 9 tests passed against the real API.
   Kept opt-in (`RUN_SAFETY_TESTS=1`) rather than part of the default
   `pytest` run, specifically because of the billing incident above —
   nothing in this project makes an unprompted real API call anymore.
2. Wrote `docs/privacy.md`, mapping this project's actual controls
   (consent notice, PII masking, no raw audio storage, data minimization)
   to India's DPDP Act 2023 principles — explicitly including the real
   gaps (no affirmative consent capture, no retention policy, no
   encryption at rest) rather than overclaiming compliance.
3. Added a Mermaid architecture diagram to the README, and verified it
   actually parses as valid Mermaid syntax rather than just eyeballing
   it.
4. Documented known limitations and future work honestly, including the
   eval-scenario coverage gap the confirmation ablation surfaced.

---

## UI/UX improvements (after the 5 phases)

The original call page was functionally complete but visually dead — a
button and one line of status text, with no sense of what was happening
during a 30-second conversation.

1. **Wired up live feedback that already existed server-side but was
   never sent to the browser:** `app/live_session.py` now pushes
   `transcript` events (every finished turn, caller and agent) and
   `state` events (`speaking` / `listening` / `checking`) over the same
   WebSocket, alongside the raw audio.
2. Rebuilt `frontend/index.html` / `app.js` around that data: a live,
   scrolling chat-style transcript, an animated mic icon that changes
   color by state, and a call timer.
3. Added two new pages that were simply missing before:
   - `frontend/admin.html` — a staff dashboard listing all appointments
     (filterable) and recent call outcomes, backed by new read-only API
     endpoints in `app/admin_api.py`, gated by a shared password.
   - `frontend/lookup.html` — lets a caller check their own booking by
     phone number.
4. Built a shared design system (`frontend/style.css`) — consistent
   colors, buttons, badges, and table styling across all three pages,
   with dark-mode support — instead of three separately-styled
   prototypes.
5. Loaded a real typeface (Inter, via Google Fonts) and renamed the
   clinic from the placeholder "Sunrise Clinic" to "Aarogya Clinic"
   across every file that referenced it — the UI, both prompt versions,
   and the tool that answers clinic-info questions — then verified live,
   via a real voice call, that the agent actually speaks the new name.
6. Fixed two pieces of log noise found by reading the user's own server
   output: a benign-but-confusing Google Cloud warning (missing project
   ID for ADC's internal quota lookup), and a missing favicon causing a
   404 on every page load.

---

## Deployment

**Goal:** make the app reachable on a real public URL, not just
`localhost`.

This was an explicit scope change — the project's original hard
constraint was local-only — confirmed with the user before doing
anything, including a second confirmation specifically about Cloud SQL
(the one piece with an ongoing cost, unlike Cloud Run which scales to
zero when idle).

1. Wrote a `Dockerfile` that packages the FastAPI app (plus `ffmpeg`,
   needed by the TTS pipeline) into a container.
2. Enabled the required Google Cloud services: Cloud Run, Cloud SQL
   Admin, Cloud Build, Artifact Registry, Secret Manager.
3. Created a dedicated service account (`aarogya-clinic-run@...`) with
   exactly two IAM roles — Vertex AI User and Cloud SQL Client — rather
   than reusing a broad default account.
4. Provisioned a Cloud SQL Postgres instance (`db-f1-micro`, the smallest
   tier), in the same region as the Vertex AI Live model.
5. Connected to it locally through the Cloud SQL Auth Proxy, and ran the
   exact same `db/schema.sql` and `db/seed.py` used for local
   development — no special-cased deployment scripts.
6. Generated strong random passwords for the database and the admin
   dashboard, stored them in Secret Manager.
7. Changed `app/db.py` to support a second connection mode: when a
   `CLOUD_SQL_CONNECTION_NAME` environment variable is present, it
   connects via the Unix socket Cloud Run automatically mounts for Cloud
   SQL, instead of the normal `DATABASE_URL`. Local development is
   unaffected — this branch only activates in the deployed environment.
8. Ran `gcloud run deploy --source .` to build and deploy in one step.
9. **Four real failures along the way, each diagnosed from actual logs,
   not guessed at:**
   - Cloud Build couldn't read the uploaded source code — the default
     service account lacked `storage.objects.get`. Granted
     `roles/storage.objectViewer`.
   - The image built successfully but couldn't be pushed — the same
     account lacked Artifact Registry write access. Granted
     `roles/artifactregistry.writer`.
   - The container built and deployed, but crashed on startup with
     `InvalidPasswordError`. Root cause, found by reading the actual
     Cloud Run startup logs: piping a password string into
     `gcloud secrets create --data-file=-` from PowerShell silently
     appends a trailing newline to the stored secret, so the password
     Cloud Run read from Secret Manager didn't exactly match the
     password actually set on the Cloud SQL user. Fixed by writing the
     password to a file with explicit no-BOM, no-trailing-newline
     encoding before storing it, and re-setting the Cloud SQL password
     from that same clean source.
   - Fourth attempt deployed successfully.
10. Set a billing budget alert (₹1000, with email warnings at 50%, 90%,
    and 100%) on the Google Cloud billing account — directly in response
    to the earlier billing incident, so a similar surprise would now be
    caught immediately instead of discovered after the fact.
11. **Verified the live deployment for real:** checked the static pages
    return correctly, confirmed the admin API returns the real seeded
    doctors (proving the Cloud SQL connection actually works), and ran a
    full real voice call over the public WebSocket URL — got back a
    correct, correctly-branded spoken greeting from Vertex AI.

**Live URL:** https://aarogya-clinic-1068699861909.us-central1.run.app

---

## The recurring pattern, if there's one lesson

Nearly every real bug in this project — the Phase 0 VAD hang, the Phase 1
per-turn `receive()` issue, the Phase 3 scenario-collision bug, the
Sunday date bug, the Vertex region split, the four deployment failures —
was found by actually running the real system and reading the real
output, not by reasoning about what the code *should* do. Several of them
(the password BOM bug especially) would have been very hard to predict
from reading the code alone. That's the same principle the evaluation
harness itself is built on: measure the real thing, don't assume it.
