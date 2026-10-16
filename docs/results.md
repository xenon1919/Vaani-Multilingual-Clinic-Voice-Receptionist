# Phase 4 results: iteration and ablations

Four full evaluation runs, all against the same 40-scenario set (`eval/scenarios.yaml`):

| run_id | tier | prompt | confirmation | model | scenarios | errors |
|---|---|---|---|---|---|---|
| `text-v1-full` | text | v1 | on | gemini-3.8-flash | 40 | 0 |
| `audio-v1-full` | audio (8-scenario subset) | v1 | on | gemini-3.8-live / gemini-3.8-flash | 8 | 0 |
| `text-v2-full` | text | v2 | on | gemini-3.8-flash | 40 | 3* |
| `text-v1-noconfirm` | text | v1 | **off** | gemini-3.8-flash | 40 | 0 |

\* 3 infrastructure errors (`402 RESOURCE_EXHAUSTED` — a Vertex AI billing credit issue unrelated to the model/prompt), not scoring failures. Excluded from the metrics below.

A fifth run — a model-version ablation (`gemini-3.8-flash` vs `gemini-2.5-flash`) — was started but stopped partway through and is **not included**. The spec marks this ablation "where possible"; Vertex AI's trial-project quota turned out to be far stricter than AI Studio's free tier was (see "Infrastructure note" below), making a second full 40-scenario run impractical in the time available. This is a deliberate scope cut, not an oversight.

## Why Phase 4 happened on Vertex AI, not AI Studio

Partway through this phase, the AI Studio API key in use turned out to have a **Prepay billing account attached** and was charged (₹500) — a direct violation of the project's zero-cost constraint. The fix was migrating to Vertex AI, authenticated via `gcloud auth application-default login` against a Google Cloud project carrying an active $300 free-trial credit, rather than an API key. See `docs/live_api_notes.md` for the technical details (the Live and text models need *different* Vertex regions — `us-central1` vs `global` — discovered by testing directly, not documented clearly anywhere).

