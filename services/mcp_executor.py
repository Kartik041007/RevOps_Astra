"""MCP Action Executor Adapter for RevOps_Astra.

Connects the Action Queue review and approval workflow to the Model Context
Protocol (MCP) server, executing approved customer actions and verifying
CRM updates through the MCP client.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from mcp_server.client import RevOpsMCPClient
from services.approval_actions import ACTION_TYPE, ActionConflict, ActionNotFound

logger = logging.getLogger(__name__)

SUPPORTED_ACTION_TYPES = {ACTION_TYPE, "customer_crm_update"}
ALLOWED_CRM_FIELDS = {"owner_email", "status", "notes"}
UUID_REGEX = re.compile(r"^[0-9a-fA-F-]{36}$")
ACCOUNT_ID_REGEX = re.compile(r"^ACC-\d{3,}$")


class MCPActionExecutor:
    """Adapter that executes approved actions via MCP tools and verifies results."""

    def __init__(
        self,
        mcp_client: RevOpsMCPClient | None = None,
        store: Any | None = None,
        timeout_seconds: float = 10.0,
    ):
        self.mcp_client = mcp_client or RevOpsMCPClient()
        self.store = store
        self.timeout_seconds = timeout_seconds

    def __call__(self, action: dict[str, Any]) -> dict[str, Any]:
        """Callable interface matching the Flask ACTION_EXECUTOR config."""
        return self.execute(action)

    def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        """Execute an approved action against the MCP server and verify results."""
        if not isinstance(action, dict):
            raise ValueError("Action must be a valid dictionary.")

        action_id = str(action.get("action_id", "")).strip()
        if not UUID_REGEX.match(action_id):
            raise ValueError(f"Invalid or missing action_id: '{action_id}'.")

        account_id = str(action.get("account_id", "")).strip()
        if not ACCOUNT_ID_REGEX.match(account_id):
            raise ValueError(f"Invalid or missing account_id: '{account_id}'.")

        # Status validation: only APPROVED or EXECUTING (claimed from APPROVED) may execute
        status = action.get("status")
        if status in ("PENDING", "REJECTED"):
            raise ActionConflict(
                f"Action {action_id} cannot be executed from status '{status}'. Only APPROVED actions may execute."
            )
        if status in ("COMPLETED", "FAILED", "EXECUTED"):
            raise ActionConflict(
                f"Action {action_id} was already executed (status: '{status}'). Duplicate execution is blocked."
            )
        if status not in ("APPROVED", "EXECUTING"):
            raise ActionConflict(
                f"Action {action_id} is in invalid status '{status}' for execution."
            )

        # Action type validation
        action_type = action.get("action_type")
        if action_type not in SUPPORTED_ACTION_TYPES:
            raise ValueError(
                f"Unsupported action type '{action_type}'. Supported types: {sorted(SUPPORTED_ACTION_TYPES)}."
            )

        # Cross-check persisted action in store if available
        if self.store is not None:
            persisted = self.store.get_action(action_id)
            if persisted is None:
                raise ActionNotFound(action_id)
            if persisted.get("status") not in ("APPROVED", "EXECUTING"):
                raise ActionConflict(
                    f"Persisted action status '{persisted.get('status')}' is not eligible for execution."
                )
            if persisted.get("account_id") != account_id:
                raise ValueError("Supplied account_id does not match the persisted action record.")

        # Extract and validate CRM field changes
        payload = action.get("proposed_payload")
        if not isinstance(payload, dict):
            raise ValueError("Proposed payload must be a non-empty dictionary.")

        crm_updates = self._extract_and_validate_crm_updates(payload)

        # Execute over MCP with timeout and read-back verification
        try:
            return asyncio.run(
                self._async_execute_and_verify(
                    action_id=action_id,
                    account_id=account_id,
                    crm_updates=crm_updates,
                )
            )
        except TimeoutError as exc:
            logger.error("MCP execution timed out for action %s: %s", action_id, exc)
            raise
        except Exception as exc:
            logger.error("MCP execution failed for action %s: %s", action_id, exc)
            raise

    def _extract_and_validate_crm_updates(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Extract and validate CRM field updates with strict allowlisting."""
        if "crm_field_changes" in payload:
            field_changes = payload["crm_field_changes"]
            if not isinstance(field_changes, dict) or not field_changes:
                raise ValueError("crm_field_changes must be a non-empty dictionary.")

            unauthorized = set(field_changes.keys()) - ALLOWED_CRM_FIELDS
            if unauthorized:
                raise ValueError(
                    f"Unauthorized CRM fields detected: {sorted(unauthorized)}. "
                    f"Only the following fields are allowlisted: {sorted(ALLOWED_CRM_FIELDS)}."
                )
            return dict(field_changes)

        # For customer outreach email actions without explicit CRM field changes,
        # derive an audit note update to record the executed outreach in the CRM record.
        proposed_action = payload.get("proposed_action", "Customer outreach")
        email = payload.get("email") or {}
        subject = email.get("subject", "Executive review")
        note_text = f"Outreach executed: {proposed_action} (Subject: {subject})"[:1000]

        return {"notes": note_text}

    async def _async_execute_and_verify(
        self,
        action_id: str,
        account_id: str,
        crm_updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute CRM update via MCP and verify via read-back with timeout."""

        async def _execute_flow() -> dict[str, Any]:
            # Step 1: Call update_customer_record MCP tool with approved action_id
            update_res = await self.mcp_client.update_customer(
                account_id=account_id,
                field_updates=crm_updates,
                action_id=action_id,
            )

            if not isinstance(update_res, dict):
                raise RuntimeError(
                    f"Invalid MCP update response format: expected dict, got {type(update_res).__name__}."
                )

            if not update_res.get("success"):
                error_msg = update_res.get("error", "Unknown MCP tool failure")
                raise RuntimeError(f"MCP update_customer_record failed: {error_msg}")

            if update_res.get("status") != "EXECUTED":
                status_msg = update_res.get("message", f"Unexpected status: {update_res.get('status')}")
                raise RuntimeError(f"MCP update was not executed: {status_msg}")

            # Step 2: Read-back verification via get_customer_record MCP tool
            readback_res = await self.mcp_client.get_customer(account_id)
            if not isinstance(readback_res, dict) or not readback_res.get("success"):
                err = readback_res.get("error") if isinstance(readback_res, dict) else "Unknown read error"
                raise RuntimeError(f"Verification read-back failed: {err}")

            verified_customer = readback_res.get("customer")
            if not isinstance(verified_customer, dict):
                raise RuntimeError("Verification read-back did not return a valid customer dictionary.")

            # Step 3: Verify that every applied field matches in the read-back record
            for field, expected_val in crm_updates.items():
                actual_val = verified_customer.get(field)
                if actual_val != expected_val:
                    raise RuntimeError(
                        f"CRM update verification failed for field '{field}': "
                        f"expected '{expected_val}', found '{actual_val}'."
                    )

            integration_name = update_res.get("integration", "mcp_mock_crm")
            mode_name = update_res.get("mode", "mock")

            # Step 4: Construct verified execution receipt
            return {
                "verified": True,
                "status": "COMPLETED",
                "action_id": action_id,
                "account_id": account_id,
                "applied_updates": crm_updates,
                "integration": integration_name,
                "mode": mode_name,
                "verified_record": {
                    "account_id": verified_customer.get("account_id"),
                    "company_name": verified_customer.get("company_name"),
                    "customer_status": verified_customer.get("customer_status"),
                    "owner_email": verified_customer.get("owner_email"),
                    "notes": verified_customer.get("notes"),
                },
                "message": (
                    f"Approved action {action_id} successfully executed and verified "
                    f"against {mode_name} CRM via MCP for customer {account_id}."
                ),
            }

        try:
            return await asyncio.wait_for(_execute_flow(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"MCP execution timed out after {self.timeout_seconds} seconds for action {action_id}."
            )


def get_default_mcp_executor(store: Any | None = None) -> MCPActionExecutor:
    """Factory creating an MCPActionExecutor instance."""
    return MCPActionExecutor(store=store)
