
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "outputs"

RISK_LEVELS = ["Urgent", "Needs Review", "Healthy"]


def load_csv(path):
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return pd.read_csv(path)


def json_records(df):
    """Convert DataFrame rows to JSON-safe records."""
    return json.loads(df.to_json(orient="records", date_format="iso"))


def build_dashboard_data():
    # Load generated customer-level risk insights
    customers = load_csv(
        OUTPUT_DIR / "account_risk_insights.csv"
    )

    # Load product usage history
    usage = load_csv(
        DATA_DIR / "product_usage_snapshots.csv"
    )

    required_columns = [
        "account_id",
        "company_name",
        "risk_level",
        "risk_index",
        "risk_driver_codes",
        "recorded_net_revenue",
        "recorded_revenue_90d",
        "usage_change_pct",
        "open_tickets",
        "overdue_followups",
        "evidence_confidence",
        "confidence_label",
        "expansion_candidate",
    ]

    missing = [
        col for col in required_columns
        if col not in customers.columns
    ]

    if missing:
        raise ValueError(
            f"Missing columns in account_risk_insights.csv: {missing}"
        )

    if customers["account_id"].duplicated().any():
        raise ValueError("Duplicate account IDs found.")

    # -----------------------------------
    # 1. Customer count by risk category
    # -----------------------------------
    counts = customers["risk_level"].value_counts()

    risk_distribution = [
        {
            "risk_level": level,
            "customer_count": int(counts.get(level, 0)),
        }
        for level in RISK_LEVELS
    ]

    # -----------------------------------
    # 2. Recorded revenue by risk level
    # -----------------------------------
    revenue_by_risk = (
        customers.groupby("risk_level")
        .agg(
            customer_count=("account_id", "nunique"),
            recorded_revenue=("recorded_net_revenue", "sum"),
            recorded_revenue_90d=("recorded_revenue_90d", "sum"),
        )
        .reindex(RISK_LEVELS, fill_value=0)
        .rename_axis("risk_level")
        .reset_index()
    )

    # -----------------------------------
    # 3. Usage decline vs recorded revenue
    # -----------------------------------
    usage_vs_revenue = customers[
        [
            "account_id",
            "company_name",
            "usage_change_pct",
            "recorded_revenue_90d",
            "risk_level",
            "risk_index",
        ]
    ].copy()

    usage_vs_revenue = usage_vs_revenue.dropna(
        subset=["usage_change_pct", "recorded_revenue_90d"]
    )

    # -----------------------------------
    # 4. Frequency of risk drivers
    # -----------------------------------
    driver_rows = []

    for _, row in customers.iterrows():
        codes = str(row["risk_driver_codes"])

        if codes and codes.lower() != "nan":
            for code in codes.split(";"):
                code = code.strip()

                if code:
                    driver_rows.append({
                        "driver": code,
                        "account_id": row["account_id"],
                    })

    if driver_rows:
        driver_df = pd.DataFrame(driver_rows)

        risk_drivers = (
            driver_df.groupby("driver")
            .agg(
                customer_count=("account_id", "nunique")
            )
            .reset_index()
            .sort_values("customer_count", ascending=False)
        )
    else:
        risk_drivers = pd.DataFrame(
            columns=["driver", "customer_count"]
        )

    # -----------------------------------
    # 5. Product usage trend over time
    # -----------------------------------
    usage["snapshot_date"] = pd.to_datetime(
        usage["snapshot_date"], errors="coerce"
    )

    for column in ["usage_events", "active_users"]:
        usage[column] = pd.to_numeric(
            usage[column], errors="coerce"
        )

    valid_usage = usage.dropna(
        subset=["snapshot_date"]
    ).copy()

    usage_trend = (
        valid_usage.groupby("snapshot_date")
        .agg(
            total_usage_events=("usage_events", "sum"),
            average_active_users=("active_users", "mean"),
            account_count=("account_id", "nunique"),
        )
        .reset_index()
        .sort_values("snapshot_date")
    )

    # -----------------------------------
    # 6. Highest-priority customer list
    # -----------------------------------
    priority_accounts = (
        customers.sort_values(
            ["risk_index", "recorded_revenue_90d"],
            ascending=[False, False],
        )
        [
            [
                "account_id",
                "company_name",
                "risk_level",
                "risk_index",
                "evidence_confidence",
                "confidence_label",
                "usage_change_pct",
                "recorded_revenue_90d",
                "open_tickets",
                "overdue_followups",
                "risk_driver_codes",
            ]
        ]
        .head(20)
    )

    # -----------------------------------
    # 7. Expansion opportunity candidates
    # -----------------------------------
    expansion_mask = (
        customers["expansion_candidate"]
        .astype(str)
        .str.strip()
        .str.lower()
        .isin(["true", "1", "yes"])
    )

    expansion_candidates = customers.loc[
        expansion_mask,
        [
            "account_id",
            "company_name",
            "usage_change_pct",
            "recorded_net_revenue",
            "recorded_revenue_90d",
        ],
    ]

    # -----------------------------------
    # 8. Dashboard KPI values
    # -----------------------------------
    urgent = customers[
        customers["risk_level"] == "Urgent"
    ]

    needs_review = customers[
        customers["risk_level"] == "Needs Review"
    ]

    pending_followups = (
        pd.to_numeric(
            customers["overdue_followups"],
            errors="coerce",
        )
        .fillna(0)
        .sum()
    )

    dashboard = {
        "kpis": {
            "total_customers": int(
                customers["account_id"].nunique()
            ),
            "urgent_customers": int(len(urgent)),
            "customers_needing_review": int(len(needs_review)),
            "healthy_customers": int(
                (customers["risk_level"] == "Healthy").sum()
            ),
            "overdue_followups": int(pending_followups),
            "expansion_candidates": int(
                expansion_mask.sum()
            ),
            "recorded_revenue_90d_associated_with_flagged_accounts": round(
                float(
                    customers[
                        customers["risk_level"].isin(
                            ["Urgent", "Needs Review"]
                        )
                    ]["recorded_revenue_90d"].sum()
                ),
                2,
            ),
        },

        "risk_distribution": risk_distribution,
        "revenue_by_risk": json_records(revenue_by_risk),
        "usage_vs_revenue": json_records(usage_vs_revenue),
        "risk_drivers": json_records(risk_drivers),
        "usage_trend": json_records(usage_trend),
        "priority_accounts": json_records(priority_accounts),
        "expansion_candidates": json_records(expansion_candidates),

        "metric_notes": {
            "recorded_revenue": (
                "Recorded sales revenue, not predicted revenue loss."
            ),
            "risk_index": (
                "Rule-based prototype score, not churn probability."
            ),
            "overdue_followups": (
                "Counts overdue follow-up tasks, not all pending tasks."
            ),
        },
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_file = OUTPUT_DIR / "dashboard_charts.json"

    with open(output_file, "w", encoding="utf-8") as file:
        json.dump(
            dashboard,
            file,
            indent=2,
            allow_nan=False,
        )

    print("Dashboard data generated successfully.")
    print(f"Total customers: {dashboard['kpis']['total_customers']}")
    print(f"Urgent: {dashboard['kpis']['urgent_customers']}")
    print(
        "Needs Review:",
        dashboard["kpis"]["customers_needing_review"],
    )
    print(
        "Healthy:",
        dashboard["kpis"]["healthy_customers"],
    )
    print(
        "Expansion candidates:",
        dashboard["kpis"]["expansion_candidates"],
    )
    print(f"Saved to: {output_file}")


if __name__ == "__main__":
    build_dashboard_data()
