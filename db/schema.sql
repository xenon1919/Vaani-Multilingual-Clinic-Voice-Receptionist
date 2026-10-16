-- Clinic schema. Synthetic data only — no real patient data, ever.

CREATE TABLE IF NOT EXISTS doctors (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    specialty       TEXT NOT NULL,
    consultation_fee NUMERIC(8, 2) NOT NULL
);

-- Unique (doctor_id, slot_date, slot_time) makes double booking impossible
-- at the database level: there is only ever one row to book per doctor/time.
CREATE TABLE IF NOT EXISTS slots (
    id          SERIAL PRIMARY KEY,
    doctor_id   INTEGER NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
    slot_date   DATE NOT NULL,
    slot_time   TIME NOT NULL,
    is_booked   BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (doctor_id, slot_date, slot_time)
);

-- One appointment per slot (slot_id UNIQUE) — the second layer of the
-- double-booking guarantee, enforced even if is_booked is set incorrectly.
CREATE TABLE IF NOT EXISTS appointments (
    id              SERIAL PRIMARY KEY,
    slot_id         INTEGER NOT NULL UNIQUE REFERENCES slots(id) ON DELETE CASCADE,
    patient_name    TEXT NOT NULL,
    phone_number    TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('booked', 'cancelled', 'rescheduled')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Backs the two-step confirmation protocol for book/reschedule/cancel: a
-- write tool's first call creates one of these and returns its token for
-- the agent to read back; the second call (confirmed=True) must present a
-- valid, unexpired token. expires_at is the "slot hold with an expiry
-- timestamp" design calls for -- a plain row, no Redis.
--
-- slot_id is set for book/reschedule (it reserves the NEW slot against
-- other callers while this caller is confirming) and NULL for cancel
-- (freeing a slot needs no reservation). result_appointment_id is filled
-- in on success so a replayed confirm with the same token is idempotent
-- (returns the existing appointment instead of erroring or double-booking).
CREATE TABLE IF NOT EXISTS pending_confirmations (
    token                   TEXT PRIMARY KEY,
    call_id                 TEXT NOT NULL,
    tool_name               TEXT NOT NULL,
    slot_id                 INTEGER REFERENCES slots(id) ON DELETE CASCADE,
    payload                 JSONB NOT NULL,
    result_appointment_id   INTEGER REFERENCES appointments(id),
    expires_at              TIMESTAMPTZ NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pending_confirmations_slot_id ON pending_confirmations(slot_id);

CREATE TABLE IF NOT EXISTS call_logs (
    id              SERIAL PRIMARY KEY,
    call_id         TEXT NOT NULL UNIQUE,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    caller_language TEXT,
    outcome         TEXT
);

-- transcript/tool_args/tool_result must hold masked PII only (names,
-- phone numbers) — enforced in the logging code, not here.
CREATE TABLE IF NOT EXISTS turn_logs (
    id          SERIAL PRIMARY KEY,
    call_id     TEXT NOT NULL REFERENCES call_logs(call_id) ON DELETE CASCADE,
    turn_index  INTEGER NOT NULL,
    role        TEXT NOT NULL,
    transcript  TEXT,
    tool_name   TEXT,
    tool_args   JSONB,
    tool_result JSONB,
    latency_ms  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_turn_logs_call_id ON turn_logs(call_id);

-- Phase 3 evaluation harness. One eval_runs row per "python -m eval.run_*"
-- invocation; one eval_results row per scenario within that run.
-- UNIQUE(run_id, scenario_id) is what makes a run resumable: re-running
-- the same run_id skips scenarios that already have a row.
CREATE TABLE IF NOT EXISTS eval_runs (
    run_id          TEXT PRIMARY KEY,
    tier            TEXT NOT NULL CHECK (tier IN ('text', 'audio')),
    prompt_version  TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS eval_results (
    id              SERIAL PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES eval_runs(run_id) ON DELETE CASCADE,
    scenario_id     TEXT NOT NULL,
    language        TEXT NOT NULL,
    intent          TEXT NOT NULL,
    difficulty_tags TEXT[] NOT NULL DEFAULT '{}',
    metrics         JSONB NOT NULL,
    judge_scores    JSONB,
    transcript      JSONB NOT NULL,
    tool_calls      JSONB NOT NULL,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, scenario_id)
);
CREATE INDEX IF NOT EXISTS idx_eval_results_run_id ON eval_results(run_id);
