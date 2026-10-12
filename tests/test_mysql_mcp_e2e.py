"""Controlled end-to-end integration test of approved CRM action execution
using CRM_PROVIDER=mysql and live MySQL persistence.
"""

from __future__ import annotations

import json
import os
import unittest
import uuid

from mcp_server.client import RevOpsMCPClient
from services.approval_actions import (
    ACTION_TYPE,
    ActionConflict,
    MySQLApprovalActionStore,
)
from services.db import get_db_connection
from services.mcp_executor import MCPActionExecutor


class MySQLMCPEndToEndTests(unittest.TestCase):
    """End-to-end verification of Action Queue approval gating, MySQL MCP execution,
    persisted database mutation, read-back verification, and audit logging.
    """

    TEST_ACCOUNT_ID = "ACC-99999"
    INITIAL_NOTE = "Controlled E2E baseline notes for testing."

    @classmethod
    def setUpClass(cls):
        # Verify database connection
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchall()
            cur.close()
            conn.close()
        except Exception as exc:
            raise unittest.SkipTest(f"Live MySQL database is not accessible: {exc}")

        cls.store = MySQLApprovalActionStore(get_db_connection)
        cls.client = RevOpsMCPClient()
        cls.executor = MCPActionExecutor(mcp_client=cls.client, store=cls.store, timeout_seconds=10.0)

    def setUp(self):
        # Ensure clean state for test account ACC-999 before each test
        self._cleanup_test_data()
        self._insert_test_customer(self.TEST_ACCOUNT_ID, self.INITIAL_NOTE)

    def tearDown(self):
        # Clean up test records after each test
        self._cleanup_test_data()

    def _cleanup_test_data(self):
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM audit_logs WHERE account_id = %s", (self.TEST_ACCOUNT_ID,))
            cur.execute(
                """
                DELETE FROM approval_action_audit
                WHERE action_id IN (SELECT action_id FROM approval_actions WHERE account_id = %s)
                """,
                (self.TEST_ACCOUNT_ID,),
            )
            cur.execute("DELETE FROM approval_actions WHERE account_id = %s", (self.TEST_ACCOUNT_ID,))
            cur.execute("DELETE FROM customer_accounts WHERE account_id = %s", (self.TEST_ACCOUNT_ID,))
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def _insert_test_customer(self, account_id: str, initial_notes: str):
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO customer_accounts
                    (account_id, company_name, industry, region, account_owner,
                     owner_email, customer_status, plan_tier, arr_usd,
                     health_score, notes, last_contact_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    account_id,
                    "Controlled Test Enterprise",
                    "Software",
                    "West",
                    "Test Reviewer",
                    "test.reviewer@revops-mock.internal",
                    "Active",
                    "Enterprise",
                    50000.00,
                    80,
                    initial_notes,
                    "2026-10-10",
                ),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def sample_payload(self):
        return {
            "proposed_action": "Execute executive adoption sync",
            "explanation": "Controlled E2E verification test payload.",
            "email": {
                "subject": "Executive Adoption Sync",
                "body": "Dear Partner,\nWe would like to schedule an adoption sync.",
            },
        }

    def test_1_unapproved_and_rejected_actions_blocked_from_execution(self):
        """Verify that PENDING and REJECTED actions are strictly blocked from execution."""
        # 1. Create a PENDING action
        action_id_pending = str(uuid.uuid4())
        self.store.create_action(
            action_id=action_id_pending,
            account_id=self.TEST_ACCOUNT_ID,
            action_type=ACTION_TYPE,
            proposed_payload=self.sample_payload(),
            evidence_refs=["SRC-TEST-1"],
            actor_admin_id=1,
        )

        action_pending = self.store.get_action(action_id_pending)
        self.assertEqual(action_pending["status"], "PENDING")

        # Attempting to execute PENDING action directly must be blocked
        with self.assertRaises(ActionConflict) as ctx:
            self.executor(action_pending)
        self.assertIn("Only APPROVED actions may execute", str(ctx.exception))

        # Attempting to claim execution on PENDING action must raise ActionConflict
        with self.assertRaises(ActionConflict):
            self.store.claim_execution(action_id_pending, self.sample_payload(), actor_admin_id=1)

        # 2. Reject the action and verify execution is blocked
        self.store.decide_action(
            action_id_pending,
            decision="reject",
            reviewer_id=1,
            reason="Blocked by test policy",
        )
        action_rejected = self.store.get_action(action_id_pending)
        self.assertEqual(action_rejected["status"], "REJECTED")

        with self.assertRaises(ActionConflict) as ctx:
            self.executor(action_rejected)
        self.assertIn("Only APPROVED actions may execute", str(ctx.exception))

        with self.assertRaises(ActionConflict):
            self.store.claim_execution(action_id_pending, self.sample_payload(), actor_admin_id=1)

    def test_2_approved_action_executes_in_mysql_mode_and_verifies_audit(self):
        """End-to-end execution of an APPROVED action with CRM_PROVIDER=mysql:
        - Executes via MCP tool against live MySQL
        - Reads back verified record
        - Records CRM mutation in audit_logs
        - Records lifecycle events in approval_action_audit
        - Blocks duplicate execution
        """
        # Step 1: Create action (PENDING)
        action_id = str(uuid.uuid4())
        self.store.create_action(
            action_id=action_id,
            account_id=self.TEST_ACCOUNT_ID,
            action_type=ACTION_TYPE,
            proposed_payload=self.sample_payload(),
            evidence_refs=["SRC-TEST-E2E"],
            actor_admin_id=1,
        )

        # Step 2: Approve action (APPROVED)
        self.store.decide_action(
            action_id,
            decision="approve",
            reviewer_id=1,
            reason="Approved for controlled E2E test",
        )
        action_approved = self.store.get_action(action_id)
        self.assertEqual(action_approved["status"], "APPROVED")

        # Step 3: Claim and execute through MCP executor in CRM_PROVIDER=mysql mode
        old_provider = os.environ.get("CRM_PROVIDER")
        os.environ["CRM_PROVIDER"] = "mysql"
        try:
            claimed_action = self.store.claim_execution(action_id, self.sample_payload(), actor_admin_id=1)
            self.assertEqual(claimed_action["status"], "EXECUTING")

            # Execute via MCP executor
            receipt = self.executor(claimed_action)

            # Finish execution in approval store
            completed_action = self.store.finish_execution(
                action_id=action_id,
                status="COMPLETED",
                result=receipt,
                actor_admin_id=1,
            )
        finally:
            if old_provider is not None:
                os.environ["CRM_PROVIDER"] = old_provider
            else:
                os.environ.pop("CRM_PROVIDER", None)

        # Step 4: Verify MCP execution receipt
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["status"], "COMPLETED")
        self.assertEqual(receipt["integration"], "mcp_mysql_crm")
        self.assertEqual(receipt["mode"], "mysql")
        expected_note = (
            "Outreach executed: Execute executive adoption sync (Subject: Executive Adoption Sync)"
        )
        self.assertEqual(receipt["applied_updates"]["notes"], expected_note)

        # Step 5: Read back verified customer from live MySQL customer_accounts table
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM customer_accounts WHERE account_id = %s", (self.TEST_ACCOUNT_ID,))
            live_cust = cur.fetchone()
            self.assertIsNotNone(live_cust)
            self.assertEqual(live_cust["notes"], expected_note)
            self.assertEqual(live_cust["customer_status"], "Active")

            # Step 6: Verify audit_logs table contains CRM mutation
            cur.execute("SELECT * FROM audit_logs WHERE account_id = %s", (self.TEST_ACCOUNT_ID,))
            crm_audits = cur.fetchall()
            self.assertEqual(len(crm_audits), 1)
            crm_audit = crm_audits[0]
            self.assertEqual(crm_audit["action_id"], action_id)
            self.assertEqual(crm_audit["actor"], "MCP_SERVER")
            self.assertEqual(crm_audit["event_type"], "CRM_RECORD_UPDATED")
            changes = json.loads(crm_audit["changes"])
            self.assertIn("notes", changes)
            self.assertEqual(changes["notes"]["old"], self.INITIAL_NOTE)
            self.assertEqual(changes["notes"]["new"], expected_note)

            # Step 7: Verify approval_action_audit contains lifecycle events
            cur.execute(
                "SELECT event_type, from_status, to_status FROM approval_action_audit WHERE action_id = %s ORDER BY event_id ASC",
                (action_id,),
            )
            lifecycle_events = cur.fetchall()
            event_types = [e["event_type"] for e in lifecycle_events]
            self.assertIn("PROPOSED", event_types)
            self.assertIn("APPROVED", event_types)
            self.assertIn("EXECUTION_STARTED", event_types)
            self.assertIn("COMPLETED", event_types)

        finally:
            cur.close()
            conn.close()

        # Step 8: Confirm duplicate execution is strictly blocked
        with self.assertRaises(ActionConflict):
            self.store.claim_execution(action_id, self.sample_payload(), actor_admin_id=1)

        with self.assertRaises(ActionConflict) as ctx:
            self.executor(completed_action)
        self.assertIn("Duplicate execution is blocked", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
