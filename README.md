# 🔭 Free Model Observatory

**Tame the chaotic frontier of free-tier LLMs — before one of them quietly lies to you.**

The internet is full of zero-dollar models. They're fast, they're cheap (actually free),
and they're a trap: a `:free` endpoint can silently swap to a lighter model, get
rate-limited into uselessness at the exact moment you need it, or return a confident lie
at 2am. This observatory exists to watch that chaos so you don't have to.

It answers exactly two questions, continuously:

1. **Routing** — *which free model should I send this job to, right now?*
2. **Risk** — *is my free-tier dependency degrading… or about to break?*

> Not a leaderboard. Not a pretty dashboard with charts nobody reads.
> It's a **router policy generator with drift detection** — a quiet watchdog that decides
> and *keeps deciding*, and pings you when the ground shifts.

---

## Why this exists

Paid LLMs are boring in a good way — predictable, monitored, contract-bound. The free
tier is the wild west. Models appear overnight, get rotated without notice, or vanish
mid-project. If you lean on them for *anything* real, you're betting on a world that
changes under you every week.

The Observatory makes that bet visible, measurable, and safe:

- **It measures capability** with a standardized probe suite — so a model's reputation
  doesn't outrun its actual output.
- **It watches reliability** (429s, 503s, time-to-first-token) — so "free" never quietly
  becomes "unavailable when I needed it."
- **It tracks the supply chain** (retention policies, ToS, registry changes) — because on
  the free tier, the model you call today may not be the model you called yesterday.

## What you get (the honest version)

| Layer | What it does | Why you care |
|---|---|---|
| **L1 · Registry Harvester** | Catalogues models, hashes ToS/retention | Knows when the ground rule changes under your feet |
| **L2 · Probe Harness** | Liveness, quota, capability, shadow-scoring | *Measures* models, doesn't trust marketing |
| **L3 · Telemetry** | Time-series (InfluxDB) + state (SQLite) | The memory; trends and anomalies live here |
| **L4 · Router Policy** | Emits a ranked fallback chain | Ships the daily "send this job to X, else Y, else Z" |

The output isn't a report you read — it's a **decision** you consume. Hermes and Iris
read the router policy and know what to call, in what order, and when to give up and hit
the local floor model instead.

## Cadence — how the watch runs

| Tier | Frequency | Action |
|---|---|---|
| **Passive** | continuous | Shadow-score real traffic (never un-redacted) |
| **Liveness** | 15 min | One-token ping; captures time-to-first-token |
| **Daily** | 06:00 UTC | Quota probe + 5-item smoke suite |
| **Weekly** | Sun 03:00 UTC | Full capability suite |
| **Event** | on trigger | Targeted re-probe when something drifts |

## Status

**v0.1 — Foundations, now with a conscience.** The core layers and 5 governing ADRs are
in place, and the capability harness landed with a wall of regression tests after a
fleet-origin probe campaign hit — and fixed, painfully — the real-world traps of grading
free-tier models (reasoning-in-`reasoning`-field, the empty-completion false-correct,
the numeric-coalesce lie). Fail-closed redaction and a secret-leak merge gate are wired
in, because a tool that watches for silent failure has no excuse for silent failure
itself. The judge for capability scoring has been validated with a measured equivalence
run; a deliberate switch flips it when we say so.

See `docs/adr/` for the reasoning behind every real decision, and `BACKLOG.md` for what
we deliberately chose *not* to build (yet).

## Quick start

```bash
git clone https://github.com/m1ck5k1/free-model-observatory.git
cd free-model-observatory
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# API keys live in your environment, never in git
export OPENROUTER_API_KEY="sk-..."   # + Google / Groq / Mistral / Cerebras as needed

python -m observatory.harvester.run       # L1: catalogue + ToS
python -m observatory.harness.liveness    # L2: the 15-min ping, once
python -m observatory.harness.capability  # L2: full capability suite (OpenRouter :free)
pytest                                    # 60+ tests, incl. the failures that taught us
```

Full operating detail (including the important — and slightly embarrassing — story of
why the git history was rewritten, and what the leak-scan gate now prevents): see
[`docs/runbook.md`](./docs/runbook.md).

## Failures we've learned from (and build guards against)

Honesty is a feature here, not a footnote. This project's credibility comes from the
times it was **wrong and caught itself** — each one became a regression test and a
permanent guard. The short version:

- **We shipped a grader that false-corrected every text-only answer.** A numeric-coalesce
  shortcut turned `"User Safety: safe"` into a pass. A model that answered nothing scored
  "correct." Then a second bug — `"INCORRECT"` contains `"CORRECT"` — silently flipped our
  Jev-as-judge result to a flattering 0.5. Both are now pinned by regression tests that
  prove the tap cannot reopen.
- **We near-shipped a leak that bit us before.** The repo's git history was force-pushed
  to purge a client-identifiers file that had been published publicly (the story, in full,
  is in `docs/runbook.md`). A `pre-push` leak-scan gate now refuses to push any file
  carrying a credential shape or the roster — proven in CI to stop both a real secret and
  the roster itself.
- **We reported a "closed" safety gate that a stray `git checkout` had silently reverted.**
  A test passed before the revert, so the green tick was a lie. We caught it in a
  pre-push self-scan, re-applied it, and re-verified. The lesson baked in: *a passing test
  proves behavior, not that the code you pushed still has it* — pushes now re-run the gate.

**The rule:** this tool watches free-tier models for **their** silent failure. It would
be indefensible to hide **our own**. Every known failure lives in
[`docs/failures.md`](./docs/failures.md) — a running log of incidents, root causes, and
the **permanent mechanical guards** each one built (failing tests, blocking hooks,
fail-closed paths). New known failures are added there alongside their guard — a note
without a guard is just documentation, not protection. See how many ways we've already
been wrong, and that none of them can quietly reopen.

## The philosophy, in one line

**Free should never mean unmonitored.** If a model costs nothing but might quietly betray
you, the only rational move is to watch it every minute of every day — and to refuse,
loudly, to run un-redacted or to trust an un-pinned model. That's this tool.

---

## Licence

Apache-2.0. See [LICENSE](./LICENSE).