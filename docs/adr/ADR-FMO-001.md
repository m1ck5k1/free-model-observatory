# ADR-FMO-001: Provider Selection

**Status:** Accepted (2026-07-30)
**Owner:** Michael Brewer
**Scope:** Registry harvester v0.1

## Context

The registry must poll live model catalogues from free-tier LLM providers. Hard cap of five providers in v0.1, plus the local Ollama floor model (not counted against the cap).

## Decision

The v0.1 provider set is:

1. **OpenRouter** — largest aggregated free model selection. Filter for `:free` suffix on model IDs. Machine-readable catalogue via `https://openrouter.ai/api/v1/models`.
2. **Google AI Studio / Gemini free tier** — strong capability, free tier has generous daily quota. Machine-readable via `https://generativelanguage.googleapis.com/v1/models`.
3. **Groq** — low-latency inference, LPU architecture. Machine-readable via `https://api.groq.com/openai/v1/models`.
4. **Mistral free tier** — `mistral-small-latest`, `open-mistral-nemo` available free. Catalogue via `https://api.mistral.ai/v1/models`.
5. **Cerebras** — wafer-scale, fast inference, free tier. Catalogue via `https://api.cerebras.ai/v1/models`.
6. **Ollama (local floor)** — always present, not counted against the five. No remote catalogue; configured locally.

**Dropped from candidate set:**
- Claude (Anthropic) — no meaningful free tier.
- GPT-4o mini (OpenAI) — free tier via ChatGPT only, no API free tier.

## Consequences

- Three providers have OpenAI-compatible catalogues (OpenRouter, Groq, Cerebras) → shared parser logic.
- Google uses a different schema — parser branch needed.
- Mistral is OpenAI-compatible but its free-tier models list may not be explicitly flagged; requires filtering by known free model IDs.
- Cerebras has the least documentation on free-tier limits — `source_confidence: low` for initial scraping.

## Rationale

- OpenRouter first because it is already the default hermes provider — the most immediate routing value.
- Google Gemini free tier included for capability floor (Gemini 2.0 Flash is surprisingly capable at no cost).
- Groq for latency — essential for Iris voice turn.
- Mistral and Cerebras for diversity of architecture and geography.
- Ollama floor for guaranteed fallback — hermes degrades, never dies.

## Revisit when

- A provider exits free tier or adds meaningful free tier.
- Provider count ceiling is challenged via ADR.
