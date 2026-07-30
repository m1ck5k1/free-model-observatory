"""Tests for the registry harvester module."""

import hashlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from observatory.harvester.run import (
    CATALOGUE_PARSERS,
    check_new_model,
    check_tos_change,
    compute_eligibility,
    compute_tos_hash,
    load_config,
    normalise_tos_html,
    parse_google,
    parse_local,
    parse_openai_compatible,
    register_parser,
)


@pytest.fixture
def db_path():
    """Create a temporary database path for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    os.unlink(path)


def test_load_config():
    """Test that provider config loads correctly."""
    config = load_config()
    assert "providers" in config
    assert "openrouter" in config["providers"]
    assert "ollama" in config["providers"]
    assert config["providers"]["ollama"]["is_floor"] is True


def test_register_parser():
    """Test the parser registration decorator."""
    # Save original
    orig = dict(CATALOGUE_PARSERS)

    @register_parser("test_type")
    def test_parser(raw, config):
        return [{"id": "test"}]

    assert "test_type" in CATALOGUE_PARSERS
    assert CATALOGUE_PARSERS["test_type"]("data", {}) == [{"id": "test"}]

    # Restore
    CATALOGUE_PARSERS.clear()
    CATALOGUE_PARSERS.update(orig)


def test_parse_openai_compatible_with_filter():
    """Test parsing OpenAI-compatible catalogue with :free filter."""
    config = {"filter": ":free", "display_name": "OpenRouter"}
    raw = {
        "data": [
            {"id": "model-a:free", "context_length": 4096},
            {"id": "model-b:paid", "context_length": 8192},
            {"id": "model-c:free", "context_length": 16384},
        ]
    }
    models = parse_openai_compatible(raw, config)
    assert len(models) == 2
    assert models[0]["id"] == "model-a:free"
    assert models[1]["id"] == "model-c:free"


def test_parse_openai_compatible_with_known_free():
    """Test parsing with known-free identifiers list."""
    config = {
        "display_name": "Groq",
        "free_model_identifiers": ["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
    }
    raw = {
        "data": [
            {"id": "llama-3.3-70b-versatile", "context_length": 32768},
            {"id": "llama-3.1-8b-instant", "context_length": 8192},
            {"id": "mixtral-8x7b-32768", "context_length": 32768},
        ]
    }
    models = parse_openai_compatible(raw, config)
    assert len(models) == 2
    assert models[0]["id"] == "llama-3.3-70b-versatile"


def test_parse_openai_compatible_empty():
    """Test parsing empty catalogue."""
    models = parse_openai_compatible({}, {"display_name": "Test"})
    assert models == []


def test_parse_google():
    """Test parsing Google AI Studio catalogue."""
    config = {
        "display_name": "Google",
        "free_model_identifiers": ["gemini-2.0-flash", "gemini-1.5-flash"],
    }
    raw = {
        "models": [
            {"name": "models/gemini-2.0-flash", "inputTokenLimit": 8192},
            {"name": "models/gemini-1.5-pro", "inputTokenLimit": 32768},
            {"name": "models/gemini-1.5-flash", "inputTokenLimit": 8192},
        ]
    }
    models = parse_google(raw, config)
    assert len(models) == 2
    assert models[0]["id"] == "gemini-2.0-flash"
    assert models[1]["id"] == "gemini-1.5-flash"


def test_parse_local():
    """Test parsing local floor model."""
    models = parse_local([{"id": "local-floor", "provider": "ollama"}], {})
    assert len(models) == 1
    assert models[0]["id"] == "local-floor"


def test_compute_eligibility():
    """Test eligibility computation."""
    # no_train is eligible
    eligible, reason = compute_eligibility({"retention_policy": "no_train"})
    assert eligible is True
    assert reason is None

    # trains_on_input is ineligible
    eligible, reason = compute_eligibility({"retention_policy": "trains_on_input"})
    assert eligible is False
    assert reason == "trains_on_input"

    # unknown is ineligible
    eligible, reason = compute_eligibility({"retention_policy": "unknown"})
    assert eligible is False
    assert reason == "retention_policy_unknown"


def test_check_new_model(db_path):
    """Test new model detection."""
    from observatory.telemetry.schema import init_db

    conn = init_db(db_path)

    # First check should detect new model
    is_new = check_new_model(conn, "new-model", "test-provider")
    assert is_new is True

    # Second check should not be new
    is_new = check_new_model(conn, "new-model", "test-provider")
    assert is_new is False

    conn.close()


def test_check_tos_change(db_path):
    """Test ToS change detection."""
    from observatory.telemetry.schema import init_db

    conn = init_db(db_path)

    # Insert a model with a known ToS hash
    conn.execute(
        """INSERT INTO models (model_id, provider, first_seen, last_seen,
           tos_url, tos_hash, retention_policy, eligible, source_confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("test", "test-provider", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
         "https://example.com/tos", "old_hash", "no_train", 1, "high"),
    )
    conn.commit()

    # Same hash — no change
    changed = check_tos_change(conn, "test-provider", "https://example.com/tos", "old_hash")
    assert changed is False

    # Different hash — change detected
    changed = check_tos_change(conn, "test-provider", "https://example.com/tos", "new_hash")
    assert changed is True

    # Verify the change was recorded
    cursor = conn.execute("SELECT * FROM tos_changes")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["old_hash"] == "old_hash"
    assert rows[0]["new_hash"] == "new_hash"

    conn.close()


