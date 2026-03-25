#!/usr/bin/env python3
"""
Reset the Orion global circuit breaker directly in TimescaleDB.

Usage:
    uv run python scripts/reset_circuit_breaker.py [--db-url URL] [--dry-run]

Set DB_URL or ORION_DB_URL env var, or pass --db-url on the command line.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

import psycopg2  # type: ignore[import-untyped]

DEFAULT_DB_URL = os.environ.get("DB_URL") or os.environ.get("ORION_DB_URL", "")
CB_KEY = "GLOBAL_CIRCUIT_BREAKER"


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset Orion global circuit breaker")
    parser.add_argument("--db-url", default=DEFAULT_DB_URL, help="PostgreSQL connection URL (or set DB_URL env var)")
    parser.add_argument("--dry-run", action="store_true", help="Show current state without modifying")
    args = parser.parse_args()

    if not args.db_url:
        print("ERROR: No DB URL provided. Set DB_URL env var or pass --db-url.", file=sys.stderr)
        sys.exit(1)

    try:
        conn = psycopg2.connect(args.db_url)
        conn.autocommit = False
        cur = conn.cursor()
    except Exception as e:
        print(f"ERROR: Cannot connect to database: {e}", file=sys.stderr)
        sys.exit(1)

    # Read current state
    cur.execute("SELECT key, status, details, last_updated_utc FROM system_status WHERE key = %s", (CB_KEY,))
    row = cur.fetchone()

    if row is None:
        print(f"No circuit breaker record found (key={CB_KEY}). System is nominal.")
        cur.close()
        conn.close()
        return

    key, status, details, last_updated = row
    print(f"Current state: status={status}, details={details}, last_updated={last_updated}")

    if status != "OPEN":
        print("Circuit breaker is already CLOSED. Nothing to do.")
        cur.close()
        conn.close()
        return

    if args.dry_run:
        print("[DRY RUN] Would reset circuit breaker to CLOSED.")
        cur.close()
        conn.close()
        return

    # Reset it
    now = datetime.now(UTC)
    cur.execute(
        "UPDATE system_status SET status = %s, details = %s, last_updated_utc = %s WHERE key = %s",
        ("CLOSED", f"Manual reset via script at {now.isoformat()}", now, CB_KEY),
    )
    conn.commit()

    # Verify
    cur.execute("SELECT status, details, last_updated_utc FROM system_status WHERE key = %s", (CB_KEY,))
    new_row = cur.fetchone()
    print(f"Reset complete: status={new_row[0]}, details={new_row[1]}, last_updated={new_row[2]}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
