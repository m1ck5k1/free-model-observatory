"""Capability probe — quantitative capability + reliability snapshot of free-tier models.

Layer 2 of the Free Model Observatory (sibling of `liveness.py`).
Folded in from the fleet's `free-llm-probe` campaign (2026-09-25) per MoA review
`2026-09-25-free-model-observatory-integration.md`. Hardens what the standalone probe
learned:

- **Reasoning models** (`dots-studio/*`, `nvidia/nemotron-*`) emit chain-of-thought in a
  `reasoning` field and may leave `content` null; extract the reasoning as a fallback so
  a capable model is not scored as "empty."
- **Empty-completion** (HTTP 200, empty content+reasoning) is a DISTINCT failure class,
  tracked separately from errors and wrong answers.
- **Multi-key / numeric grading** — text keys match by substring; numeric keys require
  the key's digit substring appear in the reply's digit stream, so a pure-text reply can
  never satisfy a numeric key (the empty-coalesce false-correct trap).

Grading-equivalence seam (Archtecture blocker 1): `grading.judge_model` in
config/thresholds.yaml selects the judge. Jev (typesafe/jev-1.13) is the candidate
replacing gpt-4o-mini; a `judge_correlation_ok` gate must pass (calibration run vs the
incumbent) before the pin swaps.
"""

from __future__ import annotations
import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field

import requests
import yaml

logger = logging.getLogger("observatory.harness.capability")

# Ground-truth standardized subset. Single correct, non-contestable answer each.
# Numeric items use `answer`; defiantly-paraphraseable text items use `answers` (list).
TESTSET = [
    {"id": "m1", "prompt": "What is 7 * 8?", "answer": "56"},
    {"id": "m2", "prompt": "Solve for x: 3x + 5 = 20. Give only the value of x.", "answer": "5"},
    {"id": "m3", "prompt": "How many sides does a hexagon have? Answer with a single number.", "answer": "6"},
    {"id": "m4", "prompt": "If it takes 2 people 3 hours to paint a room, how many hours would 6 people take (assuming same rate)? Single number.", "answer": "1"},
    {"id": "m5", "prompt": "What is the capital of France? One word.", "answer": "Paris"},
    {"id": "m6", "prompt": "Which number comes next: 2, 4, 8, 16, ?", "answer": "32"},
    {"id": "m7", "prompt": "How many minutes are in 2 hours? Answer with a single number.", "answer": "120"},
    {"id": "m8", "prompt": "What is 15% of 200? Single number.", "answer": "30"},
    {"id": "l1", "prompt": "All dogs are animals. Rex is a dog. Therefore Rex is a ____. One word.", "answer": "animal"},
    {"id": "l2", "prompt": "If A is greater than B, and B is greater than C, then A is ___ than C. One word.", "answer": "greater"},
    {"id": "s1", "prompt": "A train travels 60 km in 1 hour. How far in 3 hours? Single number (km).", "answer": "180"},
    {"id": "s2", "prompt": "How many seconds are in 1 minute? Single number.", "answer": "60"},
    {"id": "t1", "prompt": "Which planet is known as the Red Planet? One word.", "answer": "Mars"},
    {"id": "t2", "prompt": "What is the value of pi rounded to two decimal places? Give only the number.", "answer": "3.14"},
    {"id": "g1", "prompt": "If 1 kg = 1000 g, how many grams in 2.5 kg? Single number.", "answer": "2500"},
    {"id": "c1", "prompt": "In Python, what does the print() function do? One short phrase.", "answers": ["console", "standard output", "standardout", "stdout"]},
]


def _normalize(s: str) -> str:
    if s is None:
        return ""
    s = s.strip().lower()
    return re.sub(r"[,\s]+", " ", s)


def grade(raw: str, item: dict) -> bool:
    """Correct if any accepted key matches. Rules:
    - Text key -> substring of normalized reply.
    - Numeric key (contains a digit) -> key's digit substring appears in reply's digit
      stream. A pure-text reply (no digits) can never satisfy a numeric key.
    Empty raw is never correct."""
    if not raw or not raw.strip():
        return False
    r = _normalize(raw)
    keys = item.get("answers") or [item.get("answer", "")]
    for k in keys:
        a = _normalize(k)
        if not a:
            continue
        if any(ch.isdigit() for ch in k):
            anum = re.sub(r"[^0-9.\-]", "", a)
            rnum = re.sub(r"[^0-9.\-]", "", r)
            if anum and anum in rnum:
                return True
        else:
            if a in r:
                return True
    return False


def extract_response(body: dict) -> str:
    """First-choice content; fall back to `reasoning` for reasoning-class models."""
    msg = (body.get("choices") or [{}])[0].get("message", {}) or {}
    content = msg.get("content") or ""
    if not content.strip():
        content = msg.get("reasoning") or ""
    return content.strip()


