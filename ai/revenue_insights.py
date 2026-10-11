
from collections import Counter
from pathlib import Path
import json

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "outputs"
INPUT_FILE = OUTPUT_DIR / "account_features.csv"

STATUS_ORDER = ["Urgent", "Needs Review", "Healthy"]


def to_number(value, default=0):
    """Convert a value to a number without crashing on missing data."""
    number = pd.to_numeric(value, errors="coerce")
    return default if pd.isna(number) else float(number)


def to_bool(value):
    """Handle booleans loaded from CSV."""
    return str(value).strip().lower() in {"true", "1", "yes"}


def assess_customer(row):
    """Create an explainable risk index from existing customer features."""
    score = 0
    risk_drivers = []
    counter_evidence = []
    driver_codes = []

    usage_change = to_number(row.get("usage_change_pct"), default=None)

    high_tickets = to_number(
        row.get("high_priority_open_tickets", 0)
    )
    open_tickets = to_number(row.get("open_tickets", 0))
    overdue = to_number(row.get("overdue_followups", 0))
    negative_interactions = to_number(
        row.get("negative_interactions_30d", 0)
    )

    # R1: Declining product usage
    if usage_change is not None:
        if usage_change <= -30:
            score += 25
            driver_codes.append("R1_USAGE_DROP_HIGH")
            risk_drivers.append(
                f"Usage declined {abs(usage_change):.1f}% "
                "over the comparison period"
            )
        elif usage_change <= -15:
            score += 15
            driver_codes.append("R1_USAGE_DROP_MODERATE")
            risk_drivers.append(
                f"Usage declined {abs(usage_change):.1f}% "
                "over the comparison period"
            )
        elif usage_change > 15:
            score -= 15
            counter_evidence.append(
                f"Product usage increased {usage_change:.1f}%"
            )

    # R2: High-priority support issues
    if high_tickets >= 1:
        score += 20
        driver_codes.append("R2_HIGH_PRIORITY_TICKET")
        risk_drivers.append(
            f"{int(high_tickets)} high-priority open ticket(s)"
        )

    # R3: Multiple unresolved support tickets
    if open_tickets >= 2:
        score += 10
        driver_codes.append("R3_SUPPORT_BACKLOG")
        risk_drivers.append(
            f"{int(open_tickets)} open support tickets"
        )

    # R4: Overdue sales follow-ups
    if overdue >= 1:
        score += 15
        driver_codes.append("R4_OVERDUE_FOLLOWUP")
        risk_drivers.append(
            f"{int(overdue)} overdue follow-up(s)"
        )

    # R5: Recent negative interactions
    if negative_interactions >= 1:
        score += 10
        driver_codes.append("R5_NEGATIVE_INTERACTION")
        risk_drivers.append(
            f"{int(negative_interactions)} negative interaction(s) "
            "in the recent period"
        )

    # R6: No recent customer contact
    no_contact = to_bool(row.get("no_contact_45d", False))
    if no_contact:
        score += 10
        driver_codes.append("R6_NO_RECENT_CONTACT")
        risk_drivers.append("No recorded customer contact for 45+ days")

    # Expansion is a separate opportunity, not a risk point.
    expansion_candidate = to_bool(
        row.get("possible_expansion_signal", False)
    )

    if expansion_candidate:
        counter_evidence.append(
            "Possible expansion signal: usage growth plus expansion intent"
        )

    # Keep the prototype index in the 0-100 range.
    risk_index = max(0, min(100, round(score)))

    if risk_index >= 70:
        risk_level = "Urgent"
    elif risk_index >= 40:
        risk_level = "Needs Review"
    else:
        risk_level = "Healthy"

    # Evidence confidence is separate from risk.
    confidence = 100

    if to_bool(row.get("usage_data_gap", False)):
        confidence -= 15

    days_since_usage = to_number(
        row.get("days_since_last_usage"), default=None
    )
    if days_since_usage is None or days_since_usage > 14:
        confidence -= 10

    if no_contact:
        confidence -= 10

    if len(driver_codes) + len(counter_evidence) < 2:
        confidence -= 10

    confidence = max(0, min(100, confidence))

    if confidence >= 90:
        confidence_label = "High"
    elif confidence >= 70:
        confidence_label = "Medium"
    else:
        confidence_label = "Low"

    # A high risk score with weak evidence still needs investigation.
    if confidence_label == "Low" and risk_level != "Healthy":
        risk_drivers.append(
            "Evidence is incomplete; verify before consequential action"
        )

    if not risk_drivers:
        risk_drivers.append("No configured risk trigger detected")

    return {
        "risk_index": risk_index,
        "risk_level": risk_level,
        "evidence_confidence": confidence,
        "confidence_label": confidence_label,
        "risk_driver_codes": driver_codes,
        "risk_drivers": risk_drivers,
        "counter_evidence": counter_evidence,
        "expansion_candidate": expansion_candidate,
    }


