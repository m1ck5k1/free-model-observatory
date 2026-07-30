"""Router policy generator — emits router-policy.json after daily probe.

Layer 4 of the Free Model Observatory.
Output consumed by hermes-agent and Iris.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import yaml

from ..telemetry.schema import init_db

logger = logging.getLogger("observatory.policy")

DEFAULT_OUTPUT_PATH = "router-policy.json"
FLOOR_MODEL = "ollama/local-floor"


def _project_root() -> str:
    """Find the project root by walking up from the package directory."""
    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    current = os.path.dirname(pkg_dir)
    while current != "/":
        if os.path.isdir(os.path.join(current, "config")):
            return current
        current = os.path.dirname(current)
    return os.getcwd()


def load_thresholds(config_path: str | None = None) -> dict:
    """Load thresholds configuration."""
    if config_path is None:
        config_path = os.path.join(_project_root(), "config", "thresholds.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


def load_sensitive_terms(config_path: str | None = None) -> list[str]:
    """Load sensitive terms from config."""
    if config_path is None:
        config_path = os.path.join(_project_root(), "config", "sensitive_terms.yaml")
    with open(config_path) as f:
        data = yaml.safe_load(f)
    return data.get("terms", [])


def get_eligible_models(conn) -> list[dict]:
    """Get eligible models ordered by provider."""
    cursor = conn.execute(
        """SELECT model_id, provider, retention_policy
           FROM models
           WHERE eligible = 1
           ORDER BY provider, model_id"""
    )
    return [dict(row) for row in cursor.fetchall()]


def generate_policy(
    db_path: str | None = None,
    output_path: str | None = None,
) -> dict:
    """Generate the router policy from current registry state.

    In v0.1, this produces a simple chain sorted by provider.
    Full quality-gated ordering will be added in Phase 3.
    """
    conn = init_db(db_path)
    models = get_eligible_models(conn)

    # Load config
    has_sensitive_terms = len(load_sensitive_terms()) > 0

    # Build job-class chains
    # In v0.1, all eligible models go into a flat chain per job class.
    # Phase 3 will add quality-gated reordering.

    no_train_models = [
        f"{m['provider']}/{m['model_id']}"
        for m in models
        if m["retention_policy"] == "no_train"
    ]

    all_models = [
        f"{m['provider']}/{m['model_id']}"
        for m in models
    ]

    # Ensure floor is always at the end of every chain
    # (deduplicate if it's already in the list)
    if FLOOR_MODEL in all_models:
        all_models.remove(FLOOR_MODEL)
    all_models.append(FLOOR_MODEL)

    if FLOOR_MODEL in no_train_models:
        no_train_models.remove(FLOOR_MODEL)
    no_train_models.append(FLOOR_MODEL)

    now = datetime.now(timezone.utc).isoformat()

    # Get the next policy version
    cursor = conn.execute("SELECT COALESCE(MAX(policy_version), 0) + 1 FROM policy_history")
    (next_version,) = cursor.fetchone()

    # Define job classes with their constraints
    # tool_call is the primary focus in v0.1 (per ADR-FMO-002)
    policy = {
        "generated_at": now,
        "policy_version": next_version,
        "job_classes": {
            "tool_call": {
                "chain": all_models,
                "constraints": {
                    "max_ttft_ms": 4000,
                    "requires_no_train": has_sensitive_terms,
                },
                "updated_at": now,
            },
            "intent_classify": {
                "chain": all_models,
                "constraints": {
                    "max_ttft_ms": 2000,
                    "requires_no_train": has_sensitive_terms,
                },
                "updated_at": now,
            },
            "voice_turn": {
                "chain": all_models,
                "constraints": {
                    "max_ttft_ms": 500,  # strict ceiling for Iris
                    "requires_no_train": True,
                },
                "updated_at": now,
            },
            "summarise": {
                "chain": all_models,
                "constraints": {
                    "max_ttft_ms": 5000,
                    "requires_no_train": False,
                },
                "updated_at": now,
            },
            "code": {
                "chain": all_models,
                "constraints": {
                    "max_ttft_ms": 10000,
                    "requires_no_train": False,
                },
                "updated_at": now,
            },
            "long_context_retrieval": {
                "chain": all_models,
                "constraints": {
                    "max_ttft_ms": 5000,
                    "requires_no_train": False,
                },
                "updated_at": now,
            },
        },
    }

    # Write policy to output
    if output_path is None:
        output_path = DEFAULT_OUTPUT_PATH

    with open(output_path, "w") as f:
        json.dump(policy, f, indent=2)

    # Record in policy history
    chain_count = sum(len(jc["chain"]) for jc in policy["job_classes"].values())
    conn.execute(
        """INSERT INTO policy_history (policy_version, generated_at, policy_json, chain_count)
           VALUES (?, ?, ?, ?)""",
        (next_version, now, json.dumps(policy), chain_count),
    )
    conn.commit()
    conn.close()

    logger.info("Policy v%d written to %s (%d chains across %d job classes)",
                next_version, output_path, chain_count, len(policy["job_classes"]))

    return policy


def main():
    """CLI entry point for the policy generator."""
    parser = argparse.ArgumentParser(description="Free Model Observatory — Router Policy Generator")
    parser.add_argument("--output", help=f"Output path (default: {DEFAULT_OUTPUT_PATH})")
    parser.add_argument("--db-path", help="Path to SQLite database")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    policy = generate_policy(
        db_path=args.db_path,
        output_path=args.output,
    )

    classes = list(policy["job_classes"].keys())
    print(f"\nRouter policy v{policy['policy_version']} generated:")
    print(f"  Job classes: {len(classes)}")
    for cls in classes:
        chain_len = len(policy["job_classes"][cls]["chain"])
        print(f"    {cls}: {chain_len} models in chain")
    print(f"  Output: {args.output or DEFAULT_OUTPUT_PATH}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
