# Privacy: how this project maps to India's DPDP Act 2023

**This is not legal advice or a compliance certification.** It's an honest
mapping of this project's actual privacy controls to the Digital Personal
Data Protection Act, 2023's core principles, written for a portfolio
project — not a review of a production system. The Act's operational
detail is filled in by the DPDP Rules 2025; where this document says
"the Act requires," treat that as the common secondary-source reading at
the time this was written (checked October 2026), not a citation to the
Gazette text itself. A real deployment handling real patient data would
need a proper legal review, a Data Protection Officer / grievance
mechanism, and almost certainly things this project doesn't have (see
"Known gaps" below).

## Principle-by-principle mapping

### Consent
**DPDP requirement (as commonly summarized):** consent must be free,
specific, informed, unconditional, and unambiguous, with withdrawal about
as easy as giving it.

**What this project does:** `prompts/v1.md`/`v2.md` instruct the agent to
open every call with a spoken notice: *"this call may be recorded as text
transcripts for quality purposes, and no audio recording is kept"* —
before any personal data (name, phone number) is collected.

**Honest gap:** this is a **notice**, not DPDP-grade consent. It's spoken
once, there's no affirmative "yes" captured from the caller, and there's
no mechanism to withdraw consent mid-call short of hanging up. A real
deployment would need an explicit opt-in step and a way to decline while
still reaching a human.

### Purpose limitation
**DPDP requirement:** data collected for one stated purpose shouldn't be
reused for an unrelated one without fresh consent.

**What this project does:** every tool (`tools/`) is scoped to exactly
one purpose — booking, rescheduling, cancelling, or answering clinic-info
questions. Nothing in the schema or tool layer supports using collected
data (name, phone, appointment history) for anything else — no
marketing, no analytics on patient behavior, no cross-referencing between
callers. `tools/get_clinic_info.py` only reads static clinic facts; it
never touches caller data.

### Data minimization
**DPDP requirement:** collect only what's necessary for the stated
purpose.

**What this project does:** the only personal fields ever collected are
patient name and phone number (`tools/book_appointment.py` et al.) —
exactly what's needed to hold and identify an appointment. No address,
no medical history, no payment details, no demographic data. The system
prompt explicitly instructs the model to collect only intent/doctor/date/
time/name/phone (`prompts/v1.md`, "WHAT YOU DO").

### Storage limitation / no raw audio
**DPDP requirement:** don't retain personal data longer than the purpose
requires.

**What this project does:** `app/live_session.py` and `app/turn_logging.py`
persist only **text transcripts** of each turn — the raw PCM audio
stream is never written to disk or the database, by construction (the
Live API gives us audio bytes in memory to relay to the browser; nothing
in the code path writes them anywhere persistent). This is a hard design
constraint, not a retention policy applied after the fact — there's no
raw audio to delete because none is ever stored.

**Honest gap:** there's currently no automatic deletion/retention policy
for the text transcripts and appointment records themselves (`call_logs`,
`turn_logs`, `appointments` have no TTL or archival job). A real
deployment would need one.

### Security safeguards: PII masking
**DPDP requirement:** reasonable security safeguards against breach;
accountability for how personal data is handled.

**What this project does:** `tools/masking.py` scrubs patient names and
phone numbers out of everything written to `turn_logs` — including
inside free-text fields like a tool's human-readable `message`, not just
specifically-keyed fields (`mask_dict` does a two-pass scrub: collect
known PII values, then replace every occurrence of them in every string
in the log payload, recursively through nested lists/dicts). This was a
real bug I found and fixed during Phase 2 by reading the actual logged
output, not just writing the masking code and assuming it worked — see
`tools/masking.py`'s docstring.

**Important distinction:** the `appointments` table itself stores real
(unmasked) patient name and phone number — that's necessary to actually
run a clinic (you need the real number to call a patient back). Masking
applies specifically to the **audit/log trail**, where the only purpose
is debugging/quality review and the real value serves no function.

**Honest gap:** the underlying Postgres database has no encryption at
rest and no access-control layer beyond the OS/Docker boundary — fine for
local dev, not acceptable for real patient data. The admin dashboard
(`/admin.html`, `app/admin_api.py`) is gated by a single shared password
(HTTP Basic) rather than real per-user accounts, roles, or audit logging
— a minimal safeguard against the data being on a fully open page, not a
production access-control layer.

### Accuracy
**DPDP requirement:** keep personal data accurate and up to date.

**What this project does:** the confirmation protocol
(`tools/confirmation.py` + every write tool) exists specifically to
prevent *inaccurate* data from ever being stored — the agent must read
the patient's name, date, and time back to the caller and get explicit
agreement before any `book_appointment`/`reschedule_appointment`/
`cancel_appointment` call can succeed with `confirmed=true`. The Phase 4
ablation (`docs/results.md`) measured this directly: this is the
mechanism that exists to catch a misheard name or digit before it's
committed, even though the specific 40-scenario test set didn't happen to
exercise a case where it changed the outcome.

### No real/sensitive data in development
**Not a DPDP principle specifically, but the project's own hard
constraint:** every doctor, patient, phone number, and appointment used
anywhere in this codebase — seed data (`db/seed.py`), tests
(`tests/conftest.py`), eval scenarios (`eval/scenarios.yaml`) — is
synthetic. No real patient data has ever touched this system, by
construction, which sidesteps most DPDP concerns for the *development*
process even though they'd apply fully to a real deployment.

## Known gaps (if this were a real deployment, not a portfolio project)

- No explicit affirmative consent capture or in-call withdrawal mechanism
- No data retention/deletion policy or TTL on stored transcripts
- No encryption at rest; the database has no access-control layer beyond
  Docker, and the admin dashboard has only a shared password, not real
  per-user auth/roles/audit logging
- **Partial** data principal self-service: `/lookup.html` lets a caller
  *view* their own booking by phone number (added after this document was
  first written), but there's still no export/delete self-service, and no
  identity verification before showing the data (phone number alone is
  enough) — a real deployment would want an OTP step here
- No breach detection or Data Protection Board notification process
- No Data Protection Officer / grievance redressal contact
- No legal review of the consent notice's wording or placement

None of these block a portfolio/demo project. All of them would block a
real one handling real patients.
