
"""Explainable rule-based revenue risk engine."""

from math import isnan


def _number(value, default=None):
    """Safely convert a value to a number."""
    try:
        result = float(value)
        return default if isnan(result) else result
    except (TypeError, ValueError):
        return default


def _as_bool(value):
    """Handle booleans loaded from Pandas/CSV."""
    return str(value).strip().lower() in {"true", "1", "yes"}


def assess_customer(row):
    """
    Assess a customer using transparent risk rules.

    Returns a risk index, separate evidence confidence,
    reasons, counter-evidence and an expansion indicator.
    """
    score = 0
    risk_drivers = []
    counter_evidence = []
    driver_codes = []

    usage_change = _number(
        row.get("usage_change_pct"), default=None
    )
    high_tickets = _number(
        row.get("high_priority_open_tickets"), default=0
    )
    open_tickets = _number(
        row.get("open_tickets"), default=0
    )
    overdue = _number(
        row.get("overdue_followups"), default=0
    )
    negative_interactions = _number(
        row.get("negative_interactions_30d"), default=0
    )

    # R1: Product usage decline
    if usage_change is not None:
        if usage_change <= -30:
            score += 25
            driver_codes.append("R1_USAGE_DROP_HIGH")
            risk_drivers.append(
                f"Product usage declined {abs(usage_change):.1f}%"
            )
        elif usage_change <= -15:
            score += 15
            driver_codes.append("R1_USAGE_DROP_MODERATE")
            risk_drivers.append(
                f"Product usage declined {abs(usage_change):.1f}%"
            )
        elif usage_change > 15:
            score -= 15
            counter_evidence.append(
                f"Product usage increased {usage_change:.1f}%"
            )

    # R2: High-priority open support issue
    if high_tickets >= 1:
        score += 20
        driver_codes.append("R2_HIGH_PRIORITY_TICKET")
        risk_drivers.append(
            f"{int(high_tickets)} high-priority open ticket(s)"
        )

    # R3: Support backlog
    if open_tickets >= 2:
        score += 10
        driver_codes.append("R3_SUPPORT_BACKLOG")
        risk_drivers.append(
            f"{int(open_tickets)} open support tickets"
        )

    # R4: Overdue follow-ups
    if overdue >= 1:
        score += 15
        driver_codes.append("R4_OVERDUE_FOLLOWUP")
        risk_drivers.append(
            f"{int(overdue)} overdue follow-up(s)"
        )

    # R5: Negative customer interactions
    if negative_interactions >= 1:
        score += 10
        driver_codes.append("R5_NEGATIVE_INTERACTION")
        risk_drivers.append(
            f"{int(negative_interactions)} recent negative interaction(s)"
        )

    # R6: Silence detector
    no_contact = _as_bool(row.get("no_contact_45d", False))

    if no_contact:
        score += 10
        driver_codes.append("R6_NO_RECENT_CONTACT")
        risk_drivers.append(
            "No recorded customer contact for 45+ days"
        )

    # Expansion is a separate opportunity, not a risk point.
    expansion_candidate = _as_bool(
        row.get("possible_expansion_signal", False)
    )

    if expansion_candidate:
        counter_evidence.append(
            "Possible expansion signal detected"
        )

    # Bound the prototype risk index to 0–100.
    risk_index = max(0, min(100, round(score)))

    if risk_index >= 70:
        risk_level = "Urgent"
    elif risk_index >= 40:
        risk_level = "Needs Review"
    else:
        risk_level = "Healthy"

    # Confidence is separate from the risk index.
    confidence = 100

    if _as_bool(row.get("usage_data_gap", False)):
        confidence -= 15

    days_since_usage = _number(
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

    if not risk_drivers:
        risk_drivers.append("No configured risk trigger detected")

    if confidence_label == "Low" and risk_index >= 40:
        risk_drivers.append(
            "Evidence is incomplete; verify before taking action"
        )

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
