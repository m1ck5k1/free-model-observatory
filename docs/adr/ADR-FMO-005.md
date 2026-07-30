# ADR-FMO-005: Shadow-Scoring Mirror Strategy

**Status:** Accepted (2026-07-30)
**Owner:** Michael Brewer
**Scope:** Probe harness, shadow scoring

## Context

Shadow scoring mirrors real hermes production traffic to candidate models and grades the candidate output. Two strategies:

1. **Synchronous duplicate** — for each hermes request, send it to the production model AND a shadow model in parallel. Compare both outputs.
2. **Async replay** — log production requests (anonymised) to a buffer, replay them against candidate models asynchronously.

## Decision

**Synchronous duplicate for the primary path, async replay as the fallback.**

Rationale:
- Synchronous gives us **immediate** comparison with zero replay delay — the production output and shadow output come from the same input at the same time.
- The latency budget for shadow is zero-observable: the shadow call runs in parallel, the hermes response waits only for the primary model. The shadow result is written asynchronously after the primary response is returned.
- Async replay is the fallback for models where synchronous mirroring would burn quota too fast (high-frequency models) or where the model is not safe for unvetted payloads.

**Important constraint:** synchronous shadowing only applies to models marked `no_train` (see §4.1 of the spec). Async replay with the sensitive-term filter applied is the only path for models without `no_train` guarantees.

## Consequences

- Hermes needs a small plugin/adapter layer to emit duplicate requests for eligible job classes.
- The shadow scorer is a lightweight HTTP service receiving `(job_class, input, production_output, candidate_output)` tuples.
- Async replay needs a replay buffer (SQLite-backed) and a scheduler (n8n or systemd timer).
- Sensitive-term filtering happens at the mirror decision point, not at scoring time.

## Revisit when

- Shadow scoring proves too costly in quota for any provider.
- Synchronous mirroring adds observable latency to hermes responses (>50ms P95 overhead).
