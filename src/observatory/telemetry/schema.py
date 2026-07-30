"""SQLite schema for the Free Model Observatory state store.

Versioned. Migrations run on startup from the current schema version.
"""

import os
import sqlite3
from pathlib import Path

# Default database path
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "data",
    "observatory.db",
)

SCHEMA_VERSION = 1


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode and foreign keys enabled."""
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    # Ensure directory exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Run migrations to bring the database to the current schema version."""
    cursor = conn.execute("PRAGMA user_version")
    (current_version,) = cursor.fetchone()

    if current_version < 1:
        _migrate_v1(conn)
        conn.execute("PRAGMA user_version = 1")

    conn.commit()


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """Initial schema for v0.1."""
    conn.executescript("""
        -- Models registry
        CREATE TABLE IF NOT EXISTS models (
            model_id        TEXT NOT NULL,
            provider        TEXT NOT NULL,
            first_seen      TEXT NOT NULL,  -- ISO 8601 UTC
            last_seen       TEXT NOT NULL,  -- ISO 8601 UTC
            context_window  INTEGER,
            modalities      TEXT,           -- JSON array
            declared_rate_limit TEXT,       -- JSON object
            licence         TEXT,
            tos_url         TEXT,
            tos_hash        TEXT,
            retention_policy TEXT NOT NULL DEFAULT 'unknown'
                            CHECK (retention_policy IN ('no_train', 'trains_on_input', 'unknown')),
            jurisdiction    TEXT,
            eligible        INTEGER NOT NULL DEFAULT 0,
            ineligible_reason TEXT,
            source_confidence TEXT NOT NULL DEFAULT 'high'
                            CHECK (source_confidence IN ('high', 'low')),
            PRIMARY KEY (provider, model_id)
        );

        -- ToS change tracking
        CREATE TABLE IF NOT EXISTS tos_changes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            provider        TEXT NOT NULL,
            tos_url         TEXT NOT NULL,
            old_hash        TEXT,
            new_hash        TEXT NOT NULL,
            detected_at     TEXT NOT NULL,  -- ISO 8601 UTC
            notified        INTEGER NOT NULL DEFAULT 0
        );

        -- New model detection tracking
        CREATE TABLE IF NOT EXISTS model_detections (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            provider        TEXT NOT NULL,
            model_id        TEXT NOT NULL,
            detected_at     TEXT NOT NULL,  -- ISO 8601 UTC
            notified        INTEGER NOT NULL DEFAULT 0
        );

        -- Golden set versions
        CREATE TABLE IF NOT EXISTS golden_sets (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            job_class       TEXT NOT NULL,
            version         TEXT NOT NULL,
            item_count      INTEGER NOT NULL,
            created_at      TEXT NOT NULL,
            UNIQUE(job_class, version)
        );

        -- Shadow scoring buffer (async replay queue)
        CREATE TABLE IF NOT EXISTS shadow_buffer (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            job_class       TEXT NOT NULL,
            input_hash      TEXT NOT NULL,  -- SHA-256 of input
            input_text      TEXT NOT NULL,
            production_model TEXT NOT NULL,
            production_output TEXT,
            captured_at     TEXT NOT NULL,
            replayed        INTEGER NOT NULL DEFAULT 0,
            replayed_at     TEXT
        );

        -- Policy history
        CREATE TABLE IF NOT EXISTS policy_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_version  INTEGER NOT NULL,
            generated_at    TEXT NOT NULL,
            policy_json     TEXT NOT NULL,
            chain_count     INTEGER NOT NULL
        );

        -- Indexes
        CREATE INDEX IF NOT EXISTS idx_models_eligible ON models(eligible);
        CREATE INDEX IF NOT EXISTS idx_models_provider ON models(provider);
        CREATE INDEX IF NOT EXISTS idx_tos_changes_provider ON tos_changes(provider);
        CREATE INDEX IF NOT EXISTS idx_shadow_replayed ON shadow_buffer(replayed);
        CREATE INDEX IF NOT EXISTS idx_policy_version ON policy_history(policy_version DESC);
    """)


def init_db(db_path: str | None = None) -> sqlite3.Connection:
    """Initialize the database: get connection, run migrations, return connection."""
    conn = get_connection(db_path)
    migrate(conn)
    return conn
