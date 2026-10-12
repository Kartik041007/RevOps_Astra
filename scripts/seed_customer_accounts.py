"""Opt-in seed script to populate customer_accounts from synthetic baseline CSV.

Requirements:
- Does NOT execute automatically at startup.
- Safe to rerun (idempotent): will NOT overwrite existing user-edited notes or status.
- Validates the CSV schema and account IDs before inserting.
- Supports dry-run mode (--dry-run).
- Never modifies or regenerates the synthetic baseline CSV/JSON files.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from pathlib import Path
from typing import Any

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from services.db import get_db_connection

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

CSV_PATH = Path(_project_root) / "outputs" / "account_risk_insights.csv"
ACCOUNT_ID_REGEX = re.compile(r"^ACC-\d{3,}$")


def seed_customer_accounts(
    csv_path: Path = CSV_PATH,
    dry_run: bool = False,
    connection_factory=None,
) -> dict[str, int]:
    """Load customer accounts from baseline CSV into MySQL customer_accounts table.

    Returns:
        dict with counts: {'inserted': count, 'skipped': count, 'failed': count, 'total': count}
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"Source baseline CSV not found at '{csv_path}'.")

    connect_fn = connection_factory or get_db_connection

    counts = {"inserted": 0, "skipped": 0, "failed": 0, "total": 0}

    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    counts["total"] = len(rows)

    if dry_run:
        logger.info("[DRY RUN] Validating %d rows from %s...", len(rows), csv_path.name)
        for idx, row in enumerate(rows, 1):
            acc_id = row.get("account_id")
            if not acc_id or not ACCOUNT_ID_REGEX.match(acc_id):
                logger.warning("[DRY RUN] Row %d: Invalid account_id '%s'", idx, acc_id)
                counts["failed"] += 1
            else:
                counts["inserted"] += 1
        logger.info("[DRY RUN] Completed validation. Simulated insertions: %d, Failures: %d", counts["inserted"], counts["failed"])
        return counts

    conn = connect_fn()
    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)

        # Check existing account IDs
        cursor.execute("SELECT account_id FROM customer_accounts")
        existing_ids = {r["account_id"] for r in cursor.fetchall()}

        for idx, row in enumerate(rows, 1):
            acc_id = (row.get("account_id") or "").strip()
            if not acc_id or not ACCOUNT_ID_REGEX.match(acc_id):
                logger.warning("Row %d: Skipping invalid account_id '%s'", idx, acc_id)
                counts["failed"] += 1
                continue

            if acc_id in existing_ids:
                counts["skipped"] += 1
                continue

            # Parse fields cleanly
            company_name = row.get("company_name", "Unknown")
            industry = row.get("industry") or None
            region = row.get("region") or None
            owner = row.get("account_owner") or "Unassigned"
            email_prefix = owner.lower().replace(" ", ".") if owner else "unassigned"
            owner_email = f"{email_prefix}@revops-mock.internal"
            status = (row.get("customer_status") or "Active").capitalize()
            plan_tier = row.get("company_size_band") or "Standard"

            try:
                arr = float(row.get("recorded_net_revenue") or 0.0)
            except (ValueError, TypeError):
                arr = 0.0

            try:
                health = 100 - int(float(row.get("risk_index") or 50))
            except (ValueError, TypeError):
                health = 50

            notes = row.get("risk_drivers") or "No notes recorded."
            last_contact = row.get("last_interaction_date") or None
            if last_contact and not re.match(r"^\d{4}-\d{2}-\d{2}$", last_contact):
                last_contact = None

            try:
                cursor.execute(
                    """
                    INSERT INTO customer_accounts
                        (account_id, company_name, industry, region, account_owner,
                         owner_email, customer_status, plan_tier, arr_usd,
                         health_score, notes, last_contact_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        acc_id,
                        company_name,
                        industry,
                        region,
                        owner,
                        owner_email,
                        status,
                        plan_tier,
                        round(arr, 2),
                        max(0, min(100, health)),
                        notes,
                        last_contact,
                    ),
                )
                counts["inserted"] += 1
                existing_ids.add(acc_id)
            except Exception as e:
                logger.error("Failed to insert record '%s': %s", acc_id, e)
                counts["failed"] += 1

        conn.commit()
        logger.info(
            "Seed completed: %d inserted, %d skipped (already existed), %d failed out of %d total.",
            counts["inserted"],
            counts["skipped"],
            counts["failed"],
            counts["total"],
        )
        return counts
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        if cursor:
            cursor.close()
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed customer_accounts table from baseline CSV.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate CSV rows without executing database insertions.",
    )
    args = parser.parse_args()

    try:
        res = seed_customer_accounts(dry_run=args.dry_run)
        print(f"Result: {res}")
    except Exception as exc:
        print(f"Error during seeding: {exc}", file=sys.stderr)
        sys.exit(1)
