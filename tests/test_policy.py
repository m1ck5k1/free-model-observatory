"""Tests for the policy generator."""

import json
import os
import tempfile

import pytest

from observatory.policy.generate import (
    FLOOR_MODEL,
    generate_policy,
    load_sensitive_terms,
    load_thresholds,
)


@pytest.fixture
def db_path():
    """Create a temporary database path with test data."""
    from observatory.telemetry.schema import init_db

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name

    conn = init_db(path)

    # Insert some test models
    models = [
        ("model-a", "openrouter", "no_train", 1),
        ("model-b", "groq", "no_train", 1),
        ("model-c", "google", "trains_on_input", 0),
        ("model-d", "mistral", "no_train", 1),
        ("local-floor", "ollama", "no_train", 1),
    ]

    for model_id, provider, retention, eligible in models:
        conn.execute(
            """INSERT INTO models (model_id, provider, first_seen, last_seen,
               retention_policy, eligible, source_confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (model_id, provider, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
             retention, eligible, "high"),
        )
    conn.commit()
    conn.close()

    yield path
    os.unlink(path)


def test_load_thresholds():
    """Test loading thresholds config."""
    thresholds = load_thresholds()
    assert "grading" in thresholds
    assert "liveness" in thresholds
    assert "cadence" in thresholds
    assert thresholds["grading"]["judge_model"] == "openrouter/openai/gpt-4o-mini"


def test_load_sensitive_terms():
    """Test loading sensitive terms from example file."""
    terms = load_sensitive_terms()
    assert isinstance(terms, dict)
    assert "hard_identifiers" in terms
    assert "soft_identifiers" in terms
    # Example file has hard identifiers (patterns only)
    assert len(terms["hard_identifiers"]) > 0
    assert "serial:" in terms["hard_identifiers"]
    # Soft identifiers are all commented out in example
    # (actual client roster lives in private config)
    soft = terms.get("soft_identifiers") or []
    assert len(soft) >= 0


def test_generate_policy_structure(db_path):
    """Test that generated policy has the correct structure."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        output_path = f.name

    policy = generate_policy(db_path=db_path, output_path=output_path)

    assert "generated_at" in policy
    assert "policy_version" in policy
    assert policy["policy_version"] == 1
    assert "job_classes" in policy

    # Verify all expected job classes are present
    assert "tool_call" in policy["job_classes"]
    assert "intent_classify" in policy["job_classes"]
    assert "voice_turn" in policy["job_classes"]
    assert "summarise" in policy["job_classes"]
    assert "code" in policy["job_classes"]
    assert "long_context_retrieval" in policy["job_classes"]

    # Verify tool_call structure
    tc = policy["job_classes"]["tool_call"]
    assert "chain" in tc
    assert "constraints" in tc
    assert "updated_at" in tc

    # Verify floor is always at the end
    assert tc["chain"][-1] == FLOOR_MODEL

    # Verify output file was written
    with open(output_path) as f:
        written = json.load(f)
    assert written["policy_version"] == 1

    os.unlink(output_path)


def test_generate_policy_chain_content(db_path):
    """Test that the chain contains expected models."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        output_path = f.name

    policy = generate_policy(db_path=db_path, output_path=output_path)
    chain = policy["job_classes"]["tool_call"]["chain"]

    # Should include eligible models
    assert "openrouter/model-a" in chain
    assert "groq/model-b" in chain
    assert "mistral/model-d" in chain
    assert FLOOR_MODEL in chain

    # Should NOT include ineligible models
    assert "google/model-c" not in chain

    # Floor should be last
    assert chain[-1] == FLOOR_MODEL

    os.unlink(output_path)


def test_generate_policy_version_increment(db_path):
    """Test that policy version increments."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        output_path = f.name

    policy1 = generate_policy(db_path=db_path, output_path=output_path)
    assert policy1["policy_version"] == 1

    policy2 = generate_policy(db_path=db_path, output_path=output_path)
    assert policy2["policy_version"] == 2

    os.unlink(output_path)


def test_generate_policy_history(db_path):
    """Test that policy history is recorded."""
    from observatory.telemetry.schema import init_db

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        output_path = f.name

    generate_policy(db_path=db_path, output_path=output_path)

    conn = init_db(db_path)
    cursor = conn.execute("SELECT * FROM policy_history")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["policy_version"] == 1
    assert rows[0]["chain_count"] > 0
    conn.close()

    os.unlink(output_path)