def build_revenue_insights():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"{INPUT_FILE} not found. "
            "Run: python ai/feature_engineering.py first."
        )

    df = pd.read_csv(INPUT_FILE)

    required_columns = [
        "account_id",
        "company_name",
        "usage_change_pct",
        "recorded_net_revenue",
        "recorded_revenue_90d",
        "open_tickets",
        "high_priority_open_tickets",
        "overdue_followups",
        "negative_interactions_30d",
        "no_contact_45d",
        "usage_data_gap",
        "days_since_last_usage",
        "possible_expansion_signal",
    ]

    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        raise ValueError(f"Missing feature columns: {missing}")

    if df["account_id"].duplicated().any():
        raise ValueError(
            "Duplicate account IDs found. Fix account features first."
        )

    # Assess every customer independently.
    assessments = [
        assess_customer(row)
        for _, row in df.iterrows()
    ]

    df["risk_index"] = [a["risk_index"] for a in assessments]
    df["risk_level"] = [a["risk_level"] for a in assessments]
    df["evidence_confidence"] = [
        a["evidence_confidence"] for a in assessments
    ]
    df["confidence_label"] = [
        a["confidence_label"] for a in assessments
    ]
    df["risk_driver_codes"] = [
        "; ".join(a["risk_driver_codes"]) for a in assessments
    ]
    df["risk_drivers"] = [
        "; ".join(a["risk_drivers"]) for a in assessments
    ]
    df["counter_evidence"] = [
        "; ".join(a["counter_evidence"]) for a in assessments
    ]
    df["expansion_candidate"] = [
        a["expansion_candidate"] for a in assessments
    ]

    # Risk distribution for dashboard charts.
    risk_counts = (
        df["risk_level"]
        .value_counts()
        .reindex(STATUS_ORDER, fill_value=0)
    )

    # Use explicit labels: recorded sales are not expected revenue loss.
    revenue_summary = (
        df.groupby("risk_level")
        .agg(
            account_count=("account_id", "nunique"),
            recorded_revenue=("recorded_net_revenue", "sum"),
            recorded_revenue_90d=("recorded_revenue_90d", "sum"),
        )
        .reindex(STATUS_ORDER, fill_value=0)
        .reset_index()
    )

    driver_counter = Counter()
    for assessment in assessments:
        driver_counter.update(assessment["risk_driver_codes"])

    top_accounts = (
        df.sort_values(
            ["risk_index", "recorded_revenue_90d"],
            ascending=[False, False]
        )
        [[
            "account_id",
            "company_name",
            "risk_index",
            "risk_level",
            "evidence_confidence",
            "confidence_label",
            "recorded_revenue_90d",
            "risk_drivers",
            "counter_evidence",
        ]]
        .head(10)
    )

    # Revenue associated with flagged accounts, not predicted loss.
    flagged = df[df["risk_level"].isin(["Urgent", "Needs Review"])]

    summary = {
        "total_accounts": int(df["account_id"].nunique()),
        "accounts_by_risk_level": {
            level: int(risk_counts[level])
            for level in STATUS_ORDER
        },
        "revenue_by_risk_level": json.loads(
            revenue_summary.to_json(orient="records")
        ),
        "recorded_revenue_90d_associated_with_flagged_accounts": round(
            float(flagged["recorded_revenue_90d"].sum()), 2
        ),
        "expansion_candidate_count": int(
            df["expansion_candidate"].sum()
        ),
        "risk_driver_counts": dict(driver_counter.most_common()),
        "top_priority_accounts": json.loads(
            top_accounts.to_json(orient="records")
        ),
        "scoring_note": (
            "Prototype rule-based index; not a churn probability. "
            "Revenue is recorded sales, not predicted revenue loss."
        ),
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    customer_output = OUTPUT_DIR / "account_risk_insights.csv"
    summary_output = OUTPUT_DIR / "dashboard_summary.json"

    df.to_csv(customer_output, index=False)

    with open(summary_output, "w", encoding="utf-8") as file:
        json.dump(summary, file, indent=2)

    print(f"Customers analysed: {summary['total_accounts']}")
    print("\nAccounts by risk level:")
    for level, count in summary["accounts_by_risk_level"].items():
        print(f"  {level}: {count}")

    print(
        "\nRecorded 90-day sales associated with flagged accounts:",
        summary[
            "recorded_revenue_90d_associated_with_flagged_accounts"
        ],
    )

    print("\nExpansion candidates:", summary["expansion_candidate_count"])
    print("\nTop priority customers:")
    print(
        top_accounts[
            [
                "company_name",
                "risk_index",
                "risk_level",
                "recorded_revenue_90d",
            ]
        ].to_string(index=False)
    )

    print(f"\nSaved customer results: {customer_output}")
    print(f"Saved dashboard summary: {summary_output}")


if __name__ == "__main__":
    build_revenue_insights()
