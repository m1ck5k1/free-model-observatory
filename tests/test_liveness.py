"""Tests for the liveness pinger."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from observatory.harness.liveness import (
    _format_line_protocol,
    _influxdb_config,
    get_eligible_models,
    load_config,
    load_thresholds,
    probe_model,
    write_to_influxdb,
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


# ── InfluxDB write tests ──────────────────────────────────────────────────

def test_format_line_protocol_success():
    """Test line protocol formatting for a successful probe."""
    result = {
        "model_id": "gpt-4o-mini",
        "provider": "openrouter",
        "ttft_ms": 342,
        "total_latency_ms": 342,
        "success": True,
        "status_code": 200,
        "error": None,
    }
    line = _format_line_protocol(result)
    assert line.startswith("fmo_liveness")
    assert "provider=openrouter" in line
    assert "model_id=gpt-4o-mini" in line
    assert "ttft_ms=342i" in line
    assert "total_latency_ms=342i" in line
    assert "success=1i" in line
    assert "status_code=200i" in line
    assert "error=" not in line  # no error field when error is None
    # Verify it ends with a nanosecond timestamp
    parts = line.split(" ")
    assert len(parts) >= 3
    timestamp = parts[-1]
    assert timestamp.isdigit()
    assert len(timestamp) == 19  # nanosecond precision


def test_format_line_protocol_failure():
    """Test line protocol formatting for a failed probe."""
    result = {
        "model_id": "gpt-4o-mini",
        "provider": "openrouter",
        "ttft_ms": None,
        "total_latency_ms": None,
        "success": False,
        "status_code": 429,
        "error": "rate_limited",
    }
    line = _format_line_protocol(result)
    assert line.startswith("fmo_liveness")
    assert "provider=openrouter" in line
    assert "success=0i" in line
    assert "error=" in line
    assert "rate_limited" in line
    # None fields should be omitted
    assert "ttft_ms=" not in line
    assert "total_latency_ms=" not in line


def test_format_line_protocol_tag_escape():
    """Test that tag values with special characters are escaped."""
    result = {
        "model_id": "model, with=equals",
        "provider": "test provider",
        "ttft_ms": 100,
        "total_latency_ms": 100,
        "success": True,
        "status_code": 200,
        "error": None,
    }
    line = _format_line_protocol(result)
    assert "provider=test\\ provider" in line
    assert "model_id=model\\,\\ with\\=equals" in line


def test_influxdb_config_defaults():
    """Test InfluxDB config resolution with defaults."""
    cfg = _influxdb_config()
    assert "host" in cfg
    assert "token" in cfg
    assert "org" in cfg
    assert "bucket" in cfg
    assert cfg["bucket"] == "free_model_observatory"


def test_influxdb_config_env_overrides(monkeypatch):
    """Test that env vars override config values."""
    monkeypatch.setenv("INFLUXDB_HOST", "http://custom:8086")
    monkeypatch.setenv("INFLUXDB_TOKEN", "supersecret")
    monkeypatch.setenv("INFLUXDB_ORG", "custom-org")
    monkeypatch.setenv("INFLUXDB_DRY_RUN", "true")

    cfg = _influxdb_config()
    assert cfg["host"] == "http://custom:8086"
    assert cfg["token"] == "supersecret"
    assert cfg["org"] == "custom-org"
    assert cfg["dry_run"] is True


@patch("observatory.harness.liveness.requests.post")
def test_write_to_influxdb_dry_run(mock_post):
    """Test that dry-run mode does not write to InfluxDB."""
    result = {"model_id": "test", "provider": "test", "ttft_ms": 100,
              "total_latency_ms": 100, "success": True, "status_code": 200}
    cfg = {"dry_run": True}
    success = write_to_influxdb(result, cfg)
    assert success is True
    mock_post.assert_not_called()


def test_write_to_influxdb_no_token():
    """Test that missing token is handled gracefully."""
    result = {"model_id": "test", "provider": "test", "ttft_ms": 100,
              "total_latency_ms": 100, "success": True, "status_code": 200}
    cfg = {"dry_run": False, "token": "", "host": "http://localhost:8086",
           "org": "test", "bucket": "test"}
    success = write_to_influxdb(result, cfg)
    assert success is False


@patch("observatory.harness.liveness.requests.post")
def test_write_to_influxdb_success(mock_post):
    """Test a successful InfluxDB write."""
    mock_response = MagicMock()
    mock_response.status_code = 204
    mock_post.return_value = mock_response

    result = {"model_id": "test", "provider": "test", "ttft_ms": 100,
              "total_latency_ms": 100, "success": True, "status_code": 200}
    cfg = {"dry_run": False, "token": "valid-token", "host": "http://localhost:8086",
           "org": "test", "bucket": "test"}
    success = write_to_influxdb(result, cfg)
    assert success is True
    mock_post.assert_called_once()


@patch("observatory.harness.liveness.requests.post")
def test_write_to_influxdb_http_error(mock_post):
    """Test that an InfluxDB HTTP error is handled gracefully."""
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "unauthorized"
    mock_post.return_value = mock_response

    result = {"model_id": "test", "provider": "test", "ttft_ms": 100,
              "total_latency_ms": 100, "success": True, "status_code": 200}
    cfg = {"dry_run": False, "token": "bad-token", "host": "http://localhost:8086",
           "org": "test", "bucket": "test"}
    success = write_to_influxdb(result, cfg)
    assert success is False
