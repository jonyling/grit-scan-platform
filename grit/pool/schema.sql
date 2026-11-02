-- GRIT-Scan signal pool. The local working store; Dataverse stays the system of record (grit.dataverse.sync).
-- Shared facts (signals) are kept apart from reader-scoped judgements (assessments, feedback, packages).
-- JSON columns hold TEXT. Timestamps are ISO 8601 UTC strings.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS weekly_runs (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    version INTEGER NOT NULL DEFAULT 1,
    evidence_hash TEXT NOT NULL,
    search_limit INTEGER NOT NULL CHECK(search_limit >= 0),
    searches_used INTEGER NOT NULL DEFAULT 0 CHECK(searches_used >= 0),
    revisions_used INTEGER NOT NULL DEFAULT 0 CHECK(revisions_used BETWEEN 0 AND 2),
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weekly_calls (
    call_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    version INTEGER NOT NULL,
    role TEXT NOT NULL,
    task TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT NOT NULL,
    output_hash TEXT,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_weekly_call_replay ON weekly_calls(run_id, version, role, task, input_hash);
CREATE TABLE IF NOT EXISTS weekly_selections (
    selection_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    version INTEGER NOT NULL,
    signal_id TEXT NOT NULL REFERENCES signals(signal_id),
    call_id INTEGER NOT NULL REFERENCES weekly_calls(call_id),
    selection_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weekly_issues (
    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    version INTEGER NOT NULL,
    issue_id TEXT NOT NULL,
    call_id INTEGER NOT NULL REFERENCES weekly_calls(call_id),
    status TEXT NOT NULL CHECK(status IN ('OPEN', 'RESOLVED')),
    issue_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weekly_reports (
    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    version INTEGER NOT NULL,
    profile_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    selection_hash TEXT NOT NULL,
    critique_call_id INTEGER NOT NULL REFERENCES weekly_calls(call_id),
    status TEXT NOT NULL,
    brief_json TEXT NOT NULL,
    routed_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weekly_memory (
    memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id TEXT NOT NULL,
    package_version INTEGER NOT NULL,
    call_id INTEGER NOT NULL REFERENCES weekly_calls(call_id),
    proposal_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PROPOSED',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weekly_evidence (
    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    signal_id TEXT NOT NULL REFERENCES signals(signal_id),
    kind TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weekly_memory_admissions (
    memory_id INTEGER PRIMARY KEY REFERENCES weekly_memory(memory_id),
    reviewer_id TEXT NOT NULL,
    intent TEXT NOT NULL CHECK(intent IN ('ADMIT', 'REJECT')),
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS weekly_reports_no_update BEFORE UPDATE ON weekly_reports
BEGIN SELECT RAISE(ABORT, 'Weekly report snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS weekly_reports_no_delete BEFORE DELETE ON weekly_reports
BEGIN SELECT RAISE(ABORT, 'Weekly report snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS weekly_selections_no_update BEFORE UPDATE ON weekly_selections
BEGIN SELECT RAISE(ABORT, 'Weekly selections are append-only'); END;
CREATE TRIGGER IF NOT EXISTS weekly_selections_no_delete BEFORE DELETE ON weekly_selections
BEGIN SELECT RAISE(ABORT, 'Weekly selections are append-only'); END;
CREATE TRIGGER IF NOT EXISTS weekly_issues_no_update BEFORE UPDATE ON weekly_issues
BEGIN SELECT RAISE(ABORT, 'Weekly critique history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS weekly_issues_no_delete BEFORE DELETE ON weekly_issues
BEGIN SELECT RAISE(ABORT, 'Weekly critique history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS weekly_memory_admissions_no_update BEFORE UPDATE ON weekly_memory_admissions
BEGIN SELECT RAISE(ABORT, 'Memory admissions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS weekly_memory_admissions_no_delete BEFORE DELETE ON weekly_memory_admissions
BEGIN SELECT RAISE(ABORT, 'Memory admissions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS weekly_calls_no_rewrite BEFORE UPDATE ON weekly_calls WHEN OLD.status != 'STARTED'
BEGIN SELECT RAISE(ABORT, 'Completed weekly invocations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS weekly_calls_no_delete BEFORE DELETE ON weekly_calls
BEGIN SELECT RAISE(ABORT, 'Weekly invocation history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS weekly_evidence_no_update BEFORE UPDATE ON weekly_evidence
BEGIN SELECT RAISE(ABORT, 'Weekly evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS weekly_evidence_no_delete BEFORE DELETE ON weekly_evidence
BEGIN SELECT RAISE(ABORT, 'Weekly evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS weekly_memory_no_update BEFORE UPDATE ON weekly_memory
BEGIN SELECT RAISE(ABORT, 'Memory proposals are append-only'); END;
CREATE TRIGGER IF NOT EXISTS weekly_memory_no_delete BEFORE DELETE ON weekly_memory
BEGIN SELECT RAISE(ABORT, 'Memory proposals are append-only'); END;

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
    implication     TEXT,                     -- a key risk's implication for the business area (spec 36)
    options_json    TEXT NOT NULL DEFAULT '[]', -- the Proposer's checked options, without its reasoning
    opportunity     TEXT,                     -- a Group condition's opportunity pathway (spec 37)
    counter_indicator TEXT,                   -- what would show the assessment is wrong (spec 37)
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
    status          TEXT NOT NULL,            -- DRAFT, SENT_TO_DUTY, SENT_BY_HUMAN, DISMISSED
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

-- Co-Pilot web searches (spec 41): one row per search, for the per-reader daily budget. Private to the reader's own screen.
CREATE TABLE IF NOT EXISTS copilot_web_searches (
    search_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      TEXT NOT NULL,
    day             TEXT NOT NULL,            -- YYYY-MM-DD, local date of the search
    engine          TEXT NOT NULL,            -- BRAVE, CURRENTS, NONE
    query           TEXT NOT NULL,
    results         INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,            -- OK, EMPTY, FAILED
    searched_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_copilot_web_day ON copilot_web_searches (profile_id, day);

-- Web pages a reader added to the pool from a Co-Pilot answer (spec 41). The next scan of each pack assesses them like any
-- other page; nothing here keeps or ranks a page.
CREATE TABLE IF NOT EXISTS pool_additions (
    signal_id       TEXT NOT NULL REFERENCES signals (signal_id),
    pack            TEXT NOT NULL,
    added_by        TEXT NOT NULL,
    added_at        TEXT NOT NULL,
    status          TEXT NOT NULL,            -- PENDING, SCANNED
    run_id          TEXT,                     -- the run that assessed it
    PRIMARY KEY (signal_id, pack)
);

-- Events (spec 42): pages that tell the same story, so retrieval returns one item per story with all its outlets.
CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    lead_signal_id  TEXT NOT NULL REFERENCES signals (signal_id),  -- the best-labelled member: lowest tier, then earliest
    first_date      TEXT,
    last_date       TEXT,
    members         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_members (
    signal_id       TEXT PRIMARY KEY REFERENCES signals (signal_id),
    event_id        TEXT NOT NULL REFERENCES events (event_id),
    joined_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_event_members_event ON event_members (event_id);

-- Daily digests (spec 42): per pack and day, what is new. The Co-Pilot's whats_new tool reads them.
CREATE TABLE IF NOT EXISTS digests (
    pack            TEXT NOT NULL,
    day             TEXT NOT NULL,
    digest_json     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (pack, day)
);

-- The weekly source sweep (spec 42): one Brave query per stale or blocked source, at most once in SWEEP_DAYS.
CREATE TABLE IF NOT EXISTS source_sweeps (
    feed_id         TEXT NOT NULL,
    day             TEXT NOT NULL,
    query           TEXT NOT NULL,
    results         INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,            -- OK, EMPTY, FAILED
    PRIMARY KEY (feed_id, day)
);
