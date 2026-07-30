# ADR-FMO-004: Telemetry Storage Split

**Status:** Accepted (2026-07-30)
**Owner:** Michael Brewer
**Scope:** Telemetry layer

## Context

The Observatory needs two kinds of storage:

1. **Time-series metrics** — TTFT, latency, availability, throughput, quota hits. Thousands of data points per day. Query pattern: recent-window aggregations, alert thresholds, Grafana dashboards.
2. **State** — model registry, eligibility flags, ToS hashes, golden set versions, policy state. Low volume, row-level access, versioned.

Requirements: no parallel telemetry store (reuse existing infrastructure), zero new service deployment where possible.

## Decision

- **InfluxDB for time-series metrics.** Reuse the existing Argus InfluxDB instance. The observatory writes to its own bucket (`free_model_observatory`), keeping measurement names prefixed with `fmo_`.
- **SQLite for state.** Local file at `data/observatory.db`, committed schema migrations in `src/observatory/telemetry/schema.py`.

**Postgres was considered and rejected** because:
1. No existing Postgres instance in the Argus/InfluxDB stack that the observatory has access to.
2. SQLite is zero-deploy, zero-config, and the state volume is small enough (< 100K rows) that SQLite handles it trivially.
3. A migration path to Postgres exists if the dataset grows beyond SQLite's comfort zone.

## Consequences

- InfluxDB writes go through the existing InfluxDB MCP server or direct HTTP API.
- SQLite schema is versioned in code — migrations run on startup.
- Reports that need joined metric + state queries run locally against SQLite, with InfluxDB data fetched via its HTTP API.

## Revisit when

- State volume exceeds 1M rows or concurrent write contention becomes a problem.
- A shared Postgres instance becomes available in the observatory's runtime environment.