**Infrastructure note**: Vertex AI's trial-project quota is noticeably tighter than AI Studio's free tier was — running scenarios with any real concurrency produced sustained `429` throttling. `eval/rate_limit.py` and both runner scripts were changed to run one request at a time (`MAX_CONCURRENT_REQUESTS = 1`, `SCENARIO_CONCURRENCY = 1`), which made every run in this phase slower (the `text-v1-noconfirm` run took ~1h45m instead of Phase 3's ~15 minutes) but reliable. This is a real cost of the billing fix, not a regression in the harness itself.

## Results table — by run

| Metric | v1 baseline (confirm on) | v2 (confirm on) | v1, confirm **off** |
|---|---|---|---|
| Task success | 100% | 100% | 100% |
| **Wrong booking rate** | 0% | 0% | **0%** |
| Slot accuracy | 100% | 100% | 100% |
| Tool call correctness | 100% | 100% | 100% |
| Confirmation compliance | 100% | 100% | n/a (disabled) |
| Correct refusal rate | 100% | 100% | 100% |
| **Entity error rate (names/numbers)** | **20%** | **0%** | 15% |
| Conversation quality (judge, /5) | 5.0 | 5.0 | 5.0 |
| Tool latency p50 / p95 | 14ms / 30ms | 14ms / 31ms | 14ms / 31ms |

(Audio tier, 8-scenario subset, prompt v1: 87.5% task success, 0% wrong booking, 791ms avg time-to-first-audio — see Phase 3 writeup for detail; not re-run for the ablations since the text tier is where prompt/confirmation changes are evaluated.)

## Error taxonomy (built from real Phase 3 data before writing v2)

Mined from every sub-5 judge score and every `entity_error` across the Phase 3 baseline runs:

1. **Name transliteration mismatch** (4 occurrences, Telugu/Hindi only) — the agent books the *correct* appointment but records the patient's name in native script (e.g. "టెస్ట్ కాలర్ 10") instead of the Latin-script name the caller actually gave ("Test Caller 10"). Conversationally flawless; an entity-accuracy failure in the stored data only.
2. **Redundant re-confirmation** (2 occurrences, audio only) — the agent asks "is this correct?" again immediately after the caller already agreed, burning turns.
3. **Ignoring a direct question** (1 occurrence, audio) — caller asks the consultation fee, agent asks "anything else?" instead of answering, forcing a repeat.
4. **Failure to close gracefully** (1 occurrence, audio) — caller says thanks/goodbye, agent re-states already-given information and pushes booking instead of ending the call.

## What `prompts/v2.md` changed

Four targeted additions to v1, one per taxonomy category:
- Explicit instruction to transliterate patient names to Latin letters before calling any tool, regardless of what script the caller spoke in.
- Explicit instruction to act on confirmation immediately, not ask for it twice.
- Explicit instruction to answer the caller's actual question before asking anything else.
- Explicit instruction on how to end a call gracefully without repeating information or upselling.

(Full diff: `prompts/v1.md` vs `prompts/v2.md`.)

## Honest failure analyses

### 1. Name transliteration — confirmed fixed, not just hypothesized

Entity error rate dropped from **20% → 0%** between v1 and v2 on the identical scenario set, with every other metric holding at 100%/5.0. This is as close to a controlled, isolated confirmation as this harness can give: the only prompt change relevant to this failure mode was the transliteration instruction, and the failure mode it targeted disappeared completely. Worth treating as validated, not just "looks better."

### 2. Confirmation ablation: it didn't change wrong-booking rate — and that's worth taking seriously, not spinning

The original hypothesis going into this ablation was that disabling the confirm-before-write step would increase the wrong-booking rate, since there'd be no human-in-the-loop readback to catch a misheard detail before it's committed. That didn't happen: `wrong_booking` stayed at 0% with confirmation off, and the entity error rate (15%) was actually *slightly lower* than the confirmation-on v1 baseline (20%) — almost certainly noise at this sample size (40 scenarios, few entity-error events), not a real effect.

The honest conclusion: **this scenario set doesn't exercise the condition where confirmation would matter** — none of the 40 scenarios deliberately inject an ASR/transcription error that confirmation would need to catch. Text-tier scenarios are typed/generated conversation, not garbled audio, so the model rarely mishears anything in the first place. The audio tier (real speech, real ASR) is where confirmation's actual value — catching a misheard digit before it's booked — would show up, and even there, the one real failure we saw (`te_ambiguous_noisy_010`) was a *caller-side* mis-speak, not something confirmation prevented or should have prevented (the caller never got far enough to confirm). This ablation's real finding is a gap in the scenario set, not evidence that confirmation is unnecessary — the scenarios need an explicit "deliberately corrupt one detail and see if confirmation catches it" category to actually test this, which would be a natural next addition to `eval/build_scenarios.py`.

### 3. The one real task failure: turn-budget exhaustion compounded by a caller-side error

`te_ambiguous_noisy_010` (audio tier) is the only task failure across all 125 scored scenario-runs in this phase. The simulated caller mis-spoke a phone number while reading it digit-by-digit (dropped a zero: said 9 digits of a 10-digit number), and separately, the agent asked "shall I confirm?" twice in a row at the end of the conversation (taxonomy category 2, which v2 fixes) — the combination ate enough turns that the 12-turn cap hit before the real confirmation completed. This is a compounding failure, not a single root cause: either the caller-side fix (none needed, that's a harness limitation, not a product defect) or the v2 redundant-confirmation fix alone would likely have been enough to avoid it. Worth re-running this exact scenario under v2 (audio tier) as a targeted regression check, not done here due to the Vertex quota constraint described above.

## Summary

- **v2 prompt change validated**: entity error rate 20% → 0%, confirmed via controlled before/after on identical scenarios.
- **Confirmation ablation result is a negative-but-informative finding**: no measurable effect on wrong-booking rate *in this scenario set*, with a clear, specific explanation of why (no scenario currently injects the condition confirmation exists to catch) rather than a claim that confirmation doesn't matter.
- **Model-version ablation**: deliberately deprioritized given Vertex AI's quota constraints; flagged as the clearest next step if quota allows.
- **Zero wrong bookings** across all 125 scored scenario-runs in this phase — the project's most important metric held at 0% through every condition tested.
