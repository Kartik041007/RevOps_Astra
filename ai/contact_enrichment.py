"""Enrich customer risk insights with contacts and priority ranking."""

from pathlib import Path
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "outputs"

RISK_ORDER = {
    "Urgent": 1,
    "Needs Review": 2,
    "Healthy": 3,
}


def load_csv(path):
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")
    return pd.read_csv(path)


def normalize_ids(series):
    return series.astype("string").str.strip()


def to_bool(series):
    return (
        series.astype("string")
        .str.strip()
        .str.lower()
        .isin(["true", "1", "yes"])
    )


def build_customer_priority_list():
    # 1. Load existing risk insights and source tables
    risks = load_csv(OUTPUT_DIR / "account_risk_insights.csv")
    accounts = load_csv(DATA_DIR / "accounts.csv")
    contacts = load_csv(DATA_DIR / "contacts.csv")

    # 2. Validate required columns
    required_risk = {
        "account_id",
        "company_name",
        "risk_level",
        "risk_index",
    }

    missing = required_risk - set(risks.columns)
    if missing:
        raise ValueError(
            f"Risk insights are missing columns: {sorted(missing)}"
        )

    for name, frame in [
        ("accounts.csv", accounts),
        ("contacts.csv", contacts),
    ]:
        if "account_id" not in frame.columns:
            raise ValueError(f"{name} must contain account_id.")

    # 3. Normalize account IDs for reliable joins
    risks = risks.copy()
    accounts = accounts.copy()
    contacts = contacts.copy()

    risks["account_id"] = normalize_ids(risks["account_id"])
    accounts["account_id"] = normalize_ids(accounts["account_id"])
    contacts["account_id"] = normalize_ids(contacts["account_id"])

    if risks["account_id"].duplicated().any():
        raise ValueError("Risk insights contain duplicate account IDs.")

    if accounts["account_id"].duplicated().any():
        raise ValueError("accounts.csv contains duplicate account IDs.")

    # 4. Identify account-owner column in accounts.csv
    owner_candidates = [
        "account_owner",
        "owner",
        "owner_name",
        "assigned_owner",
        "sales_owner",
    ]

    owner_column = next(
        (column for column in owner_candidates if column in accounts.columns),
        None,
    )

    # Never create duplicate owner columns.
    account_fields = ["account_id"]

    optional_account_fields = [
        "industry",
        "region",
        "company_size_band",
        "customer_status",
        "company_type",
    ]

    for column in optional_account_fields:
        if column in accounts.columns and column not in risks.columns:
            account_fields.append(column)

    if owner_column:
        account_fields.append(owner_column)

    account_info = accounts[account_fields].copy()

    if owner_column:
        account_info = account_info.rename(
            columns={owner_column: "_owner_from_accounts"}
        )

    result = risks.merge(
        account_info,
        on="account_id",
        how="left",
        validate="one_to_one",
    )

    # Prefer owner from accounts.csv; fall back to risk insights.
    if "account_owner" not in result.columns:
        result["account_owner"] = pd.NA

    if "_owner_from_accounts" in result.columns:
        result["account_owner"] = result[
            "_owner_from_accounts"
        ].combine_first(result["account_owner"])

        result = result.drop(columns=["_owner_from_accounts"])

    # 5. Standardize contact columns where necessary
    if "contact_name" not in contacts.columns and "name" in contacts.columns:
        contacts = contacts.rename(
            columns={"name": "contact_name"}
        )

    if "email" not in contacts.columns and "contact_email" in contacts.columns:
        contacts = contacts.rename(
            columns={"contact_email": "email"}
        )

    if "job_title" not in contacts.columns and "title" in contacts.columns:
        contacts = contacts.rename(
            columns={"title": "job_title"}
        )

    # Ensure optional flags work even if represented as CSV strings.
    for flag in ["is_active", "is_champion", "is_exec_sponsor"]:
        if flag in contacts.columns:
            contacts[flag] = to_bool(contacts[flag])

    # 6. Select the best available contact per account
    sort_columns = ["account_id"]
    ascending = [True]

    for flag in ["is_active", "is_champion", "is_exec_sponsor"]:
        if flag in contacts.columns:
            sort_columns.append(flag)
            ascending.append(False)

    contacts = contacts.sort_values(
        sort_columns,
        ascending=ascending,
        kind="stable",
    )

    preferred_contacts = contacts.drop_duplicates(
        subset="account_id",
        keep="first",
    ).copy()

    desired_contact_fields = [
        "contact_id",
        "contact_name",
        "email",
        "job_title",
        "is_active",
        "is_champion",
        "is_exec_sponsor",
    ]

    contact_fields = ["account_id"] + [
        column
        for column in desired_contact_fields
        if column in preferred_contacts.columns
    ]

    contact_info = preferred_contacts[contact_fields].copy()

    # Avoid suffixes if contact columns already exist in risk output.
    contact_columns_to_replace = [
        column
        for column in desired_contact_fields
        if column in result.columns
    ]

    if contact_columns_to_replace:
        result = result.drop(columns=contact_columns_to_replace)

    result = result.merge(
        contact_info,
        on="account_id",
        how="left",
        validate="one_to_one",
    )

    # 7. Explicitly identify missing contact information
    for column in [
        "contact_name",
        "email",
        "job_title",
        "account_owner",
    ]:
        if column not in result.columns:
            result[column] = pd.NA

    result["contact_missing"] = result["contact_name"].isna()
    result["contact_email_missing"] = result["email"].isna()
    result["owner_missing"] = result["account_owner"].isna()

    # 8. Rank customers:
    #    Urgent > Needs Review > Healthy.
    #    Within a group: higher risk, higher 90-day revenue,
    #    more overdue tasks, then stronger usage decline.
    result["priority_group"] = (
        result["risk_level"]
        .map(RISK_ORDER)
        .fillna(4)
        .astype(int)
    )

    numeric_fields = [
        "risk_index",
        "recorded_revenue_90d",
        "overdue_followups",
        "usage_change_pct",
    ]

    for column in numeric_fields:
        if column in result.columns:
            result[column] = pd.to_numeric(
                result[column],
                errors="coerce",
            )
        else:
            result[column] = pd.NA

    result = result.sort_values(
        [
            "priority_group",
            "risk_index",
            "recorded_revenue_90d",
            "overdue_followups",
            "usage_change_pct",
        ],
        ascending=[True, False, False, False, True],
        na_position="last",
    ).reset_index(drop=True)

    result.insert(
        0,
        "priority_order",
        range(1, len(result) + 1),
    )

    # 9. Save dashboard-ready customer list
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "customer_priority_list.csv"

    result.to_csv(output_path, index=False)

    # 10. Print summary
    print("\n=== CUSTOMER CONTACT ENRICHMENT ===")
    print(f"Accounts processed: {len(result)}")
    print(f"Missing contact names: {int(result['contact_missing'].sum())}")
    print(
        "Missing contact emails:",
        int(result["contact_email_missing"].sum()),
    )
    print(f"Missing account owners: {int(result['owner_missing'].sum())}")

    print("\n=== PRIORITY COUNTS ===")
    print(
        result["risk_level"]
        .value_counts()
        .reindex(
            ["Urgent", "Needs Review", "Healthy"],
            fill_value=0,
        )
        .to_string()
    )

    preview = [
        "priority_order",
        "company_name",
        "contact_name",
        "email",
        "account_owner",
        "risk_level",
        "risk_index",
        "recorded_revenue_90d",
        "overdue_followups",
    ]

    print("\n=== TOP 10 PRIORITY CUSTOMERS ===")
    print(result[preview].head(10).to_string(index=False))
    print(f"\nSaved: {output_path}")

    return result


if __name__ == "__main__":
    try:
        build_customer_priority_list()
    except (FileNotFoundError, ValueError, KeyError) as exc:
        raise SystemExit(f"ERROR: {exc}")