"""Registry harvester — polls provider catalogues and updates the models table.

Layer 1 of the Free Model Observatory.
Never hardcodes a model list. The catalogue is volatile on a scale of days.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

import requests
import yaml

from ..telemetry.schema import init_db

logger = logging.getLogger("observatory.harvester")

# Provider catalogue types and their parser functions
CATALOGUE_PARSERS = {}


def register_parser(catalogue_type: str):
    """Decorator to register a catalogue parser function."""
    def decorator(func):
        CATALOGUE_PARSERS[catalogue_type] = func
        return func
    return decorator


def _project_root() -> str:
    """Find the project root by walking up from the package directory."""
    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Walk up until we find config/ or reach filesystem root
    current = os.path.dirname(pkg_dir)
    while current != "/":
        if os.path.isdir(os.path.join(current, "config")):
            return current
        current = os.path.dirname(current)
    # Fallback: assume CWD is project root
    return os.getcwd()


def load_config(config_path: str | None = None) -> dict:
    """Load provider configuration from config/providers.yaml."""
    if config_path is None:
        config_path = os.path.join(_project_root(), "config", "providers.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


def normalise_tos_html(html: str) -> str:
    """Normalise a ToS HTML page for stable hashing.

    Strips <script>, <style>, <svg>, <noscript> tags and their content,
    extracts visible text, collapses whitespace, and lowercases.
    This produces a hash that changes only when the substantive terms
    change, not on every deploy (CSRF tokens, build IDs, nonces, timestamps).
    """
    # Remove script, style, svg, noscript blocks (tag + content)
    text = re.sub(r'<(?:script|style|svg|noscript)[^>]*>.*?</(?:script|style|svg|noscript)>',
                  '', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove all HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode common HTML entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&nbsp;', ' ').replace('&quot;', '"').replace('&#39;', "'")
    # Collapse whitespace (including newlines, tabs)
    text = re.sub(r'\s+', ' ', text)
    # Strip leading/trailing whitespace and lowercase
    text = text.strip().lower()
    return text


def compute_tos_hash(url: str, timeout: int = 15) -> str | None:
    """Fetch the ToS/data-retention page and return its SHA-256 hash.

    The HTML is normalised before hashing — stripping scripts, styles,
    and nonces — so the hash detects substantive terms changes, not
    deployment noise.
    """
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        normalised = normalise_tos_html(resp.text)
        return hashlib.sha256(normalised.encode("utf-8")).hexdigest()
    except requests.RequestException as e:
        logger.warning("Failed to fetch ToS from %s: %s", url, e)
        return None
    except Exception as e:
        logger.warning("Unexpected error fetching ToS from %s: %s", url, e)
        return None


def fetch_catalogue(provider_config: dict) -> list[dict]:
    """Fetch the model catalogue from a provider's API endpoint.

    Returns a list of raw model dicts from the provider's catalogue.
    """
    if provider_config["catalogue_type"] == "local":
        # Local floor model — no remote catalogue
        return [{
            "id": "local-floor",
            "provider": "ollama",
            "context_window": 4096,
            "modalities": ["text"],
        }]

    url = provider_config["catalogue_url"]
    headers = {}
    params = {}

    # Auth setup
    auth_type = provider_config.get("auth_type", "none")
    if auth_type == "header":
        token = os.environ.get(provider_config.get("auth_env_var", ""))
        if token:
            prefix = provider_config.get("auth_prefix", "")
            headers[provider_config.get("auth_header", "Authorization")] = f"{prefix}{token}"
    elif auth_type == "query_param":
        token = os.environ.get(provider_config.get("auth_env_var", ""))
        if token:
            params[provider_config.get("auth_param", "key")] = token

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error("Failed to fetch catalogue from %s: %s", url, e)
        return []


@register_parser("openai_compatible")
def parse_openai_compatible(raw: dict, provider_config: dict) -> list[dict]:
    """Parse an OpenAI-compatible catalogue response.

    OpenRouter, Groq, and Cerebras all use OpenAI's /v1/models format:
    { "data": [{ "id": "...", "object": "model", ... }] }
    """
    models = []
    filter_suffix = provider_config.get("filter")
    known_free = provider_config.get("free_model_identifiers")

    for item in raw.get("data", []):
        model_id = item.get("id", "")
        if filter_suffix and filter_suffix not in model_id:
            continue
        if known_free and model_id not in known_free:
            # If the provider has a known-free list and this model isn't in it,
            # skip it (unless we have a filter suffix, which already handled it)
            continue

        models.append({
            "id": model_id,
            "provider": provider_config["display_name"],
            "context_window": item.get("context_length") or item.get("context_window"),
            "modalities": ["text"],
        })

    return models


@register_parser("google")
def parse_google(raw: dict, provider_config: dict) -> list[dict]:
    """Parse Google AI Studio catalogue response.

    Google format: { "models": [{ "name": "models/gemini-2.0-flash", ... }] }
    """
    models = []
    known_free = provider_config.get("free_model_identifiers", [])

    for item in raw.get("models", []):
        model_id = item.get("name", "")
        # Strip "models/" prefix
        if model_id.startswith("models/"):
            model_id = model_id[7:]

        if known_free and model_id not in known_free:
            continue

        models.append({
            "id": model_id,
            "provider": provider_config["display_name"],
            "context_window": item.get("inputTokenLimit") or item.get("context_window"),
            "modalities": ["text"],
        })

    return models


@register_parser("local")
def parse_local(raw: list, provider_config: dict) -> list[dict]:
    """Local floor model — just return the raw list."""
    return raw


def compute_eligibility(model: dict) -> tuple[bool, str | None]:
    """Compute eligibility for a model based on static rules.

    Returns (eligible: bool, ineligible_reason: str | None).

    Rules:
    - trains_on_input → ineligible
    - unknown → ineligible (default — fail closed)
    - no_train → eligible ONLY if verified_by is set (human attestation required)
    - no_train without verified_by → ineligible (defence against unverified claims)
    """
    retention = model.get("retention_policy", "unknown")
    if retention == "trains_on_input":
        return False, "trains_on_input"
    if retention == "unknown":
        return False, "retention_policy_unknown"
    if retention == "no_train":
        # Require human attestation — no_train without verified_by is rejected
        if not model.get("verified_by"):
            return False, "no_train_without_verification"
        return True, None
    return False, f"unrecognized_retention_policy:{retention}"


def check_tos_change(
    conn,
    provider: str,
    tos_url: str | None,
    new_hash: str | None,
) -> bool:
    """Check if a ToS hash has changed and record the event if so.

    Returns True if a change was detected.
    """
    if not tos_url or not new_hash:
        return False

    cursor = conn.execute(
        "SELECT tos_hash, MAX(last_seen) FROM models WHERE provider = ? AND tos_url = ?",
        (provider, tos_url),
    )
    row = cursor.fetchone()

    if row and row[0] and row[0] != new_hash:
        # ToS hash changed — record the event
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO tos_changes (provider, tos_url, old_hash, new_hash, detected_at)
               VALUES (?, ?, ?, ?, ?)""",
            (provider, tos_url, row[0], new_hash, now),
        )
        logger.warning("ToS hash changed for %s: %s -> %s", provider, row[0], new_hash)
        return True

    return False