@patch("observatory.harvester.run.requests.get")
def test_compute_tos_hash_success(mock_get):
    """Test ToS hashing with a successful fetch, including HTML normalisation."""
    mock_response = MagicMock()
    mock_response.text = (
        "<html><body><script>var x=1;</script>"
        "This is the <b>terms of service</b> page.</body></html>"
    )
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    hash_val = compute_tos_hash("https://example.com/tos")
    assert hash_val is not None
    assert len(hash_val) == 64  # SHA-256 hex
    # The normalised text should be: "this is the terms of service page."
    expected = hashlib.sha256(b"this is the terms of service page.").hexdigest()
    assert hash_val == expected


@patch("observatory.harvester.run.requests.get")
def test_compute_tos_hash_timeout(mock_get):
    """Test ToS hashing with a timeout."""
    mock_get.side_effect = Exception("Timeout")

    hash_val = compute_tos_hash("https://example.com/tos")
    assert hash_val is None


@patch("observatory.harvester.run.requests.get")
def test_compute_tos_hash_http_error(mock_get):
    """Test ToS hashing with an HTTP error."""
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = Exception("HTTP 404")
    mock_get.return_value = mock_response

    hash_val = compute_tos_hash("https://example.com/tos")
    assert hash_val is None


def test_fetch_catalogue_local():
    """Test fetching catalogue for local provider."""
    from observatory.harvester.run import fetch_catalogue

    config = {"catalogue_type": "local"}
    result = fetch_catalogue(config)
    assert len(result) == 1
    assert result[0]["id"] == "local-floor"


# ── HTML normalisation tests ──────────────────────────────────────────────

def test_normalise_tos_html_strips_scripts():
    """Test that <script> tags and their content are stripped."""
    html = "<html><script>var csrf='abc123';</script><body>Terms</body></html>"
    result = normalise_tos_html(html)
    assert "csrf" not in result
    assert "terms" in result


def test_normalise_tos_html_strips_styles():
    """Test that <style> tags and their content are stripped."""
    html = "<html><style>.hidden{display:none}</style><body>Privacy Policy</body></html>"
    result = normalise_tos_html(html)
    assert "hidden" not in result
    assert "privacy policy" in result


def test_normalise_tos_html_strips_svg():
    """Test that <svg> tags are stripped."""
    html = "<body><svg><text>icon</text></svg>Terms of Service</body>"
    result = normalise_tos_html(html)
    assert "icon" not in result
    assert "terms of service" in result


def test_normalise_tos_html_collapses_whitespace():
    """Test that whitespace is collapsed."""
    html = "<body>Line 1\n\n  Line 2\n\n\nLine 3</body>"
    result = normalise_tos_html(html)
    assert "line 1 line 2 line 3" in result


def test_normalise_tos_html_lowercases():
    """Test that text is lowercased."""
    html = "<body>TERMS OF SERVICE</body>"
    result = normalise_tos_html(html)
    assert result == "terms of service"


def test_normalise_tos_html_removes_entities():
    """Test that HTML entities are decoded."""
    html = "<body>Apple &amp; Orange &lt; Banana</body>"
    result = normalise_tos_html(html)
    assert "apple & orange < banana" in result


def test_normalise_tos_html_no_script_no_style():
    """Test that plain text without scripts is preserved."""
    html = "<html><body>This is the privacy policy. We do not train on your data.</body></html>"
    result = normalise_tos_html(html)
    assert "privacy policy" in result
    assert "train on your data" in result
