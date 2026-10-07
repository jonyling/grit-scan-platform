-- GRIT-Scan signal pool. The local working store; Dataverse stays the system of record (grit.dataverse.sync).
-- Shared facts (signals) are kept apart from reader-scoped judgements (assessments, feedback, packages).
-- JSON columns hold TEXT. Timestamps are ISO 8601 UTC strings.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,           -- e.g. 2026-10-07-urs-tw
    run_date      TEXT NOT NULL,
    pack          TEXT NOT NULL,
    kind          TEXT NOT NULL,              -- READER_SCAN, DIGEST, MANUAL
    status        TEXT NOT NULL,              -- STARTED, COMPLETE, FAILED
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    config_json   TEXT NOT NULL DEFAULT '{}',
    notes         TEXT
);

-- One row per page. The id is a hash of the canonical URL, so a page found twice is one signal.
CREATE TABLE IF NOT EXISTS signals (
    signal_id     TEXT PRIMARY KEY,
    url           TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    publisher     TEXT,
    published_at  TEXT,                       -- absent stays NULL, never the fetch time
    lane          TEXT NOT NULL,              -- BRAVE, CURRENTS, GDELT, SPHERA, MANUAL
    text          TEXT NOT NULL DEFAULT '',
    content_hash  TEXT NOT NULL,
    doc_type      TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_signals_published ON signals (published_at);

-- Which query found a signal in which run. Several queries can find one page.
CREATE TABLE IF NOT EXISTS sightings (
    signal_id     TEXT NOT NULL REFERENCES signals (signal_id),
    run_id        TEXT NOT NULL REFERENCES runs (run_id),
    query_id      TEXT NOT NULL,
    query         TEXT NOT NULL,
    lane          TEXT,                       -- query-plan lane, e.g. CONDITION, PROJECT, SUPPLIER
    target_id     TEXT,                       -- the register row the query was written for
    rank          INTEGER,
    PRIMARY KEY (signal_id, run_id, query_id)
);

-- Embeddings. The same text and model give the same vector, so a vector is reused across runs.
CREATE TABLE IF NOT EXISTS vectors (
    signal_id     TEXT NOT NULL REFERENCES signals (signal_id),
    model         TEXT NOT NULL,
    task          TEXT NOT NULL,
    dimensions    INTEGER NOT NULL,
    text_hash     TEXT NOT NULL,
    vector_json   TEXT NOT NULL,
    PRIMARY KEY (signal_id, model, task, dimensions)
);

-- Deterministic results tied to a signal for one run and pack. Every value is reconstructable from the logged inputs.
CREATE TABLE IF NOT EXISTS assessments (
    signal_id           TEXT NOT NULL REFERENCES signals (signal_id),
    run_id              TEXT NOT NULL REFERENCES runs (run_id),
    pack                TEXT NOT NULL,
    decision            TEXT,                 -- KEEP, DROP
    drop_reason         TEXT,
    rung                TEXT,                 -- DIRECT, COUNTERPARTY, COMPETITOR, PERIMETER, SECTOR, THEME
    link_json           TEXT NOT NULL DEFAULT '{}',
    themes_json         TEXT NOT NULL DEFAULT '[]',
    risk_cascade_json   TEXT NOT NULL DEFAULT '[]',
    condition_ids_json  TEXT NOT NULL DEFAULT '[]',
    exposure_json       TEXT NOT NULL DEFAULT '[]',
    priority_json       TEXT NOT NULL DEFAULT '{}',
    tier                INTEGER,
    harm_json           TEXT NOT NULL DEFAULT '{}',
    harm_verdict        TEXT,                 -- HIGH_NEGATIVE_IMPACT, POTENTIAL_HIGH, NOT_HIGH
    opportunity_cost    TEXT,
    trend               TEXT,
    risk_level          TEXT,
    relevance_basis     TEXT,
    evidence_confidence TEXT,
    corroboration       INTEGER,
    red_rule_json       TEXT NOT NULL DEFAULT '{}',
    red                 INTEGER NOT NULL DEFAULT 0,
    assessed_at         TEXT NOT NULL,
    PRIMARY KEY (signal_id, run_id, pack)
);

-- The six-question finding card from the loop. Draft for review.
CREATE TABLE IF NOT EXISTS findings (
    finding_id    TEXT NOT NULL,
    run_id        TEXT NOT NULL REFERENCES runs (run_id),
    signal_id     TEXT NOT NULL REFERENCES signals (signal_id),
    confidence    TEXT,
    status        TEXT NOT NULL,
    card_json     TEXT NOT NULL,
    PRIMARY KEY (run_id, finding_id)
);

-- Layer 1: one row per standing condition per run. The agent proposes; deterministic checks validate.
CREATE TABLE IF NOT EXISTS condition_assessments (
    run_id          TEXT NOT NULL REFERENCES runs (run_id),
    pack            TEXT NOT NULL,
    condition_id    TEXT NOT NULL,
    condition_name  TEXT NOT NULL,
    movement        TEXT,                     -- ESCALATING, EASING, NO_MATERIAL_CHANGE
    trend_label     TEXT,                     -- reader-facing wording, e.g. "Easing"
    confidence      TEXT,                     -- HIGH, MEDIUM, LOW
    summary         TEXT,
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL,            -- VALIDATED, NO_EVIDENCE, WITHHELD
    violations_json TEXT NOT NULL DEFAULT '[]',
    ranking_json    TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, condition_id)
);

