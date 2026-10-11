
"""Revenue Risk Investigator: priority-driven agent workflow.

Reads the generated priority queue, proposes actions, requires human review,
executes safe local demo actions, and records every decision in an audit log.

Email sending and live CRM updates require external connectors and are
intentionally not executed by this local demo.
"""

from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
import sys
import uuid

import pandas as pd


# ---------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "outputs"

QUEUE_FILE = OUTPUT_DIR / "priority_queue.csv"
CONTACT_LIST_FILE = OUTPUT_DIR / "customer_priority_list.csv"
EVIDENCE_FILE = OUTPUT_DIR / "account_evidence.csv"

ACTIONS_FILE = OUTPUT_DIR / "proposed_actions.json"
TASKS_FILE = OUTPUT_DIR / "tasks.json"
NOTIFICATIONS_FILE = OUTPUT_DIR / "notifications.json"
AUDIT_FILE = OUTPUT_DIR / "audit_log.jsonl"


# ---------------------------------------------------------------------
# General utilities
# ---------------------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_csv(path):
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")
    return pd.read_csv(path)


def read_json(path):
    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list in {path}")

    return data


def write_json(path, data):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def append_audit(record):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(AUDIT_FILE, "a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def clean(value):
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if hasattr(value, "item"):
        return value.item()

    return value


def split_ids(value):
    if value is None:
        return []

    try:
        if pd.isna(value):
            return []
    except (TypeError, ValueError):
        pass

    return [
        part.strip()
        for part in str(value).split(";")
        if part.strip()
    ]


# ---------------------------------------------------------------------
# Customer and evidence lookup
# ---------------------------------------------------------------------

def get_queue_customer(account_id):
    """Load one customer's routing decision and priority data."""
    queue = read_csv(QUEUE_FILE)

    required = {
        "account_id",
        "company_name",
        "risk_level",
        "risk_index",
        "confidence_label",
        "recommended_route",
        "needs_action",
        "proposed_action_types",
        "route_reason",
    }

    missing = required - set(queue.columns)
    if missing:
        raise ValueError(
            f"Priority queue is missing columns: {sorted(missing)}. "
            "Regenerate it using python ai/priority_queue.py."
        )

    matches = queue[
        queue["account_id"].astype(str) == str(account_id)
    ]

    if matches.empty:
        raise ValueError(
            f"Account {account_id} is not present in the priority queue."
        )

    row = matches.iloc[0]

    try:
        action_types = json.loads(row["proposed_action_types"])
    except (json.JSONDecodeError, TypeError):
        raise ValueError(
            f"Invalid proposed_action_types for {account_id}."
        )

    if not isinstance(action_types, list):
        raise ValueError("proposed_action_types must be a JSON list.")

    return row, action_types


def get_customer_contact(account_id):
    """Get the selected contact and account owner."""
    if not CONTACT_LIST_FILE.exists():
        return {}

    customers = read_csv(CONTACT_LIST_FILE)
    matches = customers[
        customers["account_id"].astype(str) == str(account_id)
    ]

    if matches.empty:
        return {}

    row = matches.iloc[0]

    return {
        "contact_name": clean(row.get("contact_name")),
        "email": clean(row.get("email")),
        "account_owner": clean(row.get("account_owner")),
    }


def get_evidence_ids(account_id):
    """Collect source IDs linked to the customer's evidence records."""
    if not EVIDENCE_FILE.exists():
        return []

    evidence = read_csv(EVIDENCE_FILE)

    if "account_id" not in evidence.columns:
        return []

    selected = evidence[
        evidence["account_id"].astype(str) == str(account_id)
    ]

    ids = set()

    if "source_record_id" in selected.columns:
        for value in selected["source_record_id"].dropna():
            ids.update(split_ids(value))

    return sorted(ids)


def get_llm_draft(account_id):
    """Read the local LLM result generated by llm_service.py."""
    path = OUTPUT_DIR / f"llm_brief_{account_id}.json"

    if not path.exists():
        raise FileNotFoundError(
            f"LLM draft not found: {path}\n"
            f"Run: python ai/llm_service.py {account_id}"
        )

    with open(path, "r", encoding="utf-8") as file:
        result = json.load(file)

    return result.get("draft", {})


# ---------------------------------------------------------------------
# Proposal creation
# ---------------------------------------------------------------------

def make_action(account_id, company, action_type, payload,
                route, route_reason, rationale, evidence_ids):
    return {
        "action_id": f"ACT-{uuid.uuid4().hex[:10].upper()}",
        "account_id": str(account_id),
        "company_name": company,
        "type": action_type,
        "payload": payload,
        "route": route,
        "route_reason": route_reason,
        "rationale": rationale,
        "evidence_signal_ids": evidence_ids,
        "status": "pending",
        "execution_status": "not_executed",
        "created_at": now_iso(),
    }


def propose_actions(account_id):
    """Create actions permitted by the priority queue's routing decision."""
    row, allowed_types = get_queue_customer(account_id)

    company = str(row["company_name"])
    route = str(row["recommended_route"])
    route_reason = str(row["route_reason"])

    needs_action_value = str(row["needs_action"]).strip().lower()
    needs_action = needs_action_value in {"true", "1", "yes"}

    if not needs_action or not allowed_types:
        print(f"No action required for {company}.")
        print(f"Route: {route}")
        print(f"Reason: {route_reason}")
        return []

    # Avoid creating duplicate pending proposals for the same account.
    actions = read_json(ACTIONS_FILE)

    existing_pending = [
        action for action in actions
        if str(action.get("account_id")) == str(account_id)
        and action.get("status") == "pending"
    ]

    if existing_pending:
        print(
            f"{len(existing_pending)} pending action(s) already exist "
            f"for {company}; no duplicates created."
        )

        for action in existing_pending:
            print(
                f"{action['action_id']} | {action['type']} | "
                f"{action['status']}"
            )

        return existing_pending

    allowed_set = set(allowed_types)
    valid_types = {"task", "email", "crm_update", "notify"}

    if not allowed_set.issubset(valid_types):
        raise ValueError(
            f"Unsupported action types in priority queue: "
            f"{sorted(allowed_set - valid_types)}"
        )

    contact = get_customer_contact(account_id)
    evidence_ids = get_evidence_ids(account_id)

    owner = contact.get("account_owner") or "unassigned"
    contact_name = contact.get("contact_name") or ""
    contact_email = contact.get("email")

    risk_level = str(row["risk_level"])
    confidence = str(row["confidence_label"])
    risk_index = clean(row["risk_index"])

    # Create an LLM draft only when email is an allowed action.
    draft = {}

    if "email" in allowed_set:
        draft = get_llm_draft(account_id)

    recommendation = draft.get("recommended_next_action")

    if not recommendation:
        recommendation = (
            "Verify the customer's current situation and review "
            "the linked evidence."
        )

    # Confidence-based due dates.
    days_until_due = 1 if confidence == "Low" else 2
    due_date = (
        datetime.now(timezone.utc).date()
        + timedelta(days=days_until_due)
    ).isoformat()

    if route == "gather_information":
        task_title = f"Verify account information: {company}"
        task_description = (
            "Gather missing or stale customer information before "
            "taking further action. " + route_reason
        )
    elif route.startswith("urgent"):
        task_title = f"Priority customer check-in: {company}"
        task_description = recommendation
    elif route == "expansion_followup":
        task_title = f"Review expansion opportunity: {company}"
        task_description = recommendation
    else:
        task_title = f"Customer review: {company}"
        task_description = recommendation

    proposals = []

    # Task proposal
    if "task" in allowed_set:
        proposals.append((
            "task",
            {
                "title": task_title,
                "description": task_description,
                "owner": owner,
                "due_date": due_date,
            },
            "Follow-up task recommended by the current customer route.",
        ))

    # Email proposal
    if "email" in allowed_set:
        email_body = draft.get("email_body", "")

        if not email_body:
            email_body = (
                f"Hello {contact_name or 'there'},\n\n"
                "Could we arrange a short conversation to understand "
                "your current needs and how we can help?\n\n"
                "Best regards"
            )

        # Use the selected customer's contact email where available.
        proposals.append((
            "email",
            {
                "to": contact_email,
                "contact_name": contact_name,
                "subject": draft.get(
                    "email_subject",
                    f"Checking in with {company}",
                ),
                "body": email_body,
                "draft_only": True,
            },
            "Review and verify the recipient and content before sending.",
        ))

    # Proposed CRM update, not a real external change
    if "crm_update" in allowed_set:
        proposals.append((
            "crm_update",
            {
                "proposed_fields": {
                    "health_review": risk_level,
                    "risk_index": risk_index,
                    "next_step": recommendation,
                    "next_step_date": due_date,
                }
            },
            "Proposed CRM changes require human approval and a CRM connector.",
        ))

    # Local notification proposal
    if "notify" in allowed_set:
        proposals.append((
            "notify",
            {
                "owner": owner,
                "message": (
                    f"{company} is classified {risk_level} "
                    f"(risk index {risk_index}, confidence {confidence}). "
                    "Review the supporting evidence and suggested actions."
                ),
            },
            "Notify the assigned owner about the priority account.",
        ))

    # Enforce action routing. Never create types outside allowed_types.
    proposals = [
        item for item in proposals
        if item[0] in allowed_set
    ]

    new_actions = []

    for action_type, payload, rationale in proposals:
        action = make_action(
            account_id=account_id,
            company=company,
            action_type=action_type,
            payload=payload,
            route=route,
            route_reason=route_reason,
            rationale=rationale,
            evidence_ids=evidence_ids,
        )

        new_actions.append(action)

    actions.extend(new_actions)
    write_json(ACTIONS_FILE, actions)

    print(f"\nProposed actions for {company}")
    print(f"Route: {route}")
    print(f"Risk: {risk_level} | Index: {risk_index}")
    print(f"Evidence confidence: {confidence}")

    for action in new_actions:
        print(
            f"{action['action_id']} | {action['type']} | "
            f"status={action['status']}"
        )

    print(f"\nSaved proposals: {ACTIONS_FILE}")
    print("No email was sent and no external CRM was changed.")

    return new_actions


# ---------------------------------------------------------------------
# Local demo execution
# ---------------------------------------------------------------------

def execute_local_action(action):
    """Execute safe local demo actions only after approval."""
    action_type = action["type"]
    payload = action["payload"]

    if action_type == "task":
        tasks = read_json(TASKS_FILE)

        # Guard against duplicate local task creation.
        if any(
            task.get("created_from_action_id") == action["action_id"]
            for task in tasks
        ):
            return "local_task_already_created"

        task = {
            "task_id": f"TASK-{uuid.uuid4().hex[:8].upper()}",
            "account_id": action["account_id"],
            "owner": payload.get("owner", "unassigned"),
            "title": payload.get("title", "Customer follow-up"),
            "description": payload.get("description", ""),
            "due_date": payload.get("due_date"),
            "status": "open",
            "created_from_action_id": action["action_id"],
            "created_at": now_iso(),
        }

        tasks.append(task)
        write_json(TASKS_FILE, tasks)
        return "local_task_created"

    if action_type == "notify":
        notifications = read_json(NOTIFICATIONS_FILE)

        notifications.append({
            "notification_id": f"NOT-{uuid.uuid4().hex[:8].upper()}",
            "account_id": action["account_id"],
            "owner": payload.get("owner", "unassigned"),
            "message": payload.get("message", ""),
            "read": False,
            "created_at": now_iso(),
            "mode": "local_demo",
        })

        write_json(NOTIFICATIONS_FILE, notifications)
        return "local_notification_created"

    if action_type == "email":
        # The email remains a draft until an email connector is implemented.
        return "awaiting_email_connector"

    if action_type == "crm_update":
        # No external CRM connector is configured yet.
        return "awaiting_crm_connector"

    return "not_executed"


# ---------------------------------------------------------------------
# Human review and audit log
# ---------------------------------------------------------------------

def review_action(action_id, decision, reason, edited_payload=None,
                  user_id="demo-salesperson"):
    """Approve, edit, or reject a proposed action with a reason."""
    actions = read_json(ACTIONS_FILE)

    action = next(
        (
            item for item in actions
            if item.get("action_id") == action_id
        ),
        None,
    )

    if action is None:
        raise ValueError(f"Action not found: {action_id}")

    if action.get("status") != "pending":
        raise ValueError(
            f"Action is not pending: {action.get('status')}"
        )

    decision = decision.strip().lower()

    if decision not in {"approve", "edit", "reject"}:
        raise ValueError("Decision must be approve, edit, or reject.")

    if not reason or not reason.strip():
        raise ValueError("A decision reason is required.")

    original_payload = json.loads(
        json.dumps(action["payload"], ensure_ascii=False)
    )

    final_payload = json.loads(
        json.dumps(original_payload, ensure_ascii=False)
    )

    if decision == "edit":
        if not isinstance(edited_payload, dict):
            raise ValueError("An edited payload JSON object is required.")

        final_payload = edited_payload
        action["payload"] = final_payload

        # Editing does not approve or execute the action.
        action["status"] = "pending"
        action["last_review_decision"] = "edited"
        execution_status = "awaiting_approval"

        audit_decision = "edited_pending_approval"

    elif decision == "approve":
        action["status"] = "approved"
        action["approved_at"] = now_iso()
        execution_status = execute_local_action(action)
        audit_decision = "approved"

    else:
        action["status"] = "rejected"
        execution_status = "not_executed"
        audit_decision = "rejected"

    action["execution_status"] = execution_status
    action["reviewed_at"] = now_iso()
    action["reviewed_by"] = user_id
    action["decision_reason"] = reason.strip()

    write_json(ACTIONS_FILE, actions)

    append_audit({
        "action_id": action["action_id"],
        "account_id": action["account_id"],
        "company_name": action["company_name"],
        "user_id": user_id,
        "decision": audit_decision,
        "original_payload": original_payload,
        "final_payload": final_payload,
        "reason": reason.strip(),
        "route": action.get("route"),
        "evidence_snapshot": action.get("evidence_signal_ids", []),
        "decided_at": action["reviewed_at"],
        "execution_status": execution_status,
    })

    print(f"\nAction: {action['action_id']}")
    print(f"Review decision: {audit_decision}")
    print(f"Execution status: {execution_status}")
    print(f"Updated actions: {ACTIONS_FILE}")
    print(f"Audit log: {AUDIT_FILE}")

    if decision == "edit":
        print("Edited action remains pending approval.")
    elif action["type"] == "email":
        print("Email is NOT sent. An email connector is still required.")
    elif action["type"] == "crm_update":
        print("Real CRM is NOT updated. A CRM connector is still required.")

    return action


def list_actions(account_id=None):
    """List proposed actions, optionally for one customer."""
    actions = read_json(ACTIONS_FILE)

    if account_id:
        actions = [
            action for action in actions
            if str(action.get("account_id")) == str(account_id)
        ]

    if not actions:
        print("No actions found.")
        return

    for action in actions:
        print(
            f"{action.get('action_id')} | "
            f"{action.get('account_id')} | "
            f"{action.get('type')} | "
            f"{action.get('status')} | "
            f"{action.get('execution_status')}"
        )


# ---------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(
            "Commands:\n"
            "  python ai/agent_workflow.py propose ACC-001\n"
            "  python ai/agent_workflow.py list\n"
            "  python ai/agent_workflow.py list ACC-001\n"
            "  python ai/agent_workflow.py review ACTION_ID"
        )
        return

    command = sys.argv[1].strip().lower()

    try:
        if command == "propose":
            if len(sys.argv) < 3:
                raise ValueError("Provide an account ID.")
            propose_actions(sys.argv[2])

        elif command == "list":
            account_id = sys.argv[2] if len(sys.argv) > 2 else None
            list_actions(account_id)

        elif command == "review":
            if len(sys.argv) < 3:
                raise ValueError("Provide an action ID.")

            action_id = sys.argv[2]
            actions = read_json(ACTIONS_FILE)

            action = next(
                (
                    item for item in actions
                    if item.get("action_id") == action_id
                ),
                None,
            )

            if action is None:
                raise ValueError(f"Action not found: {action_id}")

            print("\nAction details:")
            print(json.dumps(action, indent=2, ensure_ascii=False))

            print("\nChoose: [a]pprove, [e]dit, [r]eject")
            choice = input("Decision: ").strip().lower()

            choices = {
                "a": "approve",
                "approve": "approve",
                "e": "edit",
                "edit": "edit",
                "r": "reject",
                "reject": "reject",
            }

            if choice not in choices:
                raise ValueError("Invalid decision.")

            decision = choices[choice]
            reason = input("Reason for decision (required): ").strip()

            edited_payload = None

            if decision == "edit":
                print("Paste the complete edited payload as one-line JSON:")
                edited_payload = json.loads(
                    input("Edited payload JSON: ").strip()
                )

            review_action(
                action_id=action_id,
                decision=decision,
                reason=reason,
                edited_payload=edited_payload,
            )

        else:
            raise ValueError(f"Unknown command: {command}")

    except (
        FileNotFoundError,
        ValueError,
        KeyError,
        RuntimeError,
        json.JSONDecodeError,
    ) as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
