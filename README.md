# Free Model Observatory

Supply-chain monitor for free-tier LLM inference. Answers two questions:

1. **Routing:** which free model should hermes send *this job class* to, right now?
2. **Risk:** is the free-tier dependency degrading or about to break?

Not a leaderboard. Not a dashboard. A router policy generator with drift detection.

## Status

v0.1 — Foundations phase. See `docs/adr/` for architectural decisions, `BACKLOG.md` for deferred scope.

## Architecture

```
L4  Router Policy      → ranked fallback chain (consumed by hermes + Iris)
L3  Telemetry          → InfluxDB (time-series), SQLite (state)
L2  Probe Harness      → liveness, quota, capability + shadow scoring
L1  Registry Harvester → catalogue + ToS hashing
```

## Cadence

| Tier | Frequency | Action |
|---|---|---|
| Passive | continuous | shadow-score real traffic |
| Liveness | 15 min | one-token ping, capture TTFT |
| Daily | 06:00 UTC | quota probe + 5-item smoke suite |
| Weekly | Sun 03:00 UTC | full capability suite |
| Event | on trigger | targeted re-probe |

## Licence

Apache-2.0. See [LICENSE](./LICENSE).
