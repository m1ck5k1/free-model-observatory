# ADR-FMO-003: Pinned Judge Model for Pairwise Prose Grading

**Status:** Accepted (2026-07-30)
**Owner:** Michael Brewer
**Scope:** Probe harness, capability scoring

## Context

Prose job classes (`summarise`, certain `intent_classify` edge cases) require an LLM-as-judge for grading. The judge must be:

- **Fixed** — pinned by version, so longitudinal comparisons are valid.
- **Strong** — capable enough to evaluate output quality.
- **Available** — must be reachable when the harness runs.

## Decision

**Use `gpt-4o-mini` (via OpenRouter) as the pinned pairwise judge for v0.1.**

Pinned as `openrouter/openai/gpt-4o-mini`. This model is:
1. Available via OpenRouter (already in the hermes provider chain).
2. Cheap enough for batch judging (~$0.15/1M input tokens).
3. Well-calibrated for pairwise comparisons on summarisation.

The judge model is **not** part of the observed set — it is infrastructure, not subject to registry monitoring.

## Consequences

- Judge version is pinned in `config/thresholds.yaml` under `grading.judge_model`.
- If OpenRouter drops `gpt-4o-mini` from free tier, an alternative judge must be selected via ADR.
- The judge's own performance drift is a risk. Mitigated by: pairwise (not absolute) scoring, which is less sensitive to judge calibration drift.

## Revisit when

- `gpt-4o-mini` leaves OpenRouter free tier.
- Evidence shows the judge is biased toward a specific provider or model family.
- A free alternative matches its pairwise-accuracy performance.
