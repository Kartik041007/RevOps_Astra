
"""Validate Revenue Risk Investigator analytics outputs."""

from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTPUTS = ROOT / "outputs"


def load_csv(folder, filename):
    path = folder / filename
    if not path.exists():
        raise FileNotFoundError(f"Required file missing: {path}")
    return pd.read_csv(path)


def check(condition, passed, failed, description):
    if condition:
        passed.append(description)
        print(f"[PASS] {description}")
    else:
        failed.append(description)
        print(f"[FAIL] {description}")


def main():
    passed = []
    failed = []
    warnings = []

    print("\n=== REVENUE RISK ANALYTICS VALIDATION ===\n")

    features = load_csv(OUTPUTS, "account_features.csv")
    insights = load_csv(OUTPUTS, "account_risk_insights.csv")
    sales = load_csv(DATA, "sales_transactions.csv")

    # 1. Customer row integrity
    check(
        features["account_id"].is_unique,
        passed, failed,
        "Account features contain one row per account."
    )

    check(
        insights["account_id"].is_unique,
        passed, failed,
        "Risk insights contain one row per account."
    )

    check(
        set(features["account_id"].astype(str))
        == set(insights["account_id"].astype(str)),
        passed, failed,
        "Feature and risk outputs contain the same accounts."
    )

    print(f"\nAccounts analysed: {insights['account_id'].nunique()}")

    # 2. Verify risk categories against configured thresholds
    score = pd.to_numeric(insights["risk_index"], errors="coerce")

    expected_level = score.map(
        lambda x: (
            "Urgent" if x >= 70
            else "Needs Review" if x >= 40
            else "Healthy"
        ) if pd.notna(x) else None
    )

    check(
        score.notna().all() and score.between(0, 100).all(),
        passed, failed,
        "Risk index is numeric and within 0–100."
    )

    check(
        expected_level.equals(insights["risk_level"].reset_index(drop=True)),
        passed, failed,
        "Risk levels match the configured scoring thresholds."
    )

    # 3. Confidence labels are separate from risk levels
    confidence = pd.to_numeric(
        insights["evidence_confidence"], errors="coerce"
    )

    expected_confidence = confidence.map(
        lambda x: (
            "High" if x >= 70
            else "Medium" if x >= 40
            else "Low"
        ) if pd.notna(x) else None
    )

    actual_confidence = insights["confidence_label"].reset_index(drop=True)

    check(
        confidence.notna().all() and confidence.between(0, 100).all(),
        passed, failed,
        "Evidence confidence is numeric and within 0–100."
    )

    check(
        expected_confidence.equals(actual_confidence),
        passed, failed,
        "Confidence labels match confidence scores."
    )

    # 4. Check revenue calculations against source transactions
    sales["transaction_date"] = pd.to_datetime(
        sales["transaction_date"], errors="coerce"
    )
    sales["net_revenue"] = pd.to_numeric(
        sales["net_revenue"], errors="coerce"
    )

    reference_dates = pd.to_datetime(
        features["analysis_date"], errors="coerce"
    ).dropna()

    if reference_dates.empty:
        raise ValueError("No valid analysis_date found in account features.")

    reference_date = reference_dates.max()
    start_date = reference_date - pd.Timedelta(days=90)

    print(f"\nAnalysis date: {reference_date.date()}")
    print(f"90-day revenue window: after {start_date.date()} "
          f"through {reference_date.date()}")

    valid_sales = sales.dropna(
        subset=["account_id", "transaction_date", "net_revenue"]
    ).copy()

    all_sales = valid_sales.groupby("account_id")["net_revenue"].sum()

    recent_sales = valid_sales[
        (valid_sales["transaction_date"] > start_date)
        & (valid_sales["transaction_date"] <= reference_date)
    ]

    recent_revenue = recent_sales.groupby("account_id")[
        "net_revenue"
    ].sum()

    # Align by account ID before comparing values.
    compare = features.set_index("account_id").copy()
    compare["source_lifetime_revenue"] = (
        compare.index.to_series().map(all_sales).fillna(0)
    )
    compare["source_revenue_90d"] = (
        compare.index.to_series().map(recent_revenue).fillna(0)
    )

    lifetime = pd.to_numeric(
        compare["recorded_net_revenue"], errors="coerce"
    )
    recent = pd.to_numeric(
        compare["recorded_revenue_90d"], errors="coerce"
    )

    lifetime_mismatch = (
        lifetime - compare["source_lifetime_revenue"]
    ).abs() > 0.05

    recent_mismatch = (
        recent - compare["source_revenue_90d"]
    ).abs() > 0.05

    check(
        not lifetime_mismatch.any(),
        passed, failed,
        "Lifetime recorded sales match source transactions."
    )

    check(
        not recent_mismatch.any(),
        passed, failed,
        "90-day recorded sales match the selected date window."
    )

    if lifetime_mismatch.any():
        print("\nLifetime revenue mismatches (first 5):")
        print(compare.loc[
            lifetime_mismatch,
            ["recorded_net_revenue", "source_lifetime_revenue"]
        ].head().to_string())

    if recent_mismatch.any():
        print("\n90-day revenue mismatches (first 5):")
        print(compare.loc[
            recent_mismatch,
            ["recorded_revenue_90d", "source_revenue_90d"]
        ].head().to_string())

    # 5. Data quality observations
    zero_recent_positive_lifetime = (
        (compare["source_revenue_90d"].abs() < 0.005)
        & (compare["source_lifetime_revenue"] > 0)
    ).sum()

    negative_lifetime_revenue = (
        compare["source_lifetime_revenue"] < 0
    ).sum()

    latest_transaction = valid_sales["transaction_date"].max()

    print("\n=== DATA QUALITY NOTES ===")
    print(
        "Accounts with zero 90-day sales but positive lifetime sales:",
        int(zero_recent_positive_lifetime)
    )
    print(
        "Accounts with negative lifetime net sales:",
        int(negative_lifetime_revenue)
    )
    print(
        "Latest transaction date:",
        latest_transaction.date() if pd.notna(latest_transaction) else "Unknown"
    )

    if latest_transaction < reference_date:
        warnings.append(
            "Latest sales transaction is older than the analysis date. "
            "Verify whether this is expected in the demo data."
        )

    if zero_recent_positive_lifetime:
        warnings.append(
            "Some accounts have historical sales but no recorded net sales "
            "in the selected 90-day window. This may be valid."
        )

    if negative_lifetime_revenue:
        warnings.append(
            "Some accounts have negative lifetime net sales; inspect returns "
            "or refunds before interpreting revenue exposure."
        )

    print("\n=== SUMMARY ===")
    print(f"Passed checks: {len(passed)}")
    print(f"Failed checks: {len(failed)}")
    print(f"Warnings: {len(warnings)}")

    for warning in warnings:
        print(f"[WARN] {warning}")

    if failed:
        print("\nValidation needs attention before dashboard integration.")
    else:
        print("\nAll validation checks passed.")


if __name__ == "__main__":
    main()
