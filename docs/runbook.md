# Free Model Observatory — Runbook
# Must be independently executable by Alex Walker and James Benson.

## Prerequisites

- Python 3.12+
- API keys for providers (see config/providers.yaml)
- InfluxDB access (Argus stack)
- Git

## Quick start

```bash
# Clone
git clone https://github.com/m1ck5k1/free-model-observatory.git
cd free-model-observatory

# Set up Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Set API keys (or use .env)
export OPENROUTER_API_KEY="sk-..."
export GOOGLE_API_KEY="..."
export GROQ_API_KEY="gsk_..."
export MISTRAL_API_KEY="..."
export CEREBRAS_API_KEY="..."

# Run the registry harvester
python -m observatory.harvester.run

# Run the liveness pinger (one-shot)
python -m observatory.harness.liveness

# Run tests
pytest
```

## Layered operations

### L1 — Registry Harvester

```bash
# Normal run (update existing models, detect new ones)
python -m observatory.harvester.run

# Force full refresh (re-hash ToS, re-check all models)
python -m observatory.harvester.run --force-refresh

# Dry run (print what would change, don't write)
python -m observatory.harvester.run --dry-run
```

### L2 — Probe Harness

```bash
# Liveness check (one-shot)
python -m observatory.harness.liveness

# Daily quota probe
python -m observatory.harness.daily --smoke 5

# Full weekly capability suite
python -m observatory.harness.capability --job-class tool_call

# Shadow scoring (one-shot replay)
python -m observatory.harness.shadow --replay
```

### L3 — Telemetry

```bash
# View registry state
python -m observatory.telemetry.query --latest

# Check eligibility status
python -m observatory.telemetry.query --eligible-only

# Export metrics to InfluxDB (manual)
python -m observatory.telemetry.export
```

### L4 — Router Policy

```bash
# Generate router policy
python -m observatory.policy.generate

# Validate current policy
python -m observatory.policy.validate
```

## Scheduled tasks (n8n or systemd)

| Task | Schedule | Command |
|---|---|---|
| Registry harvest | Every 6h | `python -m observatory.harvester.run` |
| Liveness | Every 15 min | `python -m observatory.harness.liveness` |
| Daily probe | 06:00 UTC | `python -m observatory.harness.daily` |
| Weekly suite | Sun 03:00 UTC | `python -m observatory.harness.capability --all` |
| Policy refresh | After daily probe | `python -m observatory.policy.generate` |

## Alerting

Alerts fire via ntfy on the PCP. The observatory writes to topic `fmo-alerts`.

**Alert conditions:**
- New model detected in registry
- ToS hash changed for any provider
- p95 latency breach > 2s vs 7-day baseline
- 429 rate > 25% of total attempts
- Shadow score regression > 15%
- Weekly suite skipped 3 consecutive weeks (kill criteria)

## Troubleshooting

### Registry harvester fails

1. Check provider API key is set and valid
2. Check network connectivity to provider endpoint
3. Check `config/providers.yaml` for correct catalogue URL
4. Run with `--dry-run` to isolate which provider fails

### Liveness check fails

1. Run against a single provider: `python -m observatory.harness.liveness --provider openrouter`
2. Check InfluxDB connectivity
3. Check that the provider hasn't changed its API

### SQLite database is locked

1. Check for concurrent processes
2. The observatory uses WAL mode — this should be rare
3. Delete `data/observatory.db` (registry will be re-harvested)