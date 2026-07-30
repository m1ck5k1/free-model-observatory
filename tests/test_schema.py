"""Tests for the SQLite schema module."""

import json
import os
import tempfile

import pytest

from observatory.telemetry.schema import init_db


@pytest.fixture
def db_path():
    """Create a temporary database path for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    os.unlink(path)


def test_init_db_creates_tables(db_path):
    """Test that init_db creates all required tables."""
    conn = init_db(db_path)

    # Check tables exist
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]

    assert "models" in tables
    assert "tos_changes" in tables
    assert "model_detections" in tables
    assert "golden_sets" in tables
    assert "shadow_buffer" in tables
    assert "policy_history" in tables

    conn.close()


def test_init_db_sets_wal_mode(db_path):
    """Test that WAL mode is enabled."""
    conn = init_db(db_path)
    cursor = conn.execute("PRAGMA journal_mode")
    (mode,) = cursor.fetchone()
    assert mode == "wal"
    conn.close()


def test_init_db_sets_foreign_keys(db_path):
    """Test that foreign keys are enabled."""
    conn = init_db(db_path)
    cursor = conn.execute("PRAGMA foreign_keys")
    (fk,) = cursor.fetchone()
    assert fk == 1
    conn.close()


def test_models_table_constraints(db_path):
    """Test the models table schema and constraints."""
    conn = init_db(db_path)

    # Insert a model
    conn.execute(
        """INSERT INTO models (model_id, provider, first_seen, last_seen,
           context_window, modalities, retention_policy, eligible, source_confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("test-model", "test-provider", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
         4096, '["text"]', "no_train", 1, "high"),
    )
    conn.commit()

    # Verify it was inserted
    cursor = conn.execute("SELECT * FROM models WHERE model_id = ?", ("test-model",))
    row = cursor.fetchone()
    assert row is not None
    assert row["model_id"] == "test-model"
    assert row["eligible"] == 1

    # Duplicate insert should update (ON CONFLICT DO UPDATE)
    # Use the same INSERT ... ON CONFLICT pattern as the harvester
    conn.execute(
        """INSERT INTO models (model_id, provider, first_seen, last_seen,
           context_window, modalities, retention_policy, eligible, source_confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(provider, model_id) DO UPDATE SET
               last_seen = excluded.last_seen,
               context_window = excluded.context_window""",
        ("test-model", "test-provider", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z",
         8192, '["text"]', "no_train", 1, "high"),
    )
    conn.commit()

    cursor = conn.execute("SELECT * FROM models WHERE model_id = ?", ("test-model",))
    rows = cursor.fetchall()
    assert len(rows) == 1  # Same row, updated
    assert rows[0]["last_seen"] == "2026-01-02T00:00:00Z"

    conn.close()


def test_retention_policy_constraint(db_path):
    """Test that retention_policy CHECK constraint works."""
    conn = init_db(db_path)

    with pytest.raises(Exception):
        conn.execute(
            """INSERT INTO models (model_id, provider, first_seen, last_seen,
               retention_policy, eligible, source_confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("bad-model", "test", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
             "invalid_value", 1, "high"),
        )

    conn.close()


def test_source_confidence_constraint(db_path):
    """Test that source_confidence CHECK constraint works."""
    conn = init_db(db_path)

    with pytest.raises(Exception):
        conn.execute(
            """INSERT INTO models (model_id, provider, first_seen, last_seen,
               retention_policy, eligible, source_confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("bad-model", "test", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
             "no_train", 1, "unknown"),
        )

    conn.close()


def test_tos_changes_table(db_path):
    """Test the tos_changes table."""
    conn = init_db(db_path)

    conn.execute(
        """INSERT INTO tos_changes (provider, tos_url, old_hash, new_hash, detected_at)
           VALUES (?, ?, ?, ?, ?)""",
        ("openrouter", "https://example.com/tos", "abc123", "def456", "2026-01-01T00:00:00Z"),
    )
    conn.commit()

    cursor = conn.execute("SELECT * FROM tos_changes")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["notified"] == 0  # default

    conn.close()


def test_policy_history(db_path):
    """Test the policy_history table."""
    conn = init_db(db_path)

    conn.execute(
        """INSERT INTO policy_history (policy_version, generated_at, policy_json, chain_count)
           VALUES (?, ?, ?, ?)""",
        (1, "2026-01-01T00:00:00Z", json.dumps({"test": True}), 5),
    )
    conn.commit()

    cursor = conn.execute("SELECT * FROM policy_history")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["policy_version"] == 1

    conn.close()


def test_indexes_exist(db_path):
    """Test that key indexes are created."""
    conn = init_db(db_path)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index' ORDER BY name")
    indexes = [row[0] for row in cursor.fetchall()]

    assert "idx_models_eligible" in indexes
    assert "idx_models_provider" in indexes
    assert "idx_tos_changes_provider" in indexes
    assert "idx_shadow_replayed" in indexes
    assert "idx_policy_version" in indexes

    conn.close()


def test_idempotent_migration(db_path):
    """Test that running migrate twice doesn't error."""
    conn = init_db(db_path)
    conn.close()
    # Second init should be fine
    conn = init_db(db_path)
    conn.close()
