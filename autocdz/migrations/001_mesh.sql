CREATE TABLE IF NOT EXISTS mesh_connections (
    user_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    sealed_session TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    automation INTEGER NOT NULL DEFAULT 0,
    ai_consent INTEGER NOT NULL DEFAULT 0,
    next_sync REAL NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS connection_tickets (
    digest TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES mesh_connections(user_id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, external_id)
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE(assignment_id, external_id)
);
CREATE TABLE IF NOT EXISTS processing_jobs (
    id INTEGER PRIMARY KEY,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_until REAL NOT NULL DEFAULT 0,
    lease_token TEXT,
    error_code TEXT,
    UNIQUE(assignment_id, content_hash)
);
CREATE TABLE IF NOT EXISTS solutions (
    job_id INTEGER PRIMARY KEY REFERENCES processing_jobs(id) ON DELETE CASCADE,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    job_id INTEGER PRIMARY KEY REFERENCES processing_jobs(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0 CHECK(confirmed=0),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS integration_events (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_pending ON processing_jobs(state, lease_until);
PRAGMA user_version=1;