-- Layer 5 coverage: every named project, supplier, customer and hazard is checked, and "nothing found" is recorded.
CREATE TABLE IF NOT EXISTS exposure_checks (
    run_id          TEXT NOT NULL REFERENCES runs (run_id),
    pack            TEXT NOT NULL,
    item_type       TEXT NOT NULL,            -- PROJECT, SUPPLIER, CUSTOMER, HAZARD
    item_id         TEXT NOT NULL,
    item_name       TEXT NOT NULL,
    signal_ids_json TEXT NOT NULL DEFAULT '[]',
    summary         TEXT,
    status          TEXT NOT NULL,            -- FOUND, NOTHING_FOUND, WITHHELD
    PRIMARY KEY (run_id, item_type, item_id)
);

-- Watchpoints carry across weeks until a named person closes them.
CREATE TABLE IF NOT EXISTS watchpoints (
    watchpoint_id         TEXT PRIMARY KEY,
    pack                  TEXT NOT NULL,
    title                 TEXT NOT NULL,
    detail                TEXT NOT NULL,
    keywords_json         TEXT NOT NULL DEFAULT '[]',
    status                TEXT NOT NULL,      -- OPEN, ESCALATED, CLOSED
    opened_on             TEXT NOT NULL,
    last_evidence_on      TEXT,
    last_checked_run      TEXT,
    cycles_without_update INTEGER NOT NULL DEFAULT 0,
    closed_by             TEXT,
    closed_on             TEXT
);
CREATE TABLE IF NOT EXISTS watchpoint_updates (
    watchpoint_id   TEXT NOT NULL REFERENCES watchpoints (watchpoint_id),
    run_id          TEXT NOT NULL,
    signal_ids_json TEXT NOT NULL DEFAULT '[]',
    note            TEXT NOT NULL,
    cycles_before   INTEGER NOT NULL DEFAULT 0,  -- the quiet count before this run, so a rerun of the week recomputes from it
    PRIMARY KEY (watchpoint_id, run_id)
);

-- Critic results that changed or withheld a draft. The brief prints them as the QA and correction log.
CREATE TABLE IF NOT EXISTS qa_log (
    qa_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    item            TEXT NOT NULL,
    prior           TEXT,
    correction      TEXT NOT NULL,
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    origin          TEXT NOT NULL             -- CRITIC, CHECK, HUMAN
);

-- Reader feedback. It is the Profiler's memory. It changes a reader's view, never a shared fact.
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      TEXT NOT NULL,
    signal_id       TEXT NOT NULL REFERENCES signals (signal_id),
    decision        TEXT NOT NULL,            -- APPROVE, REJECT, WATCH
    rationale       TEXT,
    submitted_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_feedback_profile ON feedback (profile_id);

