"""Liveness pinger — one-token probe for every eligible model.

Layer 2 of the Free Model Observatory.
Runs every 15 minutes. Captures TTFT (time to first token) and availability.
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

import requests
import yaml

from ..telemetry.schema import init_db

logger = logging.getLogger("observatory.harness.liveness")

# Thresholds
TARGET_PROMPT = "Reply with exactly one word: ok"
MAX_TOKENS = 1
TIMEOUT_SECONDS = 30


def _project_root() -> str:
    """Find the project root by walking up from the package directory."""
    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    current = os.path.dirname(pkg_dir)
    while current != "/":
        if os.path.isdir(os.path.join(current, "config")):
            return current
        current = os.path.dirname(current)
    return os.getcwd()


def load_config(config_path: str | None = None) -> dict:
    """Load configuration."""
    if config_path is None:
        config_path = os.path.join(_project_root(), "config", "providers.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


def load_thresholds(config_path: str | None = None) -> dict:
    """Load thresholds."""
    if config_path is None:
        config_path = os.path.join(_project_root(), "config", "thresholds.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_eligible_models(conn) -> list[dict]:
    """Get eligible models from the registry."""
    cursor = conn.execute(
        "SELECT model_id, provider FROM models WHERE eligible = 1 ORDER BY provider, model_id"
    )
    return [dict(row) for row in cursor.fetchall()]


def probe_model(
    model_id: str,
    provider: str,
    provider_config: dict,
) -> dict:
    """Probe a single model for liveness.

    Returns a dict with the probe result.
    """
    result = {
        "model_id": model_id,
        "provider": provider,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": False,
        "ttft_ms": None,
        "total_latency_ms": None,
        "error": None,
        "status_code": None,
    }

    api_base = provider_config.get("api_base")
    if not api_base:
        result["error"] = "no api_base"
        return result

    url = f"{api_base}/chat/completions"
    headers = {
        "Content-Type": "application/json",
    }

    # Auth
    auth_type = provider_config.get("auth_type", "none")
    if auth_type == "header":
        token = os.environ.get(provider_config.get("auth_env_var", ""))
        if token:
            prefix = provider_config.get("auth_prefix", "")
            headers[provider_config.get("auth_header", "Authorization")] = f"{prefix}{token}"

    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": TARGET_PROMPT}],
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }

    try:
        start = time.time()
        resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT_SECONDS)
        total_ms = int((time.time() - start) * 1000)
        result["total_latency_ms"] = total_ms
        result["status_code"] = resp.status_code

        if resp.status_code == 200:
            result["success"] = True
            # Estimate TTFT from response timing (approximate without streaming)
            # In v0.1, TTFT = total_latency_ms as a rough proxy
            # Streaming TTFT measurement will be added in Phase 1
            result["ttft_ms"] = total_ms
        elif resp.status_code == 429:
            result["error"] = "rate_limited"
            result["ttft_ms"] = None
        else:
            result["error"] = f"http_{resp.status_code}"

    except requests.Timeout:
        result["error"] = "timeout"
    except requests.ConnectionError:
        result["error"] = "connection_error"
    except requests.RequestException as e:
        result["error"] = str(e)
    except Exception as e:
        result["error"] = str(e)

    return result


def write_to_influxdb(result: dict) -> None:
    """Write a liveness probe result to InfluxDB.

    In v0.1, this is a placeholder — writes to stdout for pipeline consumption.
    Full InfluxDB integration will use the existing Argus InfluxDB MCP connection.
    """
    # Placeholder: InfluxDB write via HTTP API
    # The telemetry layer will be wired to the existing Argus InfluxDB
    # instance in the next iteration.
    pass


def run_liveness(
    db_path: str | None = None,
    provider_filter: str | None = None,
) -> list[dict]:
    """Run the liveness check against all eligible models."""
    conn = init_db(db_path)
    models = get_eligible_models(conn)
    config = load_config()

    if provider_filter:
        models = [m for m in models if m["provider"] == provider_filter]

    if not models:
        logger.warning("No eligible models found to probe")
        conn.close()
        return []

    results = []
    for model in models:
        provider_key = model["provider"]
        provider_config = config["providers"].get(provider_key)
        if not provider_config:
            logger.warning("No config for provider %s", provider_key)
            continue

        result = probe_model(model["model_id"], model["provider"], provider_config)
        results.append(result)

        if result["success"]:
            logger.info(
                "OK  %s/%s  latency=%dms",
                result["provider"], result["model_id"], result["total_latency_ms"],
            )
        else:
            logger.warning(
                "FAIL %s/%s  error=%s",
                result["provider"], result["model_id"], result["error"],
            )

        # Write to InfluxDB (placeholder)
        write_to_influxdb(result)

    conn.close()
    return results


def main():
    """CLI entry point for the liveness pinger."""
    parser = argparse.ArgumentParser(description="Free Model Observatory — Liveness Pinger")
    parser.add_argument("--provider", help="Probe only a specific provider")
    parser.add_argument("--db-path", help="Path to SQLite database")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    results = run_liveness(
        db_path=args.db_path,
        provider_filter=args.provider,
    )

    # Summary
    success_count = sum(1 for r in results if r["success"])
    fail_count = len(results) - success_count

    print("\nLiveness check complete:")
    print(f"  Models probed: {len(results)}")
    print(f"  Successful:    {success_count}")
    print(f"  Failed:        {fail_count}")

    if fail_count > 0:
        for r in results:
            if not r["success"]:
                print(f"    FAIL: {r['provider']}/{r['model_id']} — {r['error']}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
