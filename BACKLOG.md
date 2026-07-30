# Backlog — deferred out of v0.1

Everything here was explicitly scoped out per the Instruction Set or emerged during development but does not serve the two core questions (routing and risk). Additions require an ADR.

## v0.2 candidates (propose with ADR)

- Custom dashboard or web UI. Use case covered by Grafana against existing InfluxDB.
- Image, audio, or video model evaluation. Text/LLM only in v0.1.
- Fine-tuning, distillation, or model hosting. Out of scope.
- Paid-tier or trial-credit models. Free-tier only.
- Parallel telemetry store. Reuse Argus's InfluxDB.

## v0.3+ candidates

- Multi-eval suite per job class (v0.1 caps at one).
- Provider count beyond five (v0.1 cap).
- Job classes beyond six (v0.1 cap).
- Mobile push for drift alerts.
- Historical ToS diff viewer.

## Rejected

(None yet.)