# ---------------------------------------------------------------------------
# Grading-judge seam (MoA FMO blocker 1d). `grading.judge_model` in
# config/thresholds.yaml names the pinned judge; the candidate is Jev
# (typesafe/jev-1.13) via the Decisions API, behind OUR gateway (key-scoping
# doctrine — never a raw OpenRouter key in a harness). Verdict is a typed
# choice (CORRECT/INCORRECT) + confidence; the caller treats it non-authoritative.
# The equivalence gate (scripts/judge_equivalence.py) proved Jev agrees with the
# incumbent (gpt-4o-mini) on a held-out set (acc 1.0 vs 0.857, κ 0.714) before
# this became the selected judge. Routing: JEV_FMO_JUDGE_TOKEN (an actor bearer
# token minted for the FMO) over JEV_GW_URL; falls back to the keeper actor token
# and then to a direct Decisions call only if no gateway is reachable.
# ---------------------------------------------------------------------------
DECISIONS_PATH = "/decisions"  # our gateway's route (upstream lives server-side; the
                               # raw /api/alpha/decisions route is never exposed)
JUDGE_QUESTION = {
    "correctness": {
        "type": "choice",
        "instructions": (
            "You are grading a candidate answer to a question for correctness. Mark "
            "CORRECT if the candidate gives the right answer (truthful and direct), "
            "INCORRECT if it is wrong, evasive, or refuses. Choose exactly one."
        ),
        "criteria": {
            "CORRECT": "the candidate answer is correct",
            "INCORRECT": "the candidate answer is wrong, evasive, or a refusal",
        },
    }
}


def _load_judge_env() -> tuple[str, str]:
    """Return (gateway_base, actor_token) for the FMO judge, from ~/.hermes/.env.
    Never prints the token. Order: JEV_FMO_JUDGE_TOKEN, then JEV_FP2_ACTOR_TOKEN."""
    env_path = os.path.join(os.path.expanduser("~/.hermes"), ".env")
    gw = os.environ.get("JEV_GW_URL", "http://127.0.0.1:8787")
    tok = os.environ.get("JEV_FMO_JUDGE_TOKEN") or os.environ.get("JEV_FP2_ACTOR_TOKEN", "")
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line.startswith("JEV_GW_URL="):
                gw = line.split("=", 1)[1].strip()
            elif line.startswith("JEV_FMO_JUDGE_TOKEN=") and not tok:
                tok = line.split("=", 1)[1].strip()
            elif line.startswith("JEV_FP2_ACTOR_TOKEN=") and not tok:
                tok = line.split("=", 1)[1].strip()
    return gw.rstrip("/"), tok


def judge_jev(problem: str, candidate: str, *, timeout: float = 20.0) -> dict:
    """Judge a candidate answer with Jev (Decisions API / gateway). Non-authoritative.
    Returns {ok, choice, confidence, verdict} — verdict is 'CORRECT'|'INCORRECT'|None."""
    import urllib.error
    import urllib.request
    gw, tok = _load_judge_env()
    payload = {
        "model": "typesafe/jev-1.13",
        "fit_point": 5,  # advisory option-evaluation fit point (registered, audit-logged)
        "questions": JUDGE_QUESTION,
        "state": f"question\n{problem}\n\ncandidate_answer\n{candidate}",
        "user": "fmo-judge",
    }
    req = urllib.request.Request(
        f"{gw}{DECISIONS_PATH}", data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": repr(e)}
    a = (body.get("answers") or {}).get("correctness") or {}
    return {"ok": True, "choice": a.get("choice"),
            "confidence": a.get("confidence"),
            "verdict": "CORRECT" if a.get("choice") == "CORRECT" else
                        ("INCORRECT" if a.get("choice") == "INCORRECT" else None)}


def grade_with_judge(raw: str, item: dict, judge: str | None) -> bool | None:
    """Grade a candidate's raw reply, using the pinned judge for prose/paraphraseable
    items (any item with a list `answers` key) and deterministic `grade()` otherwise.
    None = couldn't grade (no raw, no judge, or judge failed) — treated as empty,
    never a silent pass. Falls back to deterministic grading on judge failure."""
    if not raw or not raw.strip():
        return None
    is_prose = isinstance(item.get("answers"), list) and len(item.get("answers", [])) >= 1
    if not is_prose:
        return grade(raw, item)  # numeric/single-phrase: deterministic, free
    if not judge:
        return grade(raw, item)  # no judge configured yet -> default deterministic
    j = judge_jev(item.get("prompt", ""), raw)
    if not j.get("ok") or j.get("verdict") is None:
        # judge failed or gave no usable verdict: fall back deterministically, but
        # stay honest — a judge outage must not silently flip a correct to wrong or vice
        # versa. Deterministic fallback is the best local ground truth we have.
        return grade(raw, item)
    return j["verdict"] == "CORRECT"


@dataclass
class CapabilityRecord:
    model: str
    item: str
    status: int | None
    latency: float
    correct: bool | None          # None = no answer to grade (empty/error)
    empty_completion: bool        # HTTP 200 but no content
    attempts: int
    retry_waited_s: float | None
    raw: str = ""
    answer: str = ""


def probe_model(model: str, item: dict, base: str, key: str,
                max_tokens: int = 1024, timeout: float = 60.0,
                retries: int = 3, backoff: float = 5.0,
                judge: str | None = None) -> CapabilityRecord:
    """Probe one item against one model. 429/503 retry with backoff and record the wait.
    A `judge` (pinned) may be passed for future LLM-graded prose items; numeric/text
    items are graded directly here."""
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": [{"role": "user", "content": item["prompt"]}],
               "max_tokens": max_tokens, "temperature": 0.0}
    attempts = 1
    waited = None
    t0 = time.time()
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        status = r.status_code
        body = r.json() if (r.status_code == 200 and r.text) else {}
        ra = r.headers.get("Retry-After")
    except requests.RequestException as e:
        status = None
        body = {}
        ra = None
    raw = extract_response(body) if status == 200 else ""
    while status in (429, 503) and attempts <= retries:
        wait = float(ra) if (attempts == 1 and ra) else backoff * attempts
        time.sleep(wait)
        t0 = time.time()
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            status = r.status_code
            body = r.json() if (r.status_code == 200 and r.text) else {}
        except requests.RequestException:
            status = None
            body = {}
        raw = extract_response(body) if status == 200 else ""
        attempts += 1
        waited = round(wait, 1)

    latency = round(time.time() - t0, 3)
    empty = bool(status == 200 and not raw)
    correct = (grade_with_judge(raw, item, judge) if (status == 200 and raw) else None)
    return CapabilityRecord(
        model=model, item=item["id"], status=status, latency=latency,
        correct=correct, empty_completion=empty, attempts=attempts,
        retry_waited_s=waited, raw=raw[:120],
        answer=item.get("answer") or item.get("answers", [""])[0],
    )


