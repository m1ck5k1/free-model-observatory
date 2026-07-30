"""Tests for the liveness pinger."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from observatory.harness.liveness import (
    get_eligible_models,
    load_config,
    load_thresholds,
    probe_model,
)


@pytest.fixture
def db_path():
    """Create a temporary database path with test data."""
    from observatory.telemetry.schema import init_db

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name

    conn = init_db(path)

    # Insert an eligible model
    conn.execute(
        """INSERT INTO models (model_id, provider, first_seen, last_seen,
           retention_policy, eligible, source_confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("test-model", "test-provider", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
         "no_train", 1, "high"),
    )
    conn.commit()
    conn.close()

    yield path
    os.unlink(path)


def test_get_eligible_models(db_path):
    """Test fetching eligible models from the database."""
    conn = __import__("observatory.telemetry.schema", fromlist=["init_db"]).init_db(db_path)
    models = get_eligible_models(conn)
    assert len(models) == 1
    assert models[0]["model_id"] == "test-model"
    assert models[0]["provider"] == "test-provider"
    conn.close()


def test_get_eligible_models_empty(db_path):
    """Test fetching eligible models when none are eligible."""
    from observatory.telemetry.schema import init_db

    # Fresh db with no models
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        empty_path = f.name

    conn = init_db(empty_path)
    models = get_eligible_models(conn)
    assert models == []
    conn.close()
    os.unlink(empty_path)


def test_probe_model_no_api_base():
    """Test probing a model with no api_base configured."""
    result = probe_model("test", "test-provider", {})
    assert result["error"] == "no api_base"
    assert result["success"] is False


@patch("observatory.harness.liveness.requests.post")
def test_probe_model_success(mock_post):
    """Test a successful model probe."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    config = {
        "api_base": "https://api.example.com",
        "auth_type": "none",
    }

    result = probe_model("test-model", "test-provider", config)
    assert result["success"] is True
    assert result["ttft_ms"] is not None
    assert result["total_latency_ms"] is not None
    assert result["error"] is None


@patch("observatory.harness.liveness.requests.post")
def test_probe_model_rate_limited(mock_post):
    """Test a rate-limited model probe."""
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_post.return_value = mock_response

    config = {
        "api_base": "https://api.example.com",
        "auth_type": "none",
    }

    result = probe_model("test-model", "test-provider", config)
    assert result["success"] is False
    assert result["error"] == "rate_limited"


@patch("observatory.harness.liveness.requests.post")
def test_probe_model_timeout(mock_post):
    """Test a timed-out model probe."""
    mock_post.side_effect = Exception("timeout")

    config = {
        "api_base": "https://api.example.com",
        "auth_type": "none",
    }

    result = probe_model("test-model", "test-provider", config)
    assert result["success"] is False
    assert "timeout" in result["error"].lower() or result["error"] is not None


def test_load_config():
    """Test loading provider config."""
    config = load_config()
    assert "providers" in config
    assert "openrouter" in config["providers"]


def test_load_thresholds():
    """Test loading thresholds."""
    thresholds = load_thresholds()
    assert "liveness" in thresholds
    assert thresholds["liveness"]["interval_minutes"] == 15
