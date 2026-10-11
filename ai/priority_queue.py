
"""Priority queue and confidence-based action routing.

Reads customer_priority_list.csv and generates:
- priority_queue.csv
- priority_queue.json

This module proposes routes only. It does not execute external actions.
"""

from pathlib import Path
import json

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "outputs"

RISK_ORDER = {
    "Urgent": 1,
    "Needs Review": 2,
    "Healthy": 3,
}

CONFIDENCE_ORDER = {
    "High": 1,
    "Medium": 2,
    "Low": 3,
}


def load_csv(path):
    if not path.exists():
        raise FileNotFoundError(
            f"Required file not found: {path}"
        )
    return pd.read_csv(path)


def is_true(value):
    if value is None:
        return False

    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass

    return str(value).strip().lower() in {
        "true", "1", "yes"
    }


def numeric_column(df, column):
    if column not in df.columns:
        df[column] = 0

    df[column] = pd.to_numeric(
        df[column], errors="coerce"
    ).fillna(0)


def route_customer(row):
    """Choose a suggested route from risk and evidence confidence."""

    risk = str(row.get("risk_level", "")).strip()
    confidence = str(row.get("confidence_label", "Low")).strip()
    expansion = is_true(row.get("expansion_candidate", False))

    # Low evidence confidence always requires verification first.
    if confidence == "Low":
        return {
            "recommended_route": "gather_information",
            "needs_action": True,
            "proposed_action_types": ["task"],
            "route_reason": (
                "Evidence confidence is Low. Verify missing or stale "
                "information before customer-facing action."
            ),
        }

    # Urgent + strong evidence: prioritise immediate investigation.
    if risk == "Urgent" and confidence == "High":
        return {
            "recommended_route": "urgent_high_confidence",
            "needs_action": True,
            "proposed_action_types": [
                "task", "email", "crm_update", "notify"
            ],
            "route_reason": (
                "Urgent risk with High evidence confidence. Prepare "
                "a priority check-in and owner escalation for review."
            ),
        }

    # Urgent + medium evidence: check facts before stronger intervention.
    if risk == "Urgent" and confidence == "Medium":
        return {
            "recommended_route": "urgent_medium_confidence",
            "needs_action": True,
            "proposed_action_types": [
                "task", "email", "crm_update", "notify"
            ],
            "route_reason": (
                "Urgent risk with Medium confidence. Verify the situation "
                "and prepare a softer check-in for human review."
            ),
        }

    if risk == "Needs Review":
        return {
            "recommended_route": "investigate_and_check_in",
            "needs_action": True,
            "proposed_action_types": [
                "task", "email", "crm_update", "notify"
            ],
            "route_reason": (
                "Review the risk drivers and prepare an appropriate "
                "customer check-in for approval."
            ),
        }

    # Expansion is separate from churn risk.
    if expansion:
        return {
            "recommended_route": "expansion_followup",
            "needs_action": True,
            "proposed_action_types": [
                "task", "email", "crm_update", "notify"
            ],
            "route_reason": (
                "A possible expansion signal exists. Verify the usage "
                "trend and customer intent before outreach."
            ),
        }

    return {
        "recommended_route": "monitor_only",
        "needs_action": False,
        "proposed_action_types": [],
        "route_reason": (
            "No configured risk or expansion rule currently requires action."
        ),
    }


def build_priority_queue():
    input_path = OUTPUT_DIR / "customer_priority_list.csv"
    df = load_csv(input_path).copy()

    required = {
        "account_id",
        "company_name",
        "risk_level",
        "risk_index",
        "confidence_label",
    }

    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Customer priority list missing columns: {sorted(missing)}"
        )

    if df["account_id"].duplicated().any():
        raise ValueError(
            "Duplicate account IDs found in customer priority list."
        )

    # Make optional sorting fields safe if they are absent.
    numeric_fields = [
        "risk_index",
        "recorded_revenue_90d",
        "overdue_followups",
        "high_priority_open_tickets",
        "open_tickets",
        "usage_change_pct",
    ]

    for column in numeric_fields:
        numeric_column(df, column)

    if "expansion_candidate" not in df.columns:
        df["expansion_candidate"] = False

    # Create one routing decision per account.
    routes = [
        route_customer(row)
        for _, row in df.iterrows()
    ]

    df["recommended_route"] = [
        route["recommended_route"] for route in routes
    ]
    df["needs_action"] = [
        route["needs_action"] for route in routes
    ]
    df["proposed_action_types"] = [
        json.dumps(route["proposed_action_types"])
        for route in routes
    ]
    df["route_reason"] = [
        route["route_reason"] for route in routes
    ]

    # Queue priority:
    # 1. Urgent before Needs Review before Healthy
    # 2. High confidence before Medium before Low within each category
    # 3. Higher risk index first
    # 4. More overdue follow-ups and high-priority tickets first
    # 5. More severe usage decline before less severe decline
    df["priority_group"] = (
        df["risk_level"].map(RISK_ORDER).fillna(4).astype(int)
    )
    df["confidence_group"] = (
        df["confidence_label"]
        .map(CONFIDENCE_ORDER)
        .fillna(4)
        .astype(int)
    )

    df = df.sort_values(
        [
            "priority_group",
            "confidence_group",
            "risk_index",
            "overdue_followups",
            "high_priority_open_tickets",
            "open_tickets",
            "usage_change_pct",
            "recorded_revenue_90d",
        ],
        ascending=[
            True, True, False, False, False, False, True, False
        ],
        na_position="last",
        kind="stable",
    ).reset_index(drop=True)

    df.insert(0, "queue_position", range(1, len(df) + 1))

    # Save both formats for backend/frontend integration.
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_path = OUTPUT_DIR / "priority_queue.csv"
    json_path = OUTPUT_DIR / "priority_queue.json"

    df.to_csv(csv_path, index=False)

    records = json.loads(
        df.to_json(orient="records", date_format="iso")
    )

    with open(json_path, "w", encoding="utf-8") as file:
        json.dump(
            records,
            file,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )

    print("\n=== PRIORITY QUEUE GENERATED ===")
    print(f"Total customers: {len(df)}")
    print(f"Urgent: {(df['risk_level'] == 'Urgent').sum()}")
    print(
        f"Needs Review: {(df['risk_level'] == 'Needs Review').sum()}"
    )
    print(f"Healthy: {(df['risk_level'] == 'Healthy').sum()}")
    print(f"Customers needing action: {int(df['needs_action'].sum())}")

    print("\n=== TOP 10 CUSTOMERS ===")
    preview_columns = [
        "queue_position",
        "company_name",
        "risk_level",
        "risk_index",
        "confidence_label",
        "recommended_route",
    ]
    print(df[preview_columns].head(10).to_string(index=False))

    print(f"\nCSV saved: {csv_path}")
    print(f"JSON saved: {json_path}")
    print("No email or external CRM action was executed.")

    return df


if __name__ == "__main__":
    try:
        build_priority_queue()
    except (FileNotFoundError, ValueError, KeyError) as exc:
        raise SystemExit(f"ERROR: {exc}")
