"""Telemetry query utilities for the Free Model Observatory."""

import argparse
import sys

from ..telemetry.schema import init_db


def query_latest(db_path: str | None = None) -> None:
    """Query the latest state of the model registry."""
    conn = init_db(db_path)
    cursor = conn.execute(
        """SELECT model_id, provider, eligible, retention_policy,
                  tos_hash, source_confidence, last_seen
           FROM models
           ORDER BY provider, model_id"""
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("No models in registry.")
        return

    header = f"{'Provider':20} {'Model ID':40} {'Eligible':10} {'Retention':15} "
    header += f"{'Confidence':10} {'Last Seen':25}"
    print(header)
    print("-" * 120)
    for row in rows:
        line = (
            f"{row['provider']:20} {row['model_id']:40} "
            f"{str(row['eligible']):10} {row['retention_policy']:15} "
            f"{row['source_confidence']:10} {row['last_seen']:25}"
        )
        print(line)


def query_eligible_only(db_path: str | None = None) -> None:
    """Query only eligible models."""
    conn = init_db(db_path)
    cursor = conn.execute(
        """SELECT model_id, provider, retention_policy
           FROM models
           WHERE eligible = 1
           ORDER BY provider, model_id"""
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("No eligible models.")
        return

    print(f"{'Provider':20} {'Model ID':40} {'Retention':15}")
    print("-" * 75)
    for row in rows:
        print(f"{row['provider']:20} {row['model_id']:40} {row['retention_policy']:15}")


def main():
    parser = argparse.ArgumentParser(description="Free Model Observatory — Telemetry Query")
    parser.add_argument("--db-path", help="Path to SQLite database")
    parser.add_argument("--latest", action="store_true", help="Show latest registry state")
    parser.add_argument("--eligible-only", action="store_true", help="Show only eligible models")
    args = parser.parse_args()

    if args.latest:
        query_latest(args.db_path)
    elif args.eligible_only:
        query_eligible_only(args.db_path)
    else:
        # Default: show latest
        query_latest(args.db_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
