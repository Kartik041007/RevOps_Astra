
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "outputs"


def load_csv(filename):
    path = DATA_DIR / filename

    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    return pd.read_csv(path)


def build_account_features():
    # Load source datasets
    accounts = load_csv("accounts.csv")
    usage = load_csv("product_usage_snapshots.csv")
    sales = load_csv("sales_transactions.csv")
    tickets = load_csv("support_tickets.csv")
    followups = load_csv("followups.csv")
    interactions = load_csv("interactions.csv")

    # Parse dates
    usage["snapshot_date"] = pd.to_datetime(
        usage["snapshot_date"], errors="coerce"
    )
    sales["transaction_date"] = pd.to_datetime(
        sales["transaction_date"], errors="coerce"
    )
    tickets["opened_at"] = pd.to_datetime(
        tickets["opened_at"], errors="coerce"
    )
    followups["due_date"] = pd.to_datetime(
        followups["due_date"], errors="coerce"
    )
    interactions["occurred_at"] = pd.to_datetime(
        interactions["occurred_at"], errors="coerce"
    )

    # Use the latest available usage date as analysis date
    reference_date = usage["snapshot_date"].max()

    if pd.isna(reference_date):
        raise ValueError("No valid usage snapshot dates found.")

    last_30_start = reference_date - pd.Timedelta(days=30)
    previous_30_start = reference_date - pd.Timedelta(days=60)
    last_90_start = reference_date - pd.Timedelta(days=90)

    # -----------------------------------------
    # 1. Product usage trends
    # -----------------------------------------
    recent_usage = usage[
        (usage["snapshot_date"] > last_30_start)
        & (usage["snapshot_date"] <= reference_date)
    ]

    previous_usage = usage[
        (usage["snapshot_date"] > previous_30_start)
        & (usage["snapshot_date"] <= last_30_start)
    ]

    recent = recent_usage.groupby("account_id").agg(
        usage_events_30d=("usage_events", "mean"),
        active_users_avg_30d=("active_users", "mean"),
        feature_adoption_avg_30d=("feature_adoption_pct", "mean"),
        error_events_30d=("error_events", "mean"),
        total_events_30d=("total_events", "mean"),
        latest_usage_date=("snapshot_date", "max"),
    )

    previous = previous_usage.groupby("account_id").agg(
        usage_events_previous_30d=("usage_events", "mean")
    )

    features = accounts.copy()
    features = features.merge(
        recent, on="account_id", how="left"
    )
    features = features.merge(
        previous, on="account_id", how="left"
    )

    previous_events = features["usage_events_previous_30d"]

    # Missing previous data remains unknown, not a 0% change.
    features["usage_change_pct"] = (
        (features["usage_events_30d"] - previous_events)
        / previous_events.where(previous_events > 0)
    ) * 100

    features["error_rate_30d_pct"] = (
        features["error_events_30d"]
        / features["total_events_30d"].where(
            features["total_events_30d"] > 0
        )
    ) * 100

    features["days_since_last_usage"] = (
        reference_date - features["latest_usage_date"]
    ).dt.days

    features["usage_data_gap"] = (
        features["usage_events_30d"].isna()
        | features["usage_events_previous_30d"].isna()
    )

    # -----------------------------------------
    # 2. Recorded sales revenue
    # -----------------------------------------
    sales["net_revenue"] = pd.to_numeric(
        sales["net_revenue"], errors="coerce"
    )

    recorded_sales = sales.groupby("account_id").agg(
        recorded_net_revenue=("net_revenue", "sum"),
        transaction_count=("transaction_id", "nunique"),
    )

    recent_sales = sales[
        (sales["transaction_date"] > last_90_start)
        & (sales["transaction_date"] <= reference_date)
    ]

    revenue_90d = recent_sales.groupby("account_id").agg(
        recorded_revenue_90d=("net_revenue", "sum"),
        transactions_90d=("transaction_id", "nunique"),
    )

    features = features.merge(
        recorded_sales, on="account_id", how="left"
    )
    features = features.merge(
        revenue_90d, on="account_id", how="left"
    )

    # These represent recorded transactions, not guaranteed
    # lifetime company revenue.
    for col in [
        "recorded_net_revenue",
        "transaction_count",
        "recorded_revenue_90d",
        "transactions_90d",
    ]:
        features[col] = features[col].fillna(0)

    # -----------------------------------------
    # 3. Support ticket burden
    # -----------------------------------------
    tickets["status_clean"] = (
        tickets["status"].astype(str).str.strip().str.lower()
    )
    tickets["priority_clean"] = (
        tickets["priority"].astype(str).str.strip().str.lower()
    )

    tickets["is_open"] = tickets["status_clean"].isin(
        ["open", "in_progress", "pending"]
    )
    tickets["is_high_priority_open"] = (
        tickets["is_open"]
        & tickets["priority_clean"].isin(["high", "urgent"])
    )

    ticket_features = tickets.groupby("account_id").agg(
        total_tickets=("ticket_id", "nunique"),
        open_tickets=("is_open", "sum"),
        high_priority_open_tickets=(
            "is_high_priority_open", "sum"
        ),
        avg_resolution_hours=("resolution_hours", "mean"),
    )

    features = features.merge(
        ticket_features, on="account_id", how="left"
    )

    # -----------------------------------------
    # 4. Pending and overdue follow-ups
    # -----------------------------------------
    followups["status_clean"] = (
        followups["status"].astype(str).str.strip().str.lower()
    )

    followups["is_pending"] = (
        followups["status_clean"] == "pending"
    )
    followups["is_overdue"] = (
        followups["is_pending"]
        & (followups["due_date"] < reference_date)
    )

    followup_features = followups.groupby("account_id").agg(
        total_followups=("followup_id", "nunique"),
        pending_followups=("is_pending", "sum"),
        overdue_followups=("is_overdue", "sum"),
    )

    features = features.merge(
        followup_features, on="account_id", how="left"
    )

    # -----------------------------------------
    # 5. Customer interaction insights
    # -----------------------------------------
    interactions["sentiment_clean"] = (
        interactions["sentiment"].astype(str).str.lower()
    )
    interactions["intent_clean"] = (
        interactions["intent"].astype(str).str.lower()
    )

    recent_interactions = interactions[
        (interactions["occurred_at"] > last_30_start)
        & (interactions["occurred_at"] <= reference_date)
    ].copy()

    recent_interactions["is_negative"] = (
        recent_interactions["sentiment_clean"] == "negative"
    )

    recent_interactions["is_expansion_intent"] = (
        recent_interactions["intent_clean"].isin(
            ["pricing_for_more", "expansion_interest"]
        )
    )

    interaction_features = recent_interactions.groupby(
        "account_id"
    ).agg(
        negative_interactions_30d=("is_negative", "sum"),
        expansion_intents_30d=("is_expansion_intent", "sum"),
    )

    last_contact = interactions.groupby("account_id").agg(
        last_interaction_date=("occurred_at", "max")
    )

    features = features.merge(
        interaction_features, on="account_id", how="left"
    )
    features = features.merge(
        last_contact, on="account_id", how="left"
    )

    features["days_since_last_interaction"] = (
        reference_date - features["last_interaction_date"]
    ).dt.days

    features["no_contact_45d"] = (
        features["days_since_last_interaction"].isna()
        | (features["days_since_last_interaction"] > 45)
    )

    features["possible_expansion_signal"] = (
        (features["usage_change_pct"] > 20)
        & (features["expansion_intents_30d"].fillna(0) > 0)
    )

    # Missing source rows become zero counts, but usage
    # values and percentages are deliberately not filled.
    count_columns = [
        "total_tickets",
        "open_tickets",
        "high_priority_open_tickets",
        "total_followups",
        "pending_followups",
        "overdue_followups",
        "negative_interactions_30d",
        "expansion_intents_30d",
    ]

    features[count_columns] = features[count_columns].fillna(0)

    features["analysis_date"] = reference_date.strftime("%Y-%m-%d")

    # Keep one row per company
    if features["account_id"].duplicated().any():
        raise ValueError("Duplicate account IDs in output.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "account_features.csv"

    features.to_csv(output_path, index=False)

    print(f"Analysis date: {reference_date.date()}")
    print(f"Accounts analysed: {len(features)}")
    print(f"Output saved to: {output_path}")

    print("\nTop accounts by recent revenue:")
    print(
        features[
            ["account_id", "company_name",
             "recorded_revenue_90d", "usage_change_pct"]
        ]
        .sort_values("recorded_revenue_90d", ascending=False)
        .head(10)
        .to_string(index=False)
    )

    print("\nAccounts with usage decline over 15%:")
    print(
        features.loc[
            features["usage_change_pct"] < -15,
            ["account_id", "company_name", "usage_change_pct",
             "recorded_net_revenue", "open_tickets",
             "overdue_followups"]
        ]
        .sort_values("recorded_net_revenue", ascending=False)
        .to_string(index=False)
    )

    print("\nMissing usage history:", int(features["usage_data_gap"].sum()))

    return features


if __name__ == "__main__":
    build_account_features()