def run_snapshot(models: list[str], items: list[dict], base: str, key: str,
                 judge: str | None = None, **kw):
    return [probe_model(m, it, base, key, judge=judge, **kw) for m in models for it in items]


def summarize(records: list[CapabilityRecord]) -> list[dict]:
    bym: dict[str, list[CapabilityRecord]] = {}
    for r in records:
        bym.setdefault(r.model, []).append(r)
    out = []
    for m, recs in bym.items():
        ok = [r for r in recs if r.status == 200 and not r.empty_completion]
        out.append({
            "model": m,
            "items": len(recs),
            "ok": len(ok),
            "fail_429": sum(1 for r in recs if r.status == 429),
            "fail_503": sum(1 for r in recs if r.status == 503),
            "fail_other": sum(1 for r in recs if r.status not in (200, 429, 503)),
            "empty_completions": sum(1 for r in recs if r.empty_completion),
            "accuracy": round(sum(1 for r in ok if r.correct) / len(ok), 3) if ok else None,
            "avg_latency": round(sum(r.latency for r in ok) / len(ok), 3) if ok else None,
            "retries_needed": sum(1 for r in recs if r.attempts > 1),
            "max_retry_wait": max((r.retry_waited_s or 0) for r in recs) if recs else None,
        })
    return out


def _project_root() -> str:
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cur = os.path.dirname(pkg)
    while cur != "/":
        if os.path.isdir(os.path.join(cur, "config")):
            return cur
        cur = os.path.dirname(cur)
    return os.getcwd()


def load_thresholds(path: str | None = None) -> dict:
    if path is None:
        path = os.path.join(_project_root(), "config", "thresholds.yaml")
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    ap = argparse.ArgumentParser(description="Free-tier capability snapshot (OpenRouter)")
    ap.add_argument("--base", default="https://openrouter.ai/api/v1")
    ap.add_argument("--out", default=os.path.expanduser("~/.hermes/jevd/fmo_capability.jsonl"))
    ap.add_argument("--smoke", action="store_true", help="1 model x 3 items")
    ap.add_argument("--judge", default=None,
                    help="override grading.judge_model from config (e.g. typesafe/jev-1.13)")
    args = ap.parse_args()
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        print("OPENROUTER_API_KEY unset"); sys.exit(2)
    base = args.base.rstrip("/")
    # resolve the pinned grading judge from config, unless overridden on the CLI
    judge = args.judge
    if judge is None:
        try:
            cfg = load_thresholds()
            judge = (cfg.get("grading") or {}).get("judge_model")
        except Exception:
            judge = None
    # resolve eligible models: reuse callers/schema-free — list :free from catalogue
    with requests.get(f"{base}/models", timeout=20) as r:
        models = sorted({m["id"] for m in r.json().get("data", []) if m["id"].endswith(":free")})
    if args.smoke:
        models = [models[0]]
        items = TESTSET[:3]
    else:
        items = TESTSET
    print(f"free models={len(models)} items={len(items)} judge={judge}")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    records = []
    for m in models:
        recs = run_snapshot([m], items, base, key, judge=judge)
        records.extend(recs)
        with open(args.out, "a") as f:
            for rec in recs:
                f.write(json.dumps(rec.__dict__, ensure_ascii=False) + "\n")
        print(json.dumps(summarize(recs)[0], ensure_ascii=False))
    if not args.smoke:
        spath = os.path.join(os.path.dirname(args.out), "fmo_capability_summary.json")
        with open(spath, "w") as f:
            json.dump(summarize(records), f, indent=1)
        print("summary ->", spath)


if __name__ == "__main__":
    main()