-- Spec 29. A request from the workbook, the dashboard, or the chat. Only an ACTIVE request assigns P1.
CREATE TABLE IF NOT EXISTS management_requests (
    request_id      TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT,
    requested_by    TEXT NOT NULL,
    request_date    TEXT NOT NULL,
    kind            TEXT NOT NULL,            -- CONCERN, TOPIC, TREND, KEYWORD
    keywords_json   TEXT NOT NULL DEFAULT '[]',
    country         TEXT,
    lob             TEXT,
    status          TEXT NOT NULL,            -- PROPOSED, ACTIVE, ON_HOLD, FULFILLED, CLOSED
    origin          TEXT NOT NULL,            -- WORKBOOK, DASHBOARD, CHAT
    profile_id      TEXT,
    pack            TEXT,                     -- the reader pack it scopes to; NULL applies to every pack
    queries_json    TEXT NOT NULL DEFAULT '[]',
    source          TEXT                      -- where it came from, e.g. "Workbook sheet 10, request 1"
);

-- Spec 18. Append-only. The Supervisor is the only writer.
CREATE TABLE IF NOT EXISTS workflow_states (
    row_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       TEXT NOT NULL,
    execution_id    TEXT NOT NULL,
    sequence        INTEGER NOT NULL,
    state           TEXT NOT NULL,
    previous_state  TEXT,
    transition_type TEXT NOT NULL,
    actor_id        TEXT NOT NULL,
    recorded_at     TEXT NOT NULL,
    row_json        TEXT NOT NULL,
    UNIQUE (signal_id, execution_id, sequence)
);
CREATE TRIGGER IF NOT EXISTS workflow_states_no_update BEFORE UPDATE ON workflow_states
BEGIN SELECT RAISE(ABORT, 'workflow_states is append-only'); END;
CREATE TRIGGER IF NOT EXISTS workflow_states_no_delete BEFORE DELETE ON workflow_states
BEGIN SELECT RAISE(ABORT, 'workflow_states is append-only'); END;

-- Output packages: one per reader per run. The hash names the exact content a reviewer approved.
CREATE TABLE IF NOT EXISTS packages (
    package_id      TEXT NOT NULL,
    version         INTEGER NOT NULL,
    run_id          TEXT NOT NULL,
    profile_id      TEXT NOT NULL,
    status          TEXT NOT NULL,            -- DRAFT_FOR_REVIEW, APPROVED, RETURNED_FOR_REWORK, REJECTED
    content_hash    TEXT NOT NULL,
    membership_json TEXT NOT NULL,
    files_json      TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    decided_by      TEXT,
    decided_at      TEXT,
    PRIMARY KEY (package_id, version)
);

-- RED alerts. A draft email and the rule that raised it. A named person sends or dismisses it.
CREATE TABLE IF NOT EXISTS alerts (
    alert_id        TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    signal_id       TEXT NOT NULL REFERENCES signals (signal_id),
    recipients_json TEXT NOT NULL,
    rule_json       TEXT NOT NULL,
    status          TEXT NOT NULL,            -- DRAFT, SENT_BY_HUMAN, DISMISSED
    draft_path      TEXT,
    created_at      TEXT NOT NULL,
    decided_by      TEXT,
    decided_at      TEXT
);

-- AIRIS handoff: signals the BA head approved, packaged for import.
CREATE TABLE IF NOT EXISTS airis_exports (
    export_id       TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    approved_by     TEXT NOT NULL,
    signal_ids_json TEXT NOT NULL,
    path            TEXT NOT NULL,
    content_hash    TEXT NOT NULL
);

-- Every agent call and every logged function call (specs 17 and 27).
CREATE TABLE IF NOT EXISTS execution_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT,
    actor_type      TEXT NOT NULL,            -- AGENT, FUNCTION
    actor           TEXT NOT NULL,            -- e.g. Assessor, Critic, ScoreRisk
    version         TEXT,
    model           TEXT,
    subject         TEXT,
    input_hash      TEXT,
    output_hash     TEXT,
    status          TEXT NOT NULL,
    detail_json     TEXT NOT NULL DEFAULT '{}',
    logged_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    message_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      TEXT NOT NULL,
    role            TEXT NOT NULL,            -- user, assistant
    content         TEXT NOT NULL,
    citations_json  TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL
);

-- Dataverse sync bookkeeping: what was pushed, under which content hash.
CREATE TABLE IF NOT EXISTS sync_state (
    signal_id       TEXT PRIMARY KEY,
    content_hash    TEXT NOT NULL,
    dataverse_id    TEXT,
    synced_at       TEXT NOT NULL
);
