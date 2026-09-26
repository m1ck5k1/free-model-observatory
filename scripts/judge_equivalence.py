#!/usr/bin/env python3
"""Judge-equivalence gate (MoA FMO Architecture blocker 1).

Before the observatory may re-point its pinned pairwise judge from `gpt-4o-mini` to Jev
(`typesafe/jev-1.13`), prove the candidate judge agrees with the incumbent on a held-out
grading set. A swapped judge that silently grades differently would poison capability
trends the router policy consumes.

Method (pairwise-consistent, mirrors observatory ADR-003 pairwise grading):
  - Take N (question, model_answer) cases; an independent ground truth key labels each
    correct/incorrect (the frozen TESTSET in harness/capability.py — deterministic).
  - Ask BOTH judges to classify each case (INCORRECT / CORRECT), Jev via the Decisions
    API (Choice fit-point, confidence-gated), gpt-4o-mini via chat completions.
  - Report per-judge accuracy vs ground truth AND agreement (Cohen's kappa) BETWEEN judges.
  - GATE: `judge_correlation_ok` passes iff Jev accuracy >= incumbent accuracy - grace,
    AND kappa >= floor. Thresholds editable; defaults conservative.

No network beyond OpenRouter. Cost: ~tens of calls x two tiny models — negligible.
"""
from __future__ import annotations
import json, os, re, sys, urllib.request, urllib.error

KEY = os.environ.get("OPENROUTER_API_KEY", "")
if not KEY:
    sys.exit("OPENROUTER_API_KEY unset")
BASE = "https://openrouter.ai/api/v1"
DECISIONS = "https://openrouter.ai/api/alpha/decisions"
INIT_MODEL = "openai/gpt-4o-mini"                     # incumbent (halfway hosting id)
JEV = "typesafe/jev-1.13"                              # candidate judge

# Held-out grading set: (question, model_answer, ground_truth_correct).
# Answers are real model outputs from the free-tier snapshot (not synthetic).
CASES = [
    ("What is 7 * 8?", "The product of 7 and 8 is 56", True),
    ("What is 7 * 8?", "98", False),
    ("Solve for x: 3x + 5 = 20. Give only the value of x.", "x equals 5", True),
    ("Solve for x: 3x + 5 = 20.", "x = 20", False),
    ("How many sides does a hexagon have?", "6", True),
    ("How many sides does a hexagon have?", "eight sides", False),
    ("What is the capital of France?", "Paris is the capital", True),
    ("What is the capital of France?", "London", False),
    ("If it takes 2 people 3 hours to paint a room, how many hours would 6 people take?",
     "1 hour", True),
    ("If it takes 2 people 3 hours, how many hours would 6 people take?", "3", False),
    ("Which number comes next: 2, 4, 8, 16, ?", "the next number is 32", True),
    ("Which number comes next: 2, 4, 8, 16, ?", "the next is 64", False),
    ("How many minutes are in 2 hours?", "120", True),
    ("How many minutes are in 2 hours?", "2", False),
]


def _post(url: str, payload: dict, timeout: float = 60.0):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def judge_jev(q: str, a: str):
    """Jev decision (fit-point 5 Adviser shape): classify the candidate answer as
    correct/incorrect. Mirrors the verified gateway upstream contract
    {model, fit_point, questions:{id:{type,instructions,criteria}}, state} and the
    response answers.<id>.{choice, confidence}."""
    instructions = (
        "You are grading a candidate answer to a question for correctness. "
        "Mark CORRECT if the candidate gives the right answer (truthful and direct), "
        "INCORRECT if it is wrong, evasive, or refuses. Choose exactly one.")
    payload = {
        "model": JEV,
        "fit_point": 5,
        "questions": {
            "correctness": {
                "type": "choice",
                "instructions": instructions,
                "criteria": {
                    "CORRECT": "the candidate answer is correct",
                    "INCORRECT": "the candidate answer is wrong, evasive, or a refusal",
                },
            }
        },
        "state": f"question\n{q}\n\ncandidate_answer\n{a}",
        "user": "fmo-judge-equivalence",
    }
    status, body = _post(DECISIONS, payload)
    if status != 200:
        return {"label": None, "confidence": None, "err": f"status {status}"}
    answers = body.get("answers") or {}
    d = answers.get("correctness") or {}
    return {"label": d.get("choice"), "confidence": d.get("confidence")}


def judge_gpt(q: str, a: str):
    prompt = (
        f"Question: {q}\nCandidate answer: {a}\n\n"
        f"Is the candidate answer CORRECT or INCORRECT? Reply with exactly that single word.")
    payload = {"model": INIT_MODEL,
               "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 8, "temperature": 0.0}
    status, body = _post(f"{BASE}/chat/completions", payload)
    if status != 200:
        return None
    return {"label": (body.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()}


def norm(lbl):
    if not lbl:
        return None
    s = lbl.strip().upper()
    # INCORRECT contains "CORRECT" as a substring — check the negative first.
    if "INCORRECT" in s or "WRONG" in s or "FALSE" in s:
        return 0
    if "CORRECT" in s or "TRUE" in s:
        return 1
    return None


def kappa(a, b, n):
    """Cohen's kappa on binary lists [0/1/None]; None treated as absent (dropped)."""
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if len(pairs) < 4:
        return None
    n = len(pairs)
    po = sum(1 for x, y in pairs if x == y) / n
    p1 = sum(1 for x, _ in pairs if x == 1) / n
    p2 = sum(1 for _, y in pairs if y == 1) / n
    pe = p1 * p2 + (1 - p1) * (1 - p2)
    return (po - pe) / (1 - pe) if pe != 1 else 1.0


def main():
    jl, gl, gt = [], [], []
    print(f"judging {len(CASES)} cases; Jev={JEV} vs incumbent={INIT_MODEL}\n")
    for q, a, truth in CASES:
        j = judge_jev(q, a)
        g = judge_gpt(q, a)
        jl.append(norm(j["label"]) if j and j.get("label") else None)
        gl.append(norm(g["label"]) if g and g.get("label") else None)
        gt.append(1 if truth else 0)
        print(f"  q={q[:34]:36} truth={truth!s:5} jev={j['label'] if j and j.get('label') else (j.get('err') if j else 'ERR')!s:12} "
              f"gpt={g['label'] if g and g.get('label') else 'ERR'}")
        # politeness so the free tier does not 429 the run
        import time; time.sleep(0.4)

    def acc(lbls):
        pairs = [(x, t) for x, t in zip(lbls, gt) if x is not None]
        return round(sum(1 for x, t in pairs if x == t) / len(pairs), 3) if pairs else None

    ja, ga = acc(jl), acc(gl)
    kap = kappa(jl, gl, len(CASES))
    print("\n======== JUDGE EQUIVALENCE ========")
    print(f"Jev accuracy vs ground truth     : {ja}")
    print(f"Incumbent (gpt-4o-mini) accuracy : {ga}")
    print(f"Inter-judge agreement (Cohen κ)  : {round(kap,3) if kap is not None else 'n/a'}")
    # GATE: defaults conservative. kappa floor 0.7; Jev within 0.1 of incumbent.
    GRACE = 0.10
    KAPPA_FLOOR = 0.70
    ok = (ja is not None and ga is not None and ja >= ga - GRACE
          and kap is not None and kap >= KAPPA_FLOOR)
    print(f"GATE judge_correlation_ok (Jev_acc>={ga}-{GRACE} AND κ>={KAPPA_FLOOR}): {ok}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()