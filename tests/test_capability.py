"""Tests for the capability harness (grading + extraction + reliability telemetry)."""
import json
from unittest.mock import MagicMock, patch

import pytest

from observatory.harness.capability import (
    TESTSET,
    _normalize,
    extract_response,
    grade,
    probe_model,
    summarize,
)

# --- grading: the exact cases that bit the standalone probe ---


def test_grade_text_key_substring():
    assert grade("Paris", {"answer": "Paris"}) is True
    assert grade("The capital is Paris.", {"answer": "Paris"}) is True
    assert grade("MARS", {"answer": "Mars"}) is True  # case-insensitive


def test_grade_text_key_never_matches_unrelated_text():
    # THE TRAP: a pure-text reply must never false-correct via empty numeric coalesce
    assert grade("User Safety: safe", {"answer": "Paris"}) is False
    assert grade("User Safety: safe", {"answer": "Mars"}) is False


def test_grade_numeric_substring_and_rejects_pure_text():
    assert grade("7 * 8 = 56", {"answer": "56"}) is True      # shows working
    assert grade("56", {"answer": "56"}) is True
    assert grade("2,500 grams", {"answer": "2500"}) is True    # punctuation-coalesce
    assert grade("the value is 3.14", {"answer": "3.14"}) is True
    assert grade("User Safety: safe", {"answer": "56"}) is False  # no digits
    assert grade("50", {"answer": "56"}) is False                 # wrong number
    assert grade("6", {"answer": "1"}) is False                   # common-trap number


def test_grade_multi_key_phrase():
    item = {"answers": ["console", "standard output", "stdout"]}
    for raw in ("Outputs text to the console.", "Prints to standard output.",
                "Writes to stdout."):
        assert grade(raw, item) is True


def test_grade_empty_raw_never_correct():
    assert grade("", {"answer": "Paris"}) is False
    assert grade("   ", {"answer": "56"}) is False
    assert grade(None, {"answer": "56"}) is False


def test_teset_items_have_single_or_list_keys():
    for it in TESTSET:
        assert ("answer" in it) or ("answers" in it)
        score = grade("", it)  # must never be True on empty
        assert score is False


# --- extraction: reasoning-model fallback ---


def test_extract_content_and_reasoning_fallback():
    assert extract_response({"choices": [{"message": {"content": "56"}}]}) == "56"
    # reasoning-class model: content null, reasoning populated
    body = {"choices": [{"message": {"content": None, "reasoning": "The product is 56"}}]}
    assert extract_response(body) == "The product is 56"
    # completely empty
    assert extract_response({}) == ""


# --- probe: status / empty-completion / retry telemetry ---


def _fake_response(status, text, retry_after=None):
    m = MagicMock()
    m.status_code = status
    m.text = text
    m.headers = {"Retry-After": retry_after} if retry_after else {}
    m.json.return_value = json.loads(text) if text else {}
    return m


def test_probe_ok_and_grades():
    body = json.dumps({"choices": [{"message": {"content": "56"}}]})
    with patch("observatory.harness.capability.requests.post",
               return_value=_fake_response(200, body)) as p:
        rec = probe_model("m", {"id": "m1", "prompt": "?", "answer": "56"},
                          "http://x", "k")
    assert rec.status == 200
    assert rec.correct is True
    assert rec.empty_completion is False
    assert rec.attempts == 1


def test_probe_empty_completion_is_distinct():
    with patch("observatory.harness.capability.requests.post",
               return_value=_fake_response(200, "{\"choices\":[{\"message\":{\"content\":null}}]}")):
        rec = probe_model("m", {"id": "m1", "prompt": "?", "answer": "56"},
                          "http://x", "k")
    assert rec.status == 200
    assert rec.empty_completion is True
    assert rec.correct is None  # nothing to grade


def test_probe_429_retry_and_records_wait():
    call = {"n": 0}

    def fake(*a, **kw):
        call["n"] += 1
        if call["n"] == 1:
            return _fake_response(429, "", retry_after="1")
        return _fake_response(200, "{\"choices\":[{\"message\":{\"content\":\"56\"}}]}")

    with patch("observatory.harness.capability.requests.post", fake), \
         patch("observatory.harness.capability.time.sleep") as slp:
        rec = probe_model("m", {"id": "m1", "prompt": "?", "answer": "56"},
                          "http://x", "k", backoff=1.0)
    assert rec.attempts == 2
    assert rec.retry_waited_s == 1.0
    assert rec.status == 200
    slp.assert_called_once_with(1.0)


def test_probe_503_gives_up_after_retries():
    with patch("observatory.harness.capability.requests.post",
               return_value=_fake_response(503, "")), \
         patch("observatory.harness.capability.time.sleep"):
        rec = probe_model("m", {"id": "m1", "prompt": "?", "answer": "56"},
                          "http://x", "k", retries=2, backoff=1.0)
    assert rec.status == 503
    assert rec.attempts == 3


def test_summarize_counts():
    from observatory.harness.capability import CapabilityRecord
    recs = [
        CapabilityRecord("a", "m1", 200, 1.0, True, False, 1, None),
        CapabilityRecord("a", "m2", 429, 0.5, None, False, 4, 15.0),
        CapabilityRecord("a", "m3", 200, 0.8, None, True, 1, None),
        CapabilityRecord("a", "m4", 200, 0.9, False, False, 1, None),
    ]
    s = summarize(recs)[0]
    assert s["fail_429"] == 1
    assert s["empty_completions"] == 1
    assert s["accuracy"] == 0.5  # ok = m1(correct) + m4(incorrect) = 2; 1/2
    assert s["max_retry_wait"] == 15.0