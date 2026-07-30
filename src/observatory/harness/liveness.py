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


def _influxdb_config(thresholds: dict | None = None) -> dict:
    """Resolve InfluxDB configuration from thresholds + env overrides."""
    if thresholds is None:
        thresholds = load_thresholds()
    cfg = dict(thresholds.get("telemetry", {}).get("influxdb", {}))
    # Env overrides
    cfg["host"] = os.environ.get("INFLUXDB_HOST", cfg.get("host", "http://localhost:8086"))
    cfg["token"] = os.environ.get(cfg.get("token_env", "INFLUXDB_TOKEN"), "")
    cfg["org"] = os.environ.get(cfg.get("org_env", "INFLUXDB_ORG"), cfg.get("org", "incidium"))
    cfg["dry_run"] = os.environ.get(
        "INFLUXDB_DRY_RUN", str(cfg.get("dry_run", False))
    ).lower() == "true"
    return cfg


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


def _format_line_protocol(result: dict) -> str:
    """Format a probe result as InfluxDB line protocol.

    Returns a single line protocol string with:
      measurement=fmo_liveness
      tags=provider,model_id
      fields=ttft_ms,total_latency_ms,success,status_code
    """
    # Escape tag values (commas, spaces, equals)
    def _escape_tag(v: str) -> str:
        return v.replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")
    provider = _escape_tag(result.get("provider", "unknown"))
    model_id = _escape_tag(result.get("model_id", "unknown"))

    # Build fields — only include non-None numeric fields
    fields = []
    ttft = result.get("ttft_ms")
    if ttft is not None:
        fields.append(f"ttft_ms={ttft}i")
    lat = result.get("total_latency_ms")
    if lat is not None:
        fields.append(f"total_latency_ms={lat}i")
    sc = result.get("status_code")
    if sc is not None:
        fields.append(f"status_code={sc}i")
    fields.append(f"success={'1' if result.get('success') else '0'}i")

    error = result.get("error")
    if error:
        # Escape error string for field value
        escaped = error.replace("\\", "\\\\").replace('"', '\\"').replace(" ", "\\ ")
        fields.append(f'error="{escaped}"')

    field_str = ",".join(fields)

    # Timestamp in nanoseconds
    ts = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)

    return f"fmo_liveness,provider={provider},model_id={model_id} {field_str} {ts}"


def write_to_influxdb(result: dict, influx_cfg: dict | None = None) -> bool:
    """Write a liveness probe result to InfluxDB via HTTP API.

    Args:
        result: Probe result dict from probe_model().
        influx_cfg: InfluxDB config dict (resolved from thresholds + env).
            If None, resolves from thresholds and env vars.

    Returns:
        True if the write succeeded (or dry-run is enabled), False otherwise.
    """
    if influx_cfg is None:
        influx_cfg = _influxdb_config()

    line = _format_line_protocol(result)

    if influx_cfg.get("dry_run"):
        logger.info("[dry-run] InfluxDB point: %s", line)
        return True

    token = influx_cfg.get("token", "")
    if not token:
        logger.warning("InfluxDB token not set — skipping write. Set %s env var.",
                       influx_cfg.get("token_env", "INFLUXDB_TOKEN"))
        return False

    host = influx_cfg.get("host", "http://localhost:8086").rstrip("/")
    org = influx_cfg.get("org", "incidium")
    bucket = influx_cfg.get("bucket", "free_model_observatory")

    url = f"{host}/api/v2/write?org={org}&bucket={bucket}"
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "text/plain; charset=utf-8",
    }

    try:
        resp = requests.post(url, headers=headers, data=line, timeout=10)
        if resp.status_code == 204:
            logger.debug("InfluxDB write OK: %s/%s", result.get("provider"), result.get("model_id"))
            return True
        else:
            logger.warning("InfluxDB write failed: HTTP %d — %s", resp.status_code, resp.text[:200])
            return False
    except requests.RequestException as e:
        logger.warning("InfluxDB write error: %s", e)
        return False


def run_liveness(
    db_path: str | None = None,
    provider_filter: str | None = None,
    influx_cfg: dict | None = None,
) -> list[dict]:
    """Run the liveness check against all eligible models."""
    conn = init_db(db_path)
    models = get_eligible_models(conn)
    config = load_config()
    if influx_cfg is None:
        influx_cfg = _influxdb_config()

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

        # Write to InfluxDB
        write_to_influxdb(result, influx_cfg)

    conn.close()
    return results


def main():
    """CLI entry point for the liveness pinger."""
    parser = argparse.ArgumentParser(description="Free Model Observatory — Liveness Pinger")
    parser.add_argument("--provider", help="Probe only a specific provider")
    parser.add_argument("--db-path", help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate probe format without writing to InfluxDB")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    influx_cfg = _influxdb_config()
    if args.dry_run:
        influx_cfg["dry_run"] = True

    results = run_liveness(
        db_path=args.db_path,
        provider_filter=args.provider,
        influx_cfg=influx_cfg,
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
