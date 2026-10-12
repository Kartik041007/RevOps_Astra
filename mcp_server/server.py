"""RevOps_Astra MCP Server implementation using official Python MCP SDK (MCPServer).

Exposes:
1. get_customer_record: read-only tool to fetch a single customer record by account ID.
2. list_customer_records: read-only tool to list customer records with pagination.
3. update_customer_record: guarded update tool with strict field allowlist and safety policy.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from mcp.server.mcpserver import MCPServer
from mcp_server.mock_crm_db import mock_crm_db

# Initialize MCP Server
server = MCPServer("revops-astra-crm")

ALLOWED_UPDATE_FIELDS = {"owner_email", "status", "notes"}
ALLOWED_STATUSES = {"Active", "At Risk", "Churned", "Paused", "Watchlist"}
ACCOUNT_ID_REGEX = re.compile(r"^ACC-\d{3,}$")
EMAIL_REGEX = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")


def _load_env_file():
    if "unittest" in sys.modules:
        return
    env_file = Path(_project_root) / ".env"
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val


_load_env_file()


def get_crm_provider_mode() -> str:
    """Return active CRM provider mode ('mock' or 'mysql'). Defaults to 'mock'."""
    mode = os.environ.get("CRM_PROVIDER", "mock").strip().lower()
    return "mysql" if mode == "mysql" else "mock"


def _get_mysql_repo():
    from services.customer_repository import CustomerRepository
    return CustomerRepository()


@server.tool(
    name="get_customer_record",
    description="Retrieve a single customer CRM record by account ID (e.g. 'ACC-001').",
)
def get_customer_record(account_id: str) -> dict[str, Any]:
    """Retrieve one customer record from the configured CRM provider."""
    if not isinstance(account_id, str) or not account_id.strip():
        return {"success": False, "error": "account_id must be a non-empty string."}

    cleaned_id = account_id.strip()
    if not ACCOUNT_ID_REGEX.match(cleaned_id):
        return {
            "success": False,
            "error": f"Invalid account_id format '{cleaned_id}'. Expected format: 'ACC-###' (e.g., 'ACC-001').",
        }

    provider = get_crm_provider_mode()
    if provider == "mysql":
        try:
            repo = _get_mysql_repo()
            customer = repo.get_customer_record(cleaned_id)
        except Exception as exc:
            return {
                "success": False,
                "provider": "mysql",
                "error": f"MySQL CRM provider error: {type(exc).__name__}",
            }
    else:
        customer = mock_crm_db.get_customer(cleaned_id)

    if customer is None:
        return {
            "success": False,
            "provider": provider,
            "error": f"Customer with account ID '{cleaned_id}' was not found in CRM.",
        }

    return {
        "success": True,
        "provider": provider,
        "account_id": cleaned_id,
        "customer": customer,
    }


@server.tool(
    name="list_customer_records",
    description="List customer CRM records with optional pagination (limit: 1..50, offset: >= 0).",
)
def list_customer_records(limit: int = 10, offset: int = 0) -> dict[str, Any]:
    """List customer records from the configured CRM provider."""
    if not isinstance(limit, int) or limit < 1 or limit > 50:
        return {"success": False, "error": "limit must be an integer between 1 and 50."}
    if not isinstance(offset, int) or offset < 0:
        return {"success": False, "error": "offset must be a non-negative integer."}

    provider = get_crm_provider_mode()
    if provider == "mysql":
        try:
            repo = _get_mysql_repo()
            customers, total_count = repo.list_customer_records(limit=limit, offset=offset)
        except Exception as exc:
            return {
                "success": False,
                "provider": "mysql",
                "error": f"MySQL CRM provider error: {type(exc).__name__}",
            }
    else:
        customers, total_count = mock_crm_db.list_customers(limit=limit, offset=offset)

    return {
        "success": True,
        "provider": provider,
        "total_records": total_count,
        "returned_count": len(customers),
        "offset": offset,
        "limit": limit,
        "customers": customers,
    }


@server.tool(
    name="update_customer_record",
    description=(
        "Validate proposed CRM field changes for a customer. Direct execution is safely blocked; "
        "modifications must be routed through the RevOps_Astra Action Queue for review and approval."
    ),
)
def update_customer_record(
    account_id: str,
    field_updates: dict[str, Any],
    action_id: str | None = None,
) -> dict[str, Any]:
    """Validate customer field updates with strict allowlisting and approval requirement."""
    if not isinstance(account_id, str) or not account_id.strip():
        return {"success": False, "error": "account_id must be a non-empty string."}

    cleaned_id = account_id.strip()
    if not ACCOUNT_ID_REGEX.match(cleaned_id):
        return {
            "success": False,
            "error": f"Invalid account_id format '{cleaned_id}'. Expected format: 'ACC-###'.",
        }

    if not isinstance(field_updates, dict) or not field_updates:
        return {
            "success": False,
            "error": "field_updates must be a non-empty dictionary of field updates.",
        }

    # Strict allowlist check
    unauthorized_fields = set(field_updates.keys()) - ALLOWED_UPDATE_FIELDS
    if unauthorized_fields:
        return {
            "success": False,
            "error": (
                f"Unauthorized fields detected: {sorted(unauthorized_fields)}. "
                f"Only the following fields may be updated: {sorted(ALLOWED_UPDATE_FIELDS)}."
            ),
        }

    # Validate individual fields
    validated: dict[str, Any] = {}
    if "owner_email" in field_updates:
        email = field_updates["owner_email"]
        if not isinstance(email, str) or not EMAIL_REGEX.match(email.strip()):
            return {
                "success": False,
                "error": f"Invalid owner_email value '{email}'. Must be a valid email address.",
            }
        validated["owner_email"] = email.strip()

    if "status" in field_updates:
        status_val = field_updates["status"]
        if not isinstance(status_val, str) or status_val.strip() not in ALLOWED_STATUSES:
            return {
                "success": False,
                "error": f"Invalid status '{status_val}'. Allowed statuses are: {sorted(ALLOWED_STATUSES)}.",
            }
        validated["status"] = status_val.strip()

    if "notes" in field_updates:
        notes_val = field_updates["notes"]
        if not isinstance(notes_val, str) or len(notes_val) > 1000:
            return {
                "success": False,
                "error": "notes must be a string of at most 1000 characters.",
            }
        validated["notes"] = notes_val.strip()

    provider = get_crm_provider_mode()

    # Verify customer exists in database
    if provider == "mysql":
        try:
            repo = _get_mysql_repo()
            customer = repo.get_customer_record(cleaned_id)
        except Exception as exc:
            return {
                "success": False,
                "provider": "mysql",
                "error": f"MySQL CRM provider error: {type(exc).__name__}",
            }
    else:
        customer = mock_crm_db.get_customer(cleaned_id)

    if customer is None:
        return {
            "success": False,
            "provider": provider,
            "error": f"Customer with account ID '{cleaned_id}' not found in CRM.",
        }

    # If an approved Action Queue action_id is supplied, apply the update
    if action_id is not None and isinstance(action_id, str) and action_id.strip():
        cleaned_action_id = action_id.strip()
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", cleaned_action_id):
            return {
                "success": False,
                "error": f"Invalid action_id format '{cleaned_action_id}'. Expected UUID.",
            }

        if provider == "mysql":
            try:
                repo = _get_mysql_repo()
                updated_record = repo.update_customer_record(
                    account_id=cleaned_id,
                    field_updates=validated,
                    action_id=cleaned_action_id,
                    actor="MCP_SERVER",
                )
                integration_name = "mcp_mysql_crm"
                mode_name = "mysql"
            except Exception as exc:
                return {
                    "success": False,
                    "provider": "mysql",
                    "error": f"Failed to persist update to MySQL: {exc}",
                }
        else:
            updated_record = mock_crm_db.update_customer_in_memory(cleaned_id, validated)
            integration_name = "mcp_mock_crm"
            mode_name = "mock"

        return {
            "success": True,
            "status": "EXECUTED",
            "action_id": cleaned_action_id,
            "account_id": cleaned_id,
            "applied_updates": validated,
            "integration": integration_name,
            "mode": mode_name,
            "provider": provider,
            "record": updated_record,
            "message": f"Successfully applied approved updates to CRM record for {cleaned_id}.",
        }

    # Otherwise, enforce safety policy: direct execution is blocked without Action Queue approval
    return {
        "success": True,
        "status": "BLOCKED_PENDING_APPROVAL",
        "account_id": cleaned_id,
        "validated_updates": validated,
        "requires_approval": True,
        "provider": provider,
        "safety_policy": "NO_DIRECT_EXECUTION",
        "message": (
            "Safety policy enforced: Direct CRM modification via MCP is blocked. "
            "These field changes must be submitted to the RevOps_Astra Action Queue, "
            "reviewed by a designated reviewer, and approved prior to execution."
        ),
    }


def run_server() -> None:
    """Run the MCP server over standard I/O (stdio)."""
    server.run(transport="stdio")


if __name__ == "__main__":
    run_server()
