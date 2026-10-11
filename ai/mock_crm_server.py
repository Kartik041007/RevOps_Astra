"""ASTRA AI - Local Mock CRM MCP Server."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from mcp.server.fastmcp import FastMCP


# --------------------------------------------------
# Project paths
# --------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = ROOT / "outputs"

SOURCE_FILE = OUTPUTS / "customer_priority_list.csv"
CRM_FILE = OUTPUTS / "mock_crm.json"
ACTIONS_FILE = OUTPUTS / "proposed_actions.json"
AUDIT_FILE = OUTPUTS / "audit_log.jsonl"


# Only these CRM fields may be updated.
ALLOWED_FIELDS = {
    "health_review",
    "risk_index",
    "next_step",
    "next_step_date",
}

ALLOWED_HEALTH_REVIEWS = {
    "Urgent",
    "Needs Review",
    "Healthy",
}

mcp = FastMCP("ASTRA Local Mock CRM")


# --------------------------------------------------
# Utility functions
# --------------------------------------------------

def now_iso() -> str:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default=None):
    """Read JSON safely when the file exists."""
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data) -> None:
    """Write JSON using a temporary file to reduce partial writes."""
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)

    temp_path.replace(path)


def append_audit(event: dict) -> None:
    """Append an event to the local audit log."""
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with AUDIT_FILE.open("a", encoding="utf-8") as file:
        file.write(
            json.dumps(event, ensure_ascii=False, default=str) + "\n"
        )


def clean(value):
    """Convert pandas and NumPy values into JSON-safe values."""
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


# --------------------------------------------------
# Local Mock CRM initialization
# --------------------------------------------------

def ensure_crm_store() -> dict:
    """Create the local mock CRM from the customer dataset if needed."""

    if CRM_FILE.exists():
        store = read_json(CRM_FILE)

        if not isinstance(store, dict):
            raise ValueError("mock_crm.json must contain a JSON object.")

        if not isinstance(store.get("records"), dict):
            raise ValueError(
                "mock_crm.json must contain a records object."
            )

        return store

    if not SOURCE_FILE.exists():
        raise FileNotFoundError(
            f"Missing {SOURCE_FILE}. "
            "Run the analytics pipeline first."
        )

    df = pd.read_csv(SOURCE_FILE)

    if "account_id" not in df.columns:
        raise ValueError(
            "customer_priority_list.csv must contain account_id."
        )

    records = {}

    for _, row in df.iterrows():
        account_id = str(row["account_id"])

        records[account_id] = {
            "account_id": account_id,
            "company_name": clean(row.get("company_name")),
            "contact_name": clean(row.get("contact_name")),
            "contact_email": clean(row.get("email")),
            "account_owner": clean(row.get("account_owner")),
            "risk_level": clean(row.get("risk_level")),
            "risk_index": clean(row.get("risk_index")),
            "health_review": "Not reviewed",
            "next_step": None,
            "next_step_date": None,
            "updated_at": None,
        }

    store = {
        "mode": "LOCAL_MOCK_CRM",
        "warning": (
            "Local demo only; this is not connected to HubSpot "
            "or another external CRM."
        ),
        "records": records,
    }

    write_json(CRM_FILE, store)
    return store


# --------------------------------------------------
# MCP TOOL 1: Read one customer
# --------------------------------------------------

@mcp.tool()
def get_customer_record(account_id: str) -> dict:
    """Read one customer record from the local mock CRM."""

    if not account_id or not isinstance(account_id, str):
        return {
            "success": False,
            "error": "A valid account_id is required.",
        }

    store = ensure_crm_store()
    record = store["records"].get(account_id)

    if record is None:
        return {
            "success": False,
            "error": f"Account not found: {account_id}",
        }

    return {
        "success": True,
        "mode": "LOCAL_MOCK_CRM",
        "record": record,
    }


# --------------------------------------------------
# MCP TOOL 2: List customer records
# --------------------------------------------------

@mcp.tool()
def list_customer_records(limit: int = 10) -> dict:
    """List customer records from the local mock CRM."""

    if isinstance(limit, bool) or not isinstance(limit, int):
        return {
            "success": False,
            "error": "Limit must be an integer between 1 and 50.",
        }

    if not 1 <= limit <= 50:
        return {
            "success": False,
            "error": "Limit must be between 1 and 50.",
        }

    store = ensure_crm_store()
    records = list(store["records"].values())[:limit]

    return {
        "success": True,
        "mode": "LOCAL_MOCK_CRM",
        "count": len(records),
        "records": records,
    }


# --------------------------------------------------
# MCP TOOL 3: Update customer after human approval
# --------------------------------------------------

@mcp.tool()
def update_customer_after_approval(action_id: str) -> dict:
    """
    Update the local mock CRM only when the corresponding
    CRM update action has been approved by a human.
    """

    if not action_id or not isinstance(action_id, str):
        return {
            "success": False,
            "error": "A valid action_id is required.",
        }

    # 1. Load the proposed actions.
    actions = read_json(ACTIONS_FILE, [])

    if not isinstance(actions, list):
        return {
            "success": False,
            "error": "proposed_actions.json must contain a list.",
        }

    # 2. Find the requested action.
    action = next(
        (
            item
            for item in actions
            if isinstance(item, dict)
            and item.get("action_id") == action_id
        ),
        None,
    )

    if action is None:
        return {
            "success": False,
            "error": f"Action not found: {action_id}",
        }

    # 3. Only CRM updates may use this tool.
    if action.get("type") != "crm_update":
        return {
            "success": False,
            "error": "This action is not a CRM update.",
        }

    # 4. Explicit human approval is mandatory.
    if str(action.get("status", "")).lower() != "approved":
        return {
            "success": False,
            "error": "Human approval required before CRM update.",
        }

    # 5. Prevent accidentally executing the same action twice.
    if action.get("execution_status") == "mock_crm_updated":
        return {
            "success": True,
            "status": "already_updated",
            "mode": "LOCAL_MOCK_CRM",
            "action_id": action_id,
        }

    if action.get("execution_status") != "awaiting_crm_connector":
        return {
            "success": False,
            "error": (
                "Action is not ready for execution. "
                "Expected execution_status='awaiting_crm_connector'."
            ),
        }

    # 6. Read proposed fields from supported payload formats.
    payload = action.get("payload", {})

    if not isinstance(payload, dict):
        return {
            "success": False,
            "error": "Action payload must be an object.",
        }

    fields = payload.get("proposed_fields")

    if fields is None:
        fields = payload.get("fields")

    if fields is None:
        # Also support payloads where the CRM fields are
        # provided directly inside the payload.
        fields = {
            key: payload[key]
            for key in ALLOWED_FIELDS
            if key in payload
        }

    if not fields and isinstance(action.get("proposed_fields"), dict):
        fields = action["proposed_fields"]

    if not isinstance(fields, dict) or not fields:
        return {
            "success": False,
            "error": (
                "No proposed CRM fields found. "
                "Expected payload.proposed_fields or payload.fields."
            ),
        }

    # 7. Validate the field names using an allowlist.
    invalid_fields = set(fields.keys()) - ALLOWED_FIELDS

    if invalid_fields:
        return {
            "success": False,
            "error": (
                "Fields not allowed: "
                f"{sorted(invalid_fields)}"
            ),
        }

    # Work on a copy so validation does not mutate the source action.
    fields = dict(fields)

    # 8. Validate risk score.
    if "risk_index" in fields:
        raw_score = fields["risk_index"]

        if isinstance(raw_score, bool):
            return {
                "success": False,
                "error": "Risk index must be numeric.",
            }

        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            return {
                "success": False,
                "error": "Risk index must be numeric.",
            }

        if not 0 <= score <= 100:
            return {
                "success": False,
                "error": "Risk index must be between 0 and 100.",
            }

        fields["risk_index"] = score

    # 9. Validate risk category.
    if "health_review" in fields:
        if fields["health_review"] not in ALLOWED_HEALTH_REVIEWS:
            return {
                "success": False,
                "error": (
                    "Invalid health_review. Use Urgent, "
                    "Needs Review, or Healthy."
                ),
            }

    # 10. Validate the next-step description.
    if "next_step" in fields:
        next_step = fields["next_step"]

        if next_step is not None and not isinstance(next_step, str):
            return {
                "success": False,
                "error": "next_step must be text or null.",
            }

    # 11. Validate the follow-up date.
    if "next_step_date" in fields:
        next_date = fields["next_step_date"]

        if next_date is not None:
            if not isinstance(next_date, str):
                return {
                    "success": False,
                    "error": "Date must use YYYY-MM-DD.",
                }

            try:
                parsed_date = datetime.strptime(
                    next_date, "%Y-%m-%d"
                )
                if parsed_date.strftime("%Y-%m-%d") != next_date:
                    raise ValueError("Invalid date format.")
            except ValueError:
                return {
                    "success": False,
                    "error": "Date must use YYYY-MM-DD.",
                }

    # 12. Load the local CRM and find the target account.
    store = ensure_crm_store()
    account_id = str(action.get("account_id", ""))

    if not account_id:
        return {
            "success": False,
            "error": "The approved action has no account_id.",
        }

    record = store["records"].get(account_id)

    if record is None:
        return {
            "success": False,
            "error": f"Account not found in mock CRM: {account_id}",
        }

    # 13. Save previous values for the audit trail.
    previous_values = {
        key: record.get(key)
        for key in fields
    }

    # 14. Update only approved, validated fields.
    record.update(fields)
    record["updated_at"] = now_iso()

    write_json(CRM_FILE, store)

    # 15. Mark the action as executed in the local mock CRM.
    action["execution_status"] = "mock_crm_updated"
    action["crm_tool_result"] = {
        "updated_fields": list(fields.keys()),
        "updated_at": record["updated_at"],
        "mode": "LOCAL_MOCK_CRM",
    }

    write_json(ACTIONS_FILE, actions)

    # 16. Record the execution in the audit log.
    append_audit({
        "action_id": action_id,
        "account_id": account_id,
        "decision": "mock_crm_update_executed",
        "previous_values": previous_values,
        "new_values": fields,
        "execution_status": "mock_crm_updated",
        "decided_at": record["updated_at"],
        "mode": "LOCAL_MOCK_CRM",
    })

    # 17. Return the verified updated record.
    return {
        "success": True,
        "mode": "LOCAL_MOCK_CRM",
        "action_id": action_id,
        "account_id": account_id,
        "updated_fields": fields,
        "verified_record": record,
    }


# --------------------------------------------------
# Start MCP server over standard input/output
# --------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")