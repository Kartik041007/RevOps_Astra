
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTPUT = ROOT / "outputs"


def read_csv(name):
    path = DATA / name
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    return pd.read_csv(path)


def find_id_column(df, candidates):
    for col in candidates:
        if col in df.columns:
            return col
    return None


def add_evidence(rows, account_id, company, signal_code,
                 polarity, source_type, source_id,
                 source_date, detail):
    rows.append({
        "account_id": account_id,
        "company_name": company,
        "signal_code": signal_code,
        "polarity": polarity,
        "source_type": source_type,
        "source_record_id": source_id,
        "source_date": source_date,
        "evidence_detail": detail,
    })


def build_evidence():
    features = pd.read_csv(OUTPUT / "account_risk_insights.csv")
    usage = read_csv("product_usage_snapshots.csv")
    tickets = read_csv("support_tickets.csv")
    followups = read_csv("followups.csv")
    interactions = read_csv("interactions.csv")

    usage["snapshot_date"] = pd.to_datetime(
        usage["snapshot_date"], errors="coerce"
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

    reference_date = pd.to_datetime(
        features["analysis_date"].iloc[0]
    )

    usage_id = find_id_column(
        usage, ["snapshot_id", "usage_id", "record_id"]
    )
    interaction_id = find_id_column(
        interactions, ["interaction_id", "source_record_id"]
    )

    rows = []

    for _, account in features.iterrows():
        aid = account["account_id"]
        company = account["company_name"]
        usage_change = pd.to_numeric(
            pd.Series([account.get("usage_change_pct")]),
            errors="coerce"
        ).iloc[0]

        # 1. Product usage evidence
        if pd.notna(usage_change) and usage_change <= -15:
            recent = usage[
                (usage["account_id"] == aid)
                & (usage["snapshot_date"] > reference_date - pd.Timedelta(days=30))
                & (usage["snapshot_date"] <= reference_date)
            ]
            previous = usage[
                (usage["account_id"] == aid)
                & (usage["snapshot_date"] > reference_date - pd.Timedelta(days=60))
                & (usage["snapshot_date"] <= reference_date - pd.Timedelta(days=30))
            ]

            ids = []
            for subset, label in [(previous, "previous"), (recent, "recent")]:
                if not subset.empty:
                    if usage_id:
                        ids.extend(subset[usage_id].astype(str).tolist())
                    else:
                        # Composite reference when no native row ID exists.
                        ids.extend(
                            (subset["account_id"].astype(str) + "@" +
                             subset["snapshot_date"].astype(str)).tolist()
                        )

            add_evidence(
                rows, aid, company,
                "R1_USAGE_DECLINE", "risk", "product_usage",
                ";".join(ids),
                reference_date.date().isoformat(),
                f"Usage change: {usage_change:.1f}% versus prior period. "
                f"Previous snapshots: {len(previous)}; "
                f"recent snapshots: {len(recent)}."
            )

        # 2. Support ticket evidence
        account_tickets = tickets[tickets["account_id"] == aid].copy()
        status = account_tickets["status"].astype(str).str.lower().str.strip()
        priority = account_tickets["priority"].astype(str).str.lower().str.strip()

        open_mask = status.isin(["open", "in_progress", "pending"])
        high_mask = priority.isin(["high", "urgent"])

        for _, ticket in account_tickets[open_mask & high_mask].iterrows():
            detail = (
                f"Priority: {ticket['priority']}; "
                f"status: {ticket['status']}; "
                f"subject: {ticket.get('subject', '')}; "
                f"description: {ticket.get('description', '')}"
            )
            add_evidence(
                rows, aid, company,
                "R2_HIGH_PRIORITY_TICKET", "risk", "support_ticket",
                str(ticket["ticket_id"]),
                str(ticket["opened_at"].date()) if pd.notna(ticket["opened_at"]) else "",
                detail
            )

        # 3. Open support backlog
        open_tickets = account_tickets[open_mask]
        if len(open_tickets) >= 2:
            add_evidence(
                rows, aid, company,
                "R3_SUPPORT_BACKLOG", "risk", "support_ticket",
                ";".join(open_tickets["ticket_id"].astype(str)),
                "",
                f"{len(open_tickets)} tickets are open or in progress."
            )

        # 4. Overdue pending follow-ups
        account_followups = followups[
            followups["account_id"] == aid
        ].copy()

        pending_mask = (
            account_followups["status"].astype(str).str.lower().str.strip()
            == "pending"
        )
        overdue_mask = (
            pending_mask
            & (account_followups["due_date"] < reference_date)
        )

        for _, task in account_followups[overdue_mask].iterrows():
            due = task["due_date"]
            days_late = (reference_date - due).days

            add_evidence(
                rows, aid, company,
                "R4_OVERDUE_FOLLOWUP", "risk", "followup",
                str(task["followup_id"]),
                str(due.date()),
                f"{task['title']}; reason: {task['reason']}; "
                f"overdue by {days_late} days; "
                f"owner: {task['assigned_owner']}."
            )

        # 5. Recent negative interactions
        account_interactions = interactions[
            (interactions["account_id"] == aid)
            & (interactions["occurred_at"] > reference_date - pd.Timedelta(days=30))
            & (interactions["occurred_at"] <= reference_date)
        ].copy()

        negative = account_interactions[
            account_interactions["sentiment"].astype(str).str.lower()
            .str.strip().isin(["negative", "very_negative"])
        ]

        for _, interaction in negative.iterrows():
            source_id = (
                str(interaction[interaction_id])
                if interaction_id
                else str(interaction.get("source_record_id", ""))
            )

            add_evidence(
                rows, aid, company,
                "R5_NEGATIVE_INTERACTION", "risk", "interaction",
                source_id,
                str(interaction["occurred_at"].date()),
                f"Intent: {interaction.get('intent', '')}; "
                f"subject: {interaction.get('subject', '')}; "
                f"message: {interaction.get('body', '')}"
            )

        # 6. Silence detector: represent absence as a gap,
        # not as a fabricated source record.
        if bool(account.get("no_contact_45d", False)):
            add_evidence(
                rows, aid, company,
                "G1_NO_RECENT_CONTACT", "gap", "interaction_gap",
                "",
                reference_date.date().isoformat(),
                "No interaction was recorded in the 45-day lookback window."
            )

        # 7. Expansion opportunity from usage + intent
        if bool(account.get("possible_expansion_signal", False)):
            add_evidence(
                rows, aid, company,
                "E1_POSSIBLE_EXPANSION", "expansion", "account_analysis",
                "",
                reference_date.date().isoformat(),
                "Usage growth and an expansion-related intent were detected. "
                "Review the underlying usage and interaction records."
            )

        # 8. Counter-evidence from recent positive/help-seeking messages
        helpful = account_interactions[
            account_interactions["intent"].astype(str).str.lower().str.contains(
                "help|onboarding|support", regex=True, na=False
            )
        ]

        for _, interaction in helpful.iterrows():
            source_id = (
                str(interaction[interaction_id])
                if interaction_id
                else str(interaction.get("source_record_id", ""))
            )

            add_evidence(
                rows, aid, company,
                "C1_HELP_SEEKING_CONTEXT", "counter", "interaction",
                source_id,
                str(interaction["occurred_at"].date()),
                f"Customer is seeking help. Intent: "
                f"{interaction.get('intent', '')}; "
                f"message: {interaction.get('body', '')}"
            )

    OUTPUT.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT / "account_evidence.csv"

    evidence = pd.DataFrame(rows, columns=[
        "account_id", "company_name", "signal_code", "polarity",
        "source_type", "source_record_id", "source_date",
        "evidence_detail"
    ])
    evidence.to_csv(output_file, index=False)

    print(f"Accounts analysed: {features['account_id'].nunique()}")
    print(f"Evidence records created: {len(evidence)}")
    print(f"Saved to: {output_file}")

    if not evidence.empty:
        print("\nEvidence by type:")
        print(evidence.groupby(
            ["source_type", "polarity"]
        ).size().to_string())

        print("\nSample evidence:")
        print(evidence.head(5).to_string(index=False))


if __name__ == "__main__":
    build_evidence()