def check_new_model(conn, model_id: str, provider: str) -> bool:
    """Check if a model is new (not previously seen).

    Returns True if this is a new model detection.
    """
    cursor = conn.execute(
        "SELECT 1 FROM model_detections WHERE provider = ? AND model_id = ?",
        (provider, model_id),
    )
    if cursor.fetchone() is None:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO model_detections (provider, model_id, detected_at)
               VALUES (?, ?, ?)""",
            (provider, model_id, now),
        )
        logger.info("New model detected: %s / %s", provider, model_id)
        return True
    return False


def run_harvest(
    db_path: str | None = None,
    force_refresh: bool = False,
    dry_run: bool = False,
) -> dict:
    """Run the registry harvester against all configured providers.

    Returns a summary dict with counts of models added, updated, and events.
    """
    config = load_config()
    conn = init_db(db_path) if not dry_run else None

    summary = {
        "providers_contacted": 0,
        "models_found": 0,
        "models_upserted": 0,
        "new_models": 0,
        "tos_changes": 0,
        "errors": [],
    }
    now = datetime.now(timezone.utc).isoformat()

    for provider_key, provider_config in config["providers"].items():
        if provider_config.get("catalogue_type") == "local":
            # Local floor — always present, skip remote polling
            continue

        logger.info("Harvesting %s (%s)", provider_config["display_name"], provider_key)
        summary["providers_contacted"] += 1

        raw = fetch_catalogue(provider_config)
        if not raw:
            summary["errors"].append(f"{provider_key}: empty catalogue response")
            continue

        # Parse catalogue
        parser = CATALOGUE_PARSERS.get(provider_config["catalogue_type"])
        if not parser:
            summary["errors"].append(
                f"{provider_key}: no parser for {provider_config['catalogue_type']}"
            )
            continue

        models = parser(raw, provider_config)
        summary["models_found"] += len(models)

        if dry_run or conn is None:
            logger.info("  [dry-run] Would process %d models from %s", len(models), provider_key)
            continue

        # Upsert models
        for model in models:
            model_id = model["id"]
            is_new = check_new_model(conn, model_id, provider_key)

            # Set retention_policy from provider config (human judgement, not catalogue)
            model["retention_policy"] = provider_config.get(
                "retention_policy",
                provider_config.get("default_retention_policy", "unknown"),
            )
            # Pass verification fields for eligibility gate
            model["verified_by"] = provider_config.get("verified_by")
            model["evidence_url"] = provider_config.get("evidence_url")
            model["evidence_quote_hash"] = provider_config.get("evidence_quote_hash")

            # Fetch ToS hash if available
            tos_url = provider_config.get("tos_url")
            tos_hash = None
            if tos_url and force_refresh:
                tos_hash = compute_tos_hash(tos_url)

            if is_new:
                summary["new_models"] += 1

            # Check ToS change
            if tos_url and tos_hash:
                if check_tos_change(conn, provider_key, tos_url, tos_hash):
                    summary["tos_changes"] += 1

            # Compute eligibility
            eligible, ineligible_reason = compute_eligibility(model)

            # Upsert
            conn.execute(
                """INSERT INTO models (
                       model_id, provider, first_seen, last_seen,
                       context_window, modalities, declared_rate_limit,
                       licence, tos_url, tos_hash, retention_policy,
                       jurisdiction, eligible, ineligible_reason, source_confidence
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(provider, model_id) DO UPDATE SET
                       last_seen = excluded.last_seen,
                       context_window = COALESCE(excluded.context_window, models.context_window),
                       tos_hash = COALESCE(excluded.tos_hash, models.tos_hash),
                       tos_url = COALESCE(excluded.tos_url, models.tos_url),
                       eligible = excluded.eligible,
                       ineligible_reason = excluded.ineligible_reason""",
                (
                    model_id,
                    provider_key,
                    now if is_new else conn.execute(
                        "SELECT first_seen FROM models WHERE provider=? AND model_id=?",
                        (provider_key, model_id),
                    ).fetchone()[0],
                    now,
                    model.get("context_window"),
                    json.dumps(model.get("modalities", ["text"])),
                    json.dumps(model.get("declared_rate_limit", {})),
                    model.get("licence"),
                    tos_url,
                    tos_hash,
                    model.get("retention_policy", "unknown"),
                    model.get("jurisdiction"),
                    eligible,
                    ineligible_reason,
                    provider_config.get("source_confidence", "high"),
                ),
            )
            summary["models_upserted"] += 1

        if conn is not None:
            conn.commit()

    if conn is not None:
        conn.close()

    return summary


def main():
    """CLI entry point for the registry harvester."""
    parser = argparse.ArgumentParser(description="Free Model Observatory — Registry Harvester")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Force full refresh (re-hash ToS)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without writing")
    parser.add_argument("--db-path", help="Path to SQLite database (default: data/observatory.db)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    summary = run_harvest(
        db_path=args.db_path,
        force_refresh=args.force_refresh,
        dry_run=args.dry_run,
    )

    print("\nHarvest complete:")
    print(f"  Providers contacted: {summary['providers_contacted']}")
    print(f"  Models found:        {summary['models_found']}")
    print(f"  Models upserted:     {summary['models_upserted']}")
    print(f"  New models:          {summary['new_models']}")
    print(f"  ToS changes:         {summary['tos_changes']}")
    if summary["errors"]:
        print(f"  Errors:              {len(summary['errors'])}")
        for err in summary["errors"]:
            print(f"    - {err}")

    return 0 if not summary["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
