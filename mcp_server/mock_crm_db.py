"""Safe in-memory mock CRM dataset for MCP server testing.

This module provides a realistic, isolated customer dataset for local
development and MCP tool execution without modifying real CRM records.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

CSV_INSIGHTS_PATH = Path(__file__).resolve().parent.parent / "outputs" / "account_risk_insights.csv"
MOCK_UPDATES_FILE = Path(__file__).resolve().parent.parent / "outputs" / "mock_crm_updates.json"

# Fallback seed records if the CSV file is unavailable
DEFAULT_SEEDS = [
    {
        "account_id": "ACC-001",
        "company_name": "Northstar Components",
        "industry": "Manufacturing",
        "region": "West",
        "account_owner": "Kabir Singh",
        "owner_email": "kabir.singh@example.com",
        "customer_status": "Active",
        "risk_level": "Urgent",
        "health_score": 30,
        "plan_tier": "Enterprise",
        "arr_usd": 9747.17,
        "notes": "Usage declined 18.4%. Executive outreach requested.",
        "last_contact_date": "2026-09-25",
    },
    {
        "account_id": "ACC-002",
        "company_name": "Ironclad Products",
        "industry": "Electronics",
        "region": "Central",
        "account_owner": "Neha Sharma",
        "owner_email": "neha.sharma@example.com",
        "customer_status": "Active",
        "risk_level": "Needs Review",
        "health_score": 50,
        "plan_tier": "Enterprise",
        "arr_usd": 33476.43,
        "notes": "Usage dropped 21.7%. Follow-up overdue.",
        "last_contact_date": "2026-09-23",
    },
    {
        "account_id": "ACC-003",
        "company_name": "Redwood Operations",
        "industry": "Automotive Supply",
        "region": "West",
        "account_owner": "Ananya Rao",
        "owner_email": "ananya.rao@example.com",
        "customer_status": "Active",
        "risk_level": "Urgent",
        "health_score": 30,
        "plan_tier": "Standard",
        "arr_usd": 25584.43,
        "notes": "Multiple open support tickets and negative interactions.",
        "last_contact_date": "2026-10-02",
    },
    {
        "account_id": "ACC-004",
        "company_name": "Brightforge Enterprises",
        "industry": "Healthcare Equipment",
        "region": "West",
        "account_owner": "Rohan Mehta",
        "owner_email": "rohan.mehta@example.com",
        "customer_status": "Active",
        "risk_level": "Needs Review",
        "health_score": 50,
        "plan_tier": "Enterprise",
        "arr_usd": 97390.26,
        "notes": "Recent support backlog and negative interactions.",
        "last_contact_date": "2026-10-09",
    },
    {
        "account_id": "ACC-005",
        "company_name": "Harbor Technologies",
        "industry": "Food Processing",
        "region": "Central",
        "account_owner": "Maya Patel",
        "owner_email": "maya.patel@example.com",
        "customer_status": "Active",
        "risk_level": "Urgent",
        "health_score": 30,
        "plan_tier": "Mid-market",
        "arr_usd": 147273.07,
        "notes": "Urgent review required. Declining adoption.",
        "last_contact_date": "2026-10-08",
    },
]


class MockCRMDatabase:
    """Isolated in-memory mock CRM database."""

    def __init__(self, use_csv_seeds: bool = True):
        self._records: dict[str, dict[str, Any]] = {}
        self._init_database(use_csv_seeds)

    def _init_database(self, use_csv: bool) -> None:
        if use_csv and CSV_INSIGHTS_PATH.exists():
            try:
                with open(CSV_INSIGHTS_PATH, mode="r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        acc_id = row.get("account_id")
                        if not acc_id:
                            continue
                        owner = row.get("account_owner", "Unassigned")
                        email_name = owner.lower().replace(" ", ".") if owner else "unassigned"
                        try:
                            arr = float(row.get("recorded_net_revenue") or 0.0)
                        except ValueError:
                            arr = 0.0
                        try:
                            health = 100 - int(float(row.get("risk_index") or 50))
                        except ValueError:
                            health = 50

                        self._records[acc_id] = {
                            "account_id": acc_id,
                            "company_name": row.get("company_name", "Unknown"),
                            "industry": row.get("industry", "Unknown"),
                            "region": row.get("region", "Unknown"),
                            "account_owner": owner,
                            "owner_email": f"{email_name}@revops-mock.internal",
                            "customer_status": (row.get("customer_status") or "Active").capitalize(),
                            "status": (row.get("customer_status") or "Active").capitalize(),
                            "risk_level": row.get("risk_level", "Unknown"),
                            "health_score": max(0, min(100, health)),
                            "plan_tier": row.get("company_size_band", "Standard"),
                            "arr_usd": round(arr, 2),
                            "notes": row.get("risk_drivers", "No notes recorded."),
                            "last_contact_date": row.get("last_interaction_date", "N/A"),
                        }
            except Exception:
                self._load_fallback_seeds()
        else:
            self._load_fallback_seeds()

        self._load_updates()

    def _load_fallback_seeds(self) -> None:
        self._records.clear()
        for seed in DEFAULT_SEEDS:
            rec = dict(seed)
            if "customer_status" in rec and "status" not in rec:
                rec["status"] = rec["customer_status"]
            self._records[seed["account_id"]] = rec

    def _load_updates(self) -> None:
        if MOCK_UPDATES_FILE.exists():
            try:
                with open(MOCK_UPDATES_FILE, mode="r", encoding="utf-8") as f:
                    updates_map = json.load(f)
                if isinstance(updates_map, dict):
                    for acc_id, field_updates in updates_map.items():
                        if acc_id in self._records and isinstance(field_updates, dict):
                            normalized = dict(field_updates)
                            if "status" in normalized and "customer_status" not in normalized:
                                normalized["customer_status"] = normalized["status"]
                            elif "customer_status" in normalized and "status" not in normalized:
                                normalized["status"] = normalized["customer_status"]
                            self._records[acc_id].update(normalized)
            except Exception:
                pass

    def reset_mock_updates(self) -> None:
        """Clear mock updates file and reload baseline data."""
        if MOCK_UPDATES_FILE.exists():
            try:
                MOCK_UPDATES_FILE.unlink()
            except Exception:
                pass
        self._init_database(use_csv=True)

    def get_customer(self, account_id: str) -> dict[str, Any] | None:
        """Retrieve a customer record by account ID."""
        self._load_updates()
        record = self._records.get(account_id)
        if record is None:
            return None
        return dict(record)

    def list_customers(self, limit: int = 10, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        """List customer records with pagination."""
        self._load_updates()
        all_items = list(self._records.values())
        total_count = len(all_items)
        paginated = all_items[offset : offset + limit]
        return [dict(item) for item in paginated], total_count

    def update_customer_in_memory(self, account_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update fields in mock database and persist to mock updates cache."""
        self._load_updates()
        if account_id not in self._records:
            raise KeyError(f"Customer {account_id} not found.")

        normalized_updates = dict(updates)
        if "status" in normalized_updates and "customer_status" not in normalized_updates:
            normalized_updates["customer_status"] = normalized_updates["status"]
        elif "customer_status" in normalized_updates and "status" not in normalized_updates:
            normalized_updates["status"] = normalized_updates["customer_status"]

        self._records[account_id].update(normalized_updates)
        try:
            current_updates = {}
            if MOCK_UPDATES_FILE.exists():
                with open(MOCK_UPDATES_FILE, mode="r", encoding="utf-8") as f:
                    current_updates = json.load(f) or {}
            current_updates.setdefault(account_id, {}).update(normalized_updates)
            MOCK_UPDATES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(MOCK_UPDATES_FILE, mode="w", encoding="utf-8") as f:
                json.dump(current_updates, f, indent=2)
        except Exception:
            pass
        return dict(self._records[account_id])


# Singleton mock DB instance for the MCP server process
mock_crm_db = MockCRMDatabase()
