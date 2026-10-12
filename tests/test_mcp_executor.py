import asyncio
import os
import re
import unittest
from unittest import mock

from app import create_app
from mcp_server.client import RevOpsMCPClient
from mcp_server.mock_crm_db import mock_crm_db
from services.approval_actions import ACTION_TYPE, ActionConflict
from services.mcp_executor import MCPActionExecutor
from tests.test_approval_actions import MemoryActionStore


class MCPActionExecutorTests(unittest.TestCase):
    """Test suite for MCPActionExecutor integration with Action Queue."""

    def setUp(self):
        self.old_crm_provider = os.environ.get("CRM_PROVIDER")
        os.environ["CRM_PROVIDER"] = "mock"
        self.addCleanup(self._restore_crm_provider)

        mock_crm_db.reset_mock_updates()
        self.addCleanup(mock_crm_db.reset_mock_updates)
        self.store = MemoryActionStore()
        self.executor = MCPActionExecutor(
            mcp_client=RevOpsMCPClient(),
            store=self.store,
            timeout_seconds=5.0,
        )

        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-mcp-executor-secret",
            APPROVAL_ACTION_STORE=self.store,
            ACTION_EXECUTOR=self.executor,
        )
        self.client = self.app.test_client()

        page = self.client.get("/ai-agent")
        token = re.search(
            r'<meta name="csrf-token" content="([^"]+)"', page.get_data(as_text=True)
        ).group(1)
        self.headers = {"X-CSRFToken": token}

        with self.client.session_transaction() as session:
            session["admin_id"] = 1
            session["admin_name"] = "AdminReviewer"
            session["role"] = "admin"

        self.patchers = [
            mock.patch(
                "routes.approval_actions.read_output_csv",
                side_effect=self.csv_rows,
            )
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _restore_crm_provider(self):
        if self.old_crm_provider is not None:
            os.environ["CRM_PROVIDER"] = self.old_crm_provider
        else:
            os.environ.pop("CRM_PROVIDER", None)

    @staticmethod
    def csv_rows(filename):
        if filename == "account_risk_insights.csv":
            return [{
                "account_id": "ACC-001",
                "company_name": "Northstar Components",
                "risk_level": "Urgent",
            }]
        if filename == "account_evidence.csv":
            return [{
                "account_id": "ACC-001",
                "source_record_id": "SRC-001-A",
            }]
        return []

    def sample_proposal(self):
        return {
            "account_id": "ACC-001",
            "action_type": ACTION_TYPE,
            "proposed_payload": {
                "proposed_action": "Schedule executive review",
                "explanation": "Usage dropped 18% over the past 30 days.",
                "email": {
                    "subject": "Executive Review Meeting",
                    "body": "Dear Partner,\nWe would like to review recent usage patterns.",
                },
            },
            "evidence_refs": ["SRC-001-A"],
        }

    # 1. Approved action executes through the MCP adapter
    def test_1_approved_action_executes_through_mcp_adapter(self):
        res = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers)
        action_id = res.get_json()["action"]["action_id"]

        # Approve action
        app_res = self.client.post(f"/api/actions/{action_id}/approve", json={}, headers=self.headers)
        self.assertEqual(app_res.status_code, 200)
        self.assertEqual(app_res.get_json()["action"]["status"], "APPROVED")

        # Execute approved action
        exec_res = self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": self.sample_proposal()["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(exec_res.status_code, 200)
        data = exec_res.get_json()
        self.assertEqual(data["action"]["status"], "COMPLETED")
        self.assertEqual(data["message"], "Execution verified.")

        # Verify execution result payload from MCP
        exec_result = data["action"]["execution_result"]
        self.assertTrue(exec_result["verified"])
        self.assertEqual(exec_result["status"], "COMPLETED")
        self.assertEqual(exec_result["integration"], "mcp_mock_crm")
        self.assertEqual(exec_result["mode"], "mock")
        self.assertIn("notes", exec_result["applied_updates"])

        # Verify fresh MCP client retrieves updated record across fresh subprocess session
        fresh_client = RevOpsMCPClient()
        fresh_res = asyncio.run(fresh_client.get_customer("ACC-001"))
        self.assertTrue(fresh_res.get("success"))
        fresh_record = fresh_res.get("customer", {})
        self.assertEqual(fresh_record.get("notes"), exec_result["applied_updates"]["notes"])

        # Verify customer detail endpoint (/api/customers/ACC-001) reflects updated notes
        cust_res = self.client.get("/api/customers/ACC-001")
        self.assertEqual(cust_res.status_code, 200)
        cust_json = cust_res.get_json()
        self.assertTrue(cust_json.get("mock_crm_synced"))
        self.assertEqual(cust_json.get("notes"), exec_result["applied_updates"]["notes"])
        self.assertEqual(cust_json.get("crm_record", {}).get("notes"), exec_result["applied_updates"]["notes"])

        # Verify customer brief endpoint (/api/ai-agent/brief) reflects updated notes
        brief_res = self.client.post("/api/ai-agent/brief", json={"account_id": "ACC-001"}, headers=self.headers)
        self.assertEqual(brief_res.status_code, 200)
        brief_json = brief_res.get_json()["brief"]
        self.assertTrue(brief_json.get("crm_updated"))
        self.assertEqual(brief_json.get("customer", {}).get("notes"), exec_result["applied_updates"]["notes"])
        self.assertEqual(brief_json.get("crm_record", {}).get("notes"), exec_result["applied_updates"]["notes"])

    # 2. Pending and rejected actions are blocked
    def test_2_pending_and_rejected_actions_blocked(self):
        # Create proposal (status = PENDING)
        res = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers)
        action_id = res.get_json()["action"]["action_id"]

        # Attempt to execute while PENDING -> 409 Conflict
        pending_res = self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": self.sample_proposal()["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(pending_res.status_code, 409)

        # Direct executor call with pending status also raises ActionConflict
        pending_action = self.store.get_action(action_id)
        with self.assertRaises(ActionConflict):
            self.executor(pending_action)

        # Reject action
        rej_res = self.client.post(
            f"/api/actions/{action_id}/reject",
            json={"reason": "Customer outreach not appropriate."},
            headers=self.headers,
        )
        self.assertEqual(rej_res.status_code, 200)

        # Attempt to execute while REJECTED -> 409 Conflict
        rejected_res = self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": self.sample_proposal()["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(rejected_res.status_code, 409)

        # Direct executor call with rejected status also raises ActionConflict
        rejected_action = self.store.get_action(action_id)
        with self.assertRaises(ActionConflict):
            self.executor(rejected_action)

    # 3. Invalid fields and altered payloads are rejected
    def test_3_invalid_fields_and_altered_payloads_rejected(self):
        res = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers)
        action_id = res.get_json()["action"]["action_id"]
        self.client.post(f"/api/actions/{action_id}/approve", json={}, headers=self.headers)

        # Altered payload (different subject)
        altered_payload = dict(self.sample_proposal()["proposed_payload"])
        altered_payload["email"] = {"subject": "Altered Subject", "body": "Altered Body"}

        altered_res = self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": altered_payload},
            headers=self.headers,
        )
        self.assertEqual(altered_res.status_code, 400)

        # Direct executor call with unallowlisted CRM fields
        bad_action = dict(self.store.get_action(action_id))
        bad_action["proposed_payload"] = {
            "proposed_action": "Test",
            "crm_field_changes": {"unauthorized_field": "123"},
        }
        with self.assertRaises(ValueError) as ctx:
            self.executor(bad_action)
        self.assertIn("Unauthorized CRM fields", str(ctx.exception))

    # 4. Duplicate execution is prevented
    def test_4_duplicate_execution_is_prevented(self):
        res = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers)
        action_id = res.get_json()["action"]["action_id"]
        self.client.post(f"/api/actions/{action_id}/approve", json={}, headers=self.headers)

        # 1st execution succeeds
        res1 = self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": self.sample_proposal()["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(res1.status_code, 200)

        # 2nd execution attempt returns 409 Conflict
        res2 = self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": self.sample_proposal()["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(res2.status_code, 409)

        # Direct executor call with already completed action raises ActionConflict
        completed_action = self.store.get_action(action_id)
        with self.assertRaises(ActionConflict):
            self.executor(completed_action)

    # 5. MCP connection and tool failures are recorded safely
    def test_5_mcp_connection_and_tool_failures_recorded_safely(self):
        res = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers)
        action_id = res.get_json()["action"]["action_id"]
        self.client.post(f"/api/actions/{action_id}/approve", json={}, headers=self.headers)

        # Mock MCP tool failing
        with mock.patch.object(self.executor.mcp_client, "update_customer") as mock_update:
            mock_update.return_value = {
                "success": False,
                "error": "CRM connection reset by peer",
            }

            exec_res = self.client.post(
                f"/api/actions/{action_id}/execute",
                json={"payload": self.sample_proposal()["proposed_payload"]},
                headers=self.headers,
            )
            self.assertEqual(exec_res.status_code, 502)
            self.assertEqual(exec_res.get_json()["error"], "Execution failed and was recorded.")

            # Stored status should be FAILED
            stored = self.store.get_action(action_id)
            self.assertEqual(stored["status"], "FAILED")
            self.assertIn("error", stored["execution_result"])

    # 6. Successful updates are verified by reading the record again
    def test_6_successful_updates_verified_by_reading_record_again(self):
        res = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers)
        action_id = res.get_json()["action"]["action_id"]
        self.client.post(f"/api/actions/{action_id}/approve", json={}, headers=self.headers)

        exec_res = self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": self.sample_proposal()["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(exec_res.status_code, 200)

        # Check mock CRM database directly to ensure record was actually updated and read back
        updated_customer = mock_crm_db.get_customer("ACC-001")
        self.assertIsNotNone(updated_customer)
        self.assertIn("Executive Review Meeting", updated_customer["notes"])

    # 7. Failed verification does not produce a false COMPLETED status
    def test_7_failed_verification_does_not_produce_false_completed_status(self):
        res = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers)
        action_id = res.get_json()["action"]["action_id"]
        self.client.post(f"/api/actions/{action_id}/approve", json={}, headers=self.headers)

        # Mock readback returning a mismatched record
        with mock.patch.object(self.executor.mcp_client, "update_customer") as mock_update, \
             mock.patch.object(self.executor.mcp_client, "get_customer") as mock_get:
            mock_update.return_value = {
                "success": True,
                "status": "EXECUTED",
                "action_id": action_id,
            }
            # Readback returns old/mismatched notes
            mock_get.return_value = {
                "success": True,
                "customer": {"account_id": "ACC-001", "notes": "MISMATCHED_OLD_NOTE"},
            }

            exec_res = self.client.post(
                f"/api/actions/{action_id}/execute",
                json={"payload": self.sample_proposal()["proposed_payload"]},
                headers=self.headers,
            )
            # Must NOT produce a 200 OK or COMPLETED status
            self.assertEqual(exec_res.status_code, 502)
            stored = self.store.get_action(action_id)
            self.assertEqual(stored["status"], "FAILED")
            self.assertNotEqual(stored["status"], "COMPLETED")

    # 8. Audit records are created
    def test_8_audit_records_are_created(self):
        res = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers)
        action_id = res.get_json()["action"]["action_id"]
        self.client.post(f"/api/actions/{action_id}/approve", json={}, headers=self.headers)

        self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": self.sample_proposal()["proposed_payload"]},
            headers=self.headers,
        )

        audit_events = self.store.get_audit_events(action_id)
        event_types = [e["event_type"] for e in audit_events]
        self.assertEqual(event_types, ["PROPOSED", "APPROVED", "EXECUTION_STARTED", "COMPLETED"])

    # 9. MCP timeout is handled safely
    def test_9_timeout_is_handled_safely(self):
        fast_executor = MCPActionExecutor(
            mcp_client=RevOpsMCPClient(),
            store=self.store,
            timeout_seconds=0.001,  # 1 ms to trigger timeout
        )
        self.app.config["ACTION_EXECUTOR"] = fast_executor

        res = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers)
        action_id = res.get_json()["action"]["action_id"]
        self.client.post(f"/api/actions/{action_id}/approve", json={}, headers=self.headers)

        exec_res = self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": self.sample_proposal()["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(exec_res.status_code, 502)
        stored = self.store.get_action(action_id)
        self.assertEqual(stored["status"], "FAILED")
        self.assertEqual(stored["execution_result"]["error_type"], "TimeoutError")


if __name__ == "__main__":
    unittest.main()
