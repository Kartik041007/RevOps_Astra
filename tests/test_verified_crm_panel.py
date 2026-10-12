import os
import unittest
from unittest import mock

from app import create_app
from tests.test_approval_actions import MemoryActionStore


class VerifiedCRMPanelTests(unittest.TestCase):
    def setUp(self):
        self.old_crm_provider = os.environ.get("CRM_PROVIDER")
        os.environ["CRM_PROVIDER"] = "mock"
        self.addCleanup(self._restore_crm_provider)

        self.store = MemoryActionStore()
        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-verified-panel-secret",
            APPROVAL_ACTION_STORE=self.store,
            ACTION_EXECUTOR=None,
        )
        self.client = self.app.test_client()

    def _restore_crm_provider(self):
        if self.old_crm_provider is not None:
            os.environ["CRM_PROVIDER"] = self.old_crm_provider
        else:
            os.environ.pop("CRM_PROVIDER", None)

    # 1. Customer with no action has no verified mutation, but keeps ordinary CRM record
    def test_customer_with_no_action_not_verified(self):
        # Query customer detail API for ACC-003 (no actions exist in store)
        res = self.client.get("/api/customers/ACC-003")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        # Verified updates must be None and sync flag False
        self.assertIsNone(data.get("verified_update"))
        self.assertFalse(data.get("has_verified_mutation"))
        self.assertFalse(data.get("crm_synced"))
        self.assertFalse(data.get("mock_crm_synced"))

        # Ordinary CRM fields must remain visible and populated
        self.assertIn("crm_record", data)
        self.assertTrue(data.get("crm_record"))
        self.assertIn("customer_status", data.get("crm_record", {}))
        self.assertIn("account_owner", data.get("crm_record", {}))

        # Query customer impact API for ACC-003
        impact_res = self.client.get("/api/customers/ACC-003/impact")
        self.assertEqual(impact_res.status_code, 200)
        impact_data = impact_res.get_json()
        account = impact_data.get("account", {})

        self.assertIsNone(account.get("verified_update"))
        self.assertFalse(account.get("has_verified_mutation"))
        self.assertFalse(account.get("crm_synced"))
        self.assertFalse(account.get("mock_crm_synced"))
        self.assertIn("crm_record", account)

    # 2. Customer with a pending action is NOT treated as verified
    def test_customer_with_pending_action_not_verified(self):
        self.store.actions["act-pending-1"] = {
            "action_id": "act-pending-1",
            "account_id": "ACC-004",
            "action_type": "customer_outreach_email",
            "proposed_payload": {
                "proposed_action": "Reach out to customer",
                "explanation": "High churn risk",
                "email": {"subject": "Check-in", "body": "Hello"},
            },
            "evidence_refs": ["EV-1"],
            "status": "PENDING",
            "reviewer_id": None,
            "decision_reason": None,
            "execution_result": None,
        }

        res = self.client.get("/api/customers/ACC-004")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        self.assertIsNone(data.get("verified_update"))
        self.assertFalse(data.get("has_verified_mutation"))
        self.assertFalse(data.get("crm_synced"))
        self.assertFalse(data.get("mock_crm_synced"))

    # 3. Customer with an approved-but-unexecuted action is NOT treated as verified
    def test_customer_with_approved_unexecuted_action_not_verified(self):
        self.store.actions["act-approved-1"] = {
            "action_id": "act-approved-1",
            "account_id": "ACC-005",
            "action_type": "customer_outreach_email",
            "proposed_payload": {
                "proposed_action": "Reach out to customer",
                "explanation": "High churn risk",
                "email": {"subject": "Check-in", "body": "Hello"},
            },
            "evidence_refs": ["EV-1"],
            "status": "APPROVED",
            "reviewer_id": 1,
            "decision_reason": "Approved for outreach",
            "execution_result": None,
        }

        res = self.client.get("/api/customers/ACC-005")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        self.assertIsNone(data.get("verified_update"))
        self.assertFalse(data.get("has_verified_mutation"))
        self.assertFalse(data.get("crm_synced"))
        self.assertFalse(data.get("mock_crm_synced"))

        impact_res = self.client.get("/api/customers/ACC-005/impact")
        self.assertEqual(impact_res.status_code, 200)
        impact_data = impact_res.get_json()
        account = impact_data.get("account", {})
        self.assertIsNone(account.get("verified_update"))
        self.assertFalse(account.get("has_verified_mutation"))

    # 4. Customer with a rejected or failed action is NOT treated as verified
    def test_customer_with_rejected_or_failed_action_not_verified(self):
        self.store.actions["act-rejected-1"] = {
            "action_id": "act-rejected-1",
            "account_id": "ACC-006",
            "action_type": "customer_outreach_email",
            "proposed_payload": {},
            "status": "REJECTED",
            "reviewer_id": 1,
            "execution_result": None,
        }
        self.store.actions["act-failed-1"] = {
            "action_id": "act-failed-1",
            "account_id": "ACC-007",
            "action_type": "customer_outreach_email",
            "proposed_payload": {},
            "status": "FAILED",
            "reviewer_id": 1,
            "execution_result": {"error": "Connection reset", "verified": False},
        }

        res6 = self.client.get("/api/customers/ACC-006")
        self.assertFalse(res6.get_json().get("has_verified_mutation"))

        res7 = self.client.get("/api/customers/ACC-007")
        self.assertFalse(res7.get_json().get("has_verified_mutation"))

    # 5. Customer with a successfully executed and read-back verified action is verified
    def test_customer_with_completed_verified_execution(self):
        exec_receipt = {
            "verified": True,
            "status": "COMPLETED",
            "action_id": "act-exec-1",
            "account_id": "ACC-001",
            "applied_updates": {
                "notes": "Outreach executed: Executive review meeting confirmed."
            },
            "integration": "mcp_mysql_crm",
            "mode": "mysql",
            "verified_record": {
                "account_id": "ACC-001",
                "notes": "Outreach executed: Executive review meeting confirmed.",
                "customer_status": "Active",
            },
            "message": "Approved action act-exec-1 successfully executed and verified via MCP.",
        }

        self.store.actions["act-exec-1"] = {
            "action_id": "act-exec-1",
            "account_id": "ACC-001",
            "action_type": "customer_outreach_email",
            "proposed_payload": {"notes": "Outreach executed: Executive review meeting confirmed."},
            "status": "COMPLETED",
            "reviewer_id": 1,
            "execution_result": exec_receipt,
            "executed_at": "2026-10-12T04:00:00",
        }

        res = self.client.get("/api/customers/ACC-001")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        self.assertTrue(data.get("has_verified_mutation"))
        self.assertTrue(data.get("crm_synced"))
        self.assertTrue(data.get("mock_crm_synced"))
        self.assertIsNotNone(data.get("verified_update"))
        self.assertEqual(data["verified_update"]["action_id"], "act-exec-1")
        self.assertTrue(data["verified_update"]["verified"])
        self.assertEqual(
            data["verified_update"]["applied_updates"]["notes"],
            "Outreach executed: Executive review meeting confirmed.",
        )

        # Customer impact API returns verified receipt
        impact_res = self.client.get("/api/customers/ACC-001/impact")
        self.assertEqual(impact_res.status_code, 200)
        account = impact_res.get_json().get("account", {})
        self.assertTrue(account.get("has_verified_mutation"))
        self.assertEqual(account["verified_update"]["action_id"], "act-exec-1")

    # 6. Execution isolation: ACC-001's verified execution does NOT leak to ACC-002
    def test_customer_execution_isolation(self):
        self.store.actions["act-acc1"] = {
            "action_id": "act-acc1",
            "account_id": "ACC-001",
            "action_type": "customer_outreach_email",
            "status": "COMPLETED",
            "execution_result": {
                "verified": True,
                "status": "COMPLETED",
                "action_id": "act-acc1",
                "account_id": "ACC-001",
                "applied_updates": {"notes": "ACC-001 specific update"},
            },
            "executed_at": "2026-10-12T04:00:00",
        }

        # ACC-001 must be verified
        res1 = self.client.get("/api/customers/ACC-001")
        self.assertTrue(res1.get_json().get("has_verified_mutation"))

        # ACC-002 must NOT be verified
        res2 = self.client.get("/api/customers/ACC-002")
        data2 = res2.get_json()
        self.assertFalse(data2.get("has_verified_mutation"))
        self.assertIsNone(data2.get("verified_update"))

        impact2 = self.client.get("/api/customers/ACC-002/impact")
        acc2 = impact2.get_json().get("account", {})
        self.assertFalse(acc2.get("has_verified_mutation"))
        self.assertIsNone(acc2.get("verified_update"))

    # 7. Customer 360 page template contains both CRM details panel and Verified CRM panel
    def test_customer_page_template_panels(self):
        res = self.client.get("/customers/ACC-001")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        self.assertIn("crm-details-panel", html)
        self.assertIn("crm-details-grid", html)
        self.assertIn("CRM Account Profile", html)
        self.assertIn("verified-crm-panel", html)
        self.assertIn("verified-crm-grid", html)
        self.assertIn("Verified CRM Updates", html)
        self.assertIn("crm-sync-badge", html)


if __name__ == "__main__":
    unittest.main()
