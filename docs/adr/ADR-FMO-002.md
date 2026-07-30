# ADR-FMO-002: Primary Job-Class Slice

**Status:** Accepted (2026-07-30)
**Owner:** Michael Brewer
**Scope:** Probe harness v0.1

## Context

The probe harness needs an initial job class to build golden sets, graders, and scoring pipelines. Two candidates at the top of the stack:

- **`tool_call`** — hermes's heaviest current dependency. JSON schema validation + argument correctness. Affects every production hermes session today.
- **`voice_turn`** — Iris/Halo voice intent parsing. Latency-sensitive. Important but Halo has not yet landed.
- **`intent_classify`** — hermes front door, but has the least tolerance for drift (FreeMoE routing)

## Decision

**`tool_call` is the primary v0.1 slice, with `voice_turn` as immediate fast-follow (target Phase 1).**

Rationale:
1. Hermes depends on tool calling *today* for every structured action it takes.
2. Halo/Iris has not yet landed in production.
3. Tool-call success is the most measurable deterministic signal — JSON schema validation, argument equality, execution outcome.
4. The `tool_call` golden sets can draw from real hermes traffic immediately.

## Assumption

If `voice_turn` had been preferred, the Iris team would have built it. Defaulting to `tool_call` on the basis that hermes is the system we can instrument today.

## Consequences

- Golden set v1 for `tool_call` is Phase 0 work.
- `voice_turn` golden set is Phase 1 (target 2026-08-10).
- Grading for `tool_call` is fully deterministic — no LLM judge needed for this class.

## Revisit when

- Halo lands and `voice_turn` traffic is measurable.
- A new job class shows higher risk or more traffic.
