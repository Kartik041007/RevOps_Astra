import asyncio
import os
import re
import unittest
from unittest import mock

from app import create_app
from services.approval_actions import ACTION_TYPE
from tests.test_approval_actions import MemoryActionStore


class ActionQueueFeatureTests(unittest.TestCase):
    def setUp(self):
        self.old_crm_provider = os.environ.get("CRM_PROVIDER")
        os.environ["CRM_PROVIDER"] = "mock"
        self.addCleanup(self._restore_crm_provider)

        self.store = MemoryActionStore()
        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-action-queue-secret",
            APPROVAL_ACTION_STORE=self.store,
            ACTION_EXECUTOR=None,
        )
        self.client = self.app.test_client()

        page = self.client.get("/ai-agent")
        token = re.search(
            r'<meta name="csrf-token" content="([^"]+)"', page.get_data(as_text=True)
        ).group(1)
        self.headers = {"X-CSRFToken": token}

        with self.client.session_transaction() as session:
            session["admin_id"] = 1
            session["admin_name"] = "AdminUser"
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
                "source_record_id": "SRC-001-A; SRC-001-B",
            }]
        return []

    def sample_proposal(self):
        return {
            "account_id": "ACC-001",
            "action_type": ACTION_TYPE,
            "proposed_payload": {
                "proposed_action": "Schedule executive review",
                "explanation": "Usage declined 18% over the past 30 days.",
                "email": {
                    "subject": "Executive Review Meeting",
                    "body": "Dear Partner,\nWe would like to review recent usage patterns.",
                },
            },
            "evidence_refs": ["SRC-001-A"],
        }

    # 1. Opening and closing the Action Queue panel (DOM structure & handlers)
    def test_1_open_and_close_queue_panel_structure(self):
        res = self.client.get("/ai-agent")
        html = res.get_data(as_text=True)

        # Trigger button replaced Account Health in w5 position
        self.assertIn('id="openQueueButton"', html)
        self.assertIn('class="word w5 action-queue-btn"', html)
        self.assertIn('id="queueBadge"', html)
        self.assertNotIn("Account Health</div>", html)

        # Separate panel exists outside .agent-panel
        self.assertIn('<section id="queuePanel" class="queue-panel" hidden', html)
        self.assertIn('id="closeQueue"', html)
        self.assertIn('id="refreshQueue"', html)
        self.assertIn('id="queueLoading"', html)
        self.assertIn('id="queueStatus"', html)
        self.assertIn('id="actionQueue"', html)

        # Verify JavaScript contains open/close handlers, escape key and outside click handling
        self.assertIn("openQueueButton.addEventListener(\"click\"", html)
        self.assertIn("closeQueue.addEventListener(\"click\", closeQueuePanel)", html)
        self.assertIn("event.key === \"Escape\"", html)

    # 2. Submitting a proposal and seeing it appear
    def test_2_submit_proposal_appears_in_queue(self):
        # Queue is empty initially
        q0 = self.client.get("/api/actions").get_json()
        self.assertEqual(q0["count"], 0)

        # Submit proposal
        res = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        action_id = data["action"]["action_id"]
        self.assertTrue(re.fullmatch(r"[0-9a-fA-F-]{36}", action_id))
        self.assertEqual(data["action"]["status"], "PENDING")

        # Automatically returned in queue
        q1 = self.client.get("/api/actions").get_json()
        self.assertEqual(q1["count"], 1)
        self.assertEqual(q1["actions"][0]["action_id"], action_id)
        self.assertEqual(q1["actions"][0]["status"], "PENDING")

    # 3. Correct pending-count updates
    def test_3_pending_count_updates(self):
        # 0 pending initially
        actions = self.client.get("/api/actions").get_json()["actions"]
        pending0 = len([a for a in actions if a["status"] == "PENDING"])
        self.assertEqual(pending0, 0)

        # Create 1st proposal -> pending = 1
        res1 = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers)
        id1 = res1.get_json()["action"]["action_id"]
        actions1 = self.client.get("/api/actions").get_json()["actions"]
        self.assertEqual(len([a for a in actions1 if a["status"] == "PENDING"]), 1)

        # Create 2nd proposal -> pending = 2
        res2 = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers)
        id2 = res2.get_json()["action"]["action_id"]
        actions2 = self.client.get("/api/actions").get_json()["actions"]
        self.assertEqual(len([a for a in actions2 if a["status"] == "PENDING"]), 2)

        # Approve 1st proposal -> pending decreases to 1
        self.client.post(f"/api/actions/{id1}/approve", json={}, headers=self.headers)
        actions3 = self.client.get("/api/actions").get_json()["actions"]
        self.assertEqual(len([a for a in actions3 if a["status"] == "PENDING"]), 1)

        # Reject 2nd proposal -> pending decreases to 0
        self.client.post(f"/api/actions/{id2}/reject", json={"reason": "Customer requested no outreach"}, headers=self.headers)
        actions4 = self.client.get("/api/actions").get_json()["actions"]
        self.assertEqual(len([a for a in actions4 if a["status"] == "PENDING"]), 0)

    # 4. Approval and rejection status updates
    def test_4_approval_and_rejection_status_updates(self):
        prop1 = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers).get_json()["action"]["action_id"]
        prop2 = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers).get_json()["action"]["action_id"]

        # Approve prop1
        app_res = self.client.post(f"/api/actions/{prop1}/approve", json={}, headers=self.headers)
        self.assertEqual(app_res.status_code, 200)
        self.assertEqual(app_res.get_json()["action"]["status"], "APPROVED")

        # Reject prop2
        rej_res = self.client.post(f"/api/actions/{prop2}/reject", json={"reason": "Not appropriate at this stage"}, headers=self.headers)
        self.assertEqual(rej_res.status_code, 200)
        self.assertEqual(rej_res.get_json()["action"]["status"], "REJECTED")
        self.assertEqual(rej_res.get_json()["action"]["decision_reason"], "Not appropriate at this stage")

    # 5. Missing rejection reason validation
    def test_5_missing_rejection_reason_validation(self):
        prop = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers).get_json()["action"]["action_id"]

        # Empty reason fails
        empty_res = self.client.post(f"/api/actions/{prop}/reject", json={"reason": "   "}, headers=self.headers)
        self.assertEqual(empty_res.status_code, 400)
        self.assertIn("rejection reason", empty_res.get_json()["error"].lower())

        # Missing reason field fails
        missing_res = self.client.post(f"/api/actions/{prop}/reject", json={}, headers=self.headers)
        self.assertEqual(missing_res.status_code, 400)

        # Status remains PENDING
        self.assertEqual(self.store.get_action(prop)["status"], "PENDING")

    # 6. Failed submission handling
    def test_6_failed_submission_behaviour(self):
        # Invalid account ID
        bad_prop = self.sample_proposal()
        bad_prop["account_id"] = "ACC-NONEXISTENT"
        res = self.client.post("/api/actions", json=bad_prop, headers=self.headers)
        self.assertEqual(res.status_code, 404)

        # Invalid payload (extra fields)
        bad_payload = self.sample_proposal()
        bad_payload["proposed_payload"]["unauthorized_field"] = "hack"
        res2 = self.client.post("/api/actions", json=bad_payload, headers=self.headers)
        self.assertEqual(res2.status_code, 400)

    # 7. Duplicate click prevention & conflict handling
    def test_7_duplicate_decisions_conflict(self):
        prop = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers).get_json()["action"]["action_id"]
        res1 = self.client.post(f"/api/actions/{prop}/approve", json={}, headers=self.headers)
        self.assertEqual(res1.status_code, 200)

        # Duplicate approve returns 409 Conflict
        res2 = self.client.post(f"/api/actions/{prop}/approve", json={}, headers=self.headers)
        self.assertEqual(res2.status_code, 409)

        # Reject after approve returns 409 Conflict
        res3 = self.client.post(f"/api/actions/{prop}/reject", json={"reason": "changed mind"}, headers=self.headers)
        self.assertEqual(res3.status_code, 409)

    # 8. Existing customer brief generation works
    def test_8_customer_brief_generation(self):
        with mock.patch("ai.llm_service.generate_customer_brief") as mock_brief:
            mock_brief.return_value = {
                "account_id": "ACC-001",
                "customer": {"company_name": "Northstar Components", "risk_level": "Urgent"},
                "draft": {
                    "issue_summary": "Declining usage",
                    "recommended_next_action": "Outreach email",
                    "evidence_used": ["SRC-001-A"],
                    "email_subject": "Checking in",
                    "email_body": "Hello,\nChecking in.",
                },
                "email_sent": False,
                "crm_updated": False,
            }
            res = self.client.post("/api/ai-agent/brief", json={"account_id": "ACC-001"})
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data["success"])
            self.assertEqual(data["brief"]["draft"]["email_subject"], "Checking in")

    # 9. Persistence after reloading the page
    def test_9_persistence_across_requests(self):
        prop = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers).get_json()["action"]["action_id"]
        self.client.post(f"/api/actions/{prop}/approve", json={}, headers=self.headers)

        # Simulate page reload (new GET request)
        reloaded = self.client.get("/api/actions").get_json()
        matching = next((a for a in reloaded["actions"] if a["action_id"] == prop), None)
        self.assertIsNotNone(matching)
        self.assertEqual(matching["status"], "APPROVED")
        self.assertEqual(matching["customer_name"], "Northstar Components")
        self.assertEqual(matching["risk_level"], "Urgent")

    # 10. Confirmation that no email or CRM update is executed
    def test_10_no_email_or_crm_execution(self):
        prop = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers).get_json()["action"]["action_id"]
        self.client.post(f"/api/actions/{prop}/approve", json={}, headers=self.headers)

        # Execution without configured executor returns 503 and records EXECUTION_BLOCKED
        exec_res = self.client.post(
            f"/api/actions/{prop}/execute",
            json={"payload": self.sample_proposal()["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(exec_res.status_code, 503)
        self.assertEqual(self.store.get_action(prop)["status"], "APPROVED")
        audit_events = self.store.get_audit_events(prop)
        event_types = [e["event_type"] for e in audit_events]
        self.assertEqual(event_types, ["PROPOSED", "APPROVED", "EXECUTION_BLOCKED"])

    # 11. Edit email draft persists to backend and emits audit trail
    def test_11_edit_email_draft_persists_to_backend(self):
        prop = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers).get_json()["action"]["action_id"]

        updated_email = {
            "subject": "Updated Executive Review for Q3",
            "body": "Dear Partner,\n\nWe would like to discuss recent performance trends and our Q3 review.",
        }
        res = self.client.put(
            f"/api/actions/{prop}/email",
            json={"email": updated_email},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("Email draft updated successfully", data["message"])
        self.assertEqual(data["action"]["proposed_payload"]["email"]["subject"], updated_email["subject"])
        self.assertEqual(data["action"]["proposed_payload"]["email"]["body"], updated_email["body"])

        # Check backend store
        stored = self.store.get_action(prop)
        self.assertEqual(stored["proposed_payload"]["email"]["subject"], updated_email["subject"])
        self.assertEqual(stored["proposed_payload"]["email"]["body"], updated_email["body"])

        # Verify audit trail
        audit_events = self.store.get_audit_events(prop)
        self.assertEqual(audit_events[-1]["event_type"], "EMAIL_EDITED")
        self.assertEqual(audit_events[-1]["details"]["subject"], updated_email["subject"])

    # 12. Edited email survives page reload (GET /api/actions)
    def test_12_edited_email_survives_reload_and_get_queue(self):
        prop = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers).get_json()["action"]["action_id"]

        new_subject = "Custom Subject Line 123"
        new_body = "Custom Body Line 456"
        self.client.put(
            f"/api/actions/{prop}/email",
            json={"email": {"subject": new_subject, "body": new_body}},
            headers=self.headers,
        )

        # Fetch entire queue to simulate page reload
        reloaded = self.client.get("/api/actions").get_json()
        matching = next(a for a in reloaded["actions"] if a["action_id"] == prop)
        self.assertEqual(matching["proposed_payload"]["email"]["subject"], new_subject)
        self.assertEqual(matching["proposed_payload"]["email"]["body"], new_body)

    # 13. Editing an approved action resets status to PENDING and requires fresh approval
    def test_13_editing_after_approval_resets_status_to_pending(self):
        prop = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers).get_json()["action"]["action_id"]

        # Initial approval
        app_res = self.client.post(f"/api/actions/{prop}/approve", json={}, headers=self.headers)
        self.assertEqual(app_res.status_code, 200)
        self.assertEqual(app_res.get_json()["action"]["status"], "APPROVED")

        # Edit the email of the approved action
        edit_res = self.client.put(
            f"/api/actions/{prop}/email",
            json={"email": {"subject": "Revised After Approval", "body": "Revised body."}},
            headers=self.headers,
        )
        self.assertEqual(edit_res.status_code, 200)
        action_data = edit_res.get_json()["action"]

        # Status must be reset to PENDING and require fresh approval
        self.assertEqual(action_data["status"], "PENDING")
        self.assertIsNone(action_data["reviewer_id"])

        # Check in store
        stored = self.store.get_action(prop)
        self.assertEqual(stored["status"], "PENDING")
        self.assertIsNone(stored["reviewer_id"])

        # Fresh approval now succeeds with the revised draft
        app_res2 = self.client.post(f"/api/actions/{prop}/approve", json={}, headers=self.headers)
        self.assertEqual(app_res2.status_code, 200)
        self.assertEqual(app_res2.get_json()["action"]["status"], "APPROVED")
        self.assertEqual(
            app_res2.get_json()["action"]["proposed_payload"]["email"]["subject"],
            "Revised After Approval",
        )

    # 14. Validation rejects empty or excessive email fields
    def test_14_validation_rejects_empty_or_excessive_email_fields(self):
        prop = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers).get_json()["action"]["action_id"]
        original_subject = self.sample_proposal()["proposed_payload"]["email"]["subject"]

        # Empty subject
        res1 = self.client.put(f"/api/actions/{prop}/email", json={"email": {"subject": "   ", "body": "valid"}}, headers=self.headers)
        self.assertEqual(res1.status_code, 400)
        self.assertIn("subject", res1.get_json()["error"].lower())

        # Subject > 300 characters
        res2 = self.client.put(f"/api/actions/{prop}/email", json={"email": {"subject": "A" * 301, "body": "valid"}}, headers=self.headers)
        self.assertEqual(res2.status_code, 400)

        # Empty body
        res3 = self.client.put(f"/api/actions/{prop}/email", json={"email": {"subject": "valid", "body": "   "}}, headers=self.headers)
        self.assertEqual(res3.status_code, 400)
        self.assertIn("body", res3.get_json()["error"].lower())

        # Body > 10000 characters
        res4 = self.client.put(f"/api/actions/{prop}/email", json={"email": {"subject": "valid", "body": "B" * 10001}}, headers=self.headers)
        self.assertEqual(res4.status_code, 400)

        # Missing json
        res5 = self.client.put(f"/api/actions/{prop}/email", headers=self.headers)
        self.assertEqual(res5.status_code, 400)

        # Store remains completely unmodified
        self.assertEqual(self.store.get_action(prop)["proposed_payload"]["email"]["subject"], original_subject)

    # 15. Actions without email cannot be edited via email route
    def test_15_actions_without_email_cannot_be_edited_via_email_route(self):
        action = self.store.create_action(
            action_id="11111111-2222-3333-4444-555555555555",
            account_id="ACC-001",
            action_type="account_note",
            proposed_payload={
                "proposed_action": "Internal risk escalation note",
                "explanation": "Account flagged for internal watch.",
            },
            evidence_refs=["SRC-001-A"],
            actor_admin_id=1,
        )
        prop = action["action_id"]

        # Attempt to edit email draft on action without an email
        res = self.client.put(
            f"/api/actions/{prop}/email",
            json={"email": {"subject": "New Subject", "body": "New Body"}},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("does not have an email draft", res.get_json()["error"].lower())

        # Normal approval still functions perfectly
        app_res = self.client.post(f"/api/actions/{prop}/approve", json={}, headers=self.headers)
        self.assertEqual(app_res.status_code, 200)
        self.assertEqual(app_res.get_json()["action"]["status"], "APPROVED")

    # 16. Email edit requires reviewer authentication and CSRF token
    def test_16_email_edit_requires_auth_and_csrf(self):
        prop = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers).get_json()["action"]["action_id"]

        # Missing CSRF
        no_csrf = self.client.put(
            f"/api/actions/{prop}/email",
            json={"email": {"subject": "A", "body": "B"}},
        )
        self.assertEqual(no_csrf.status_code, 400)

        # Missing auth
        with self.client.session_transaction() as session:
            session.clear()
        no_auth = self.client.put(
            f"/api/actions/{prop}/email",
            json={"email": {"subject": "A", "body": "B"}},
            headers=self.headers,
        )
        self.assertEqual(no_auth.status_code, 401)

    # 17. Rejected action cannot be edited
    def test_17_rejected_action_cannot_be_edited(self):
        prop = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers).get_json()["action"]["action_id"]
        self.client.post(f"/api/actions/{prop}/reject", json={"reason": "Customer churned"}, headers=self.headers)

        res = self.client.put(
            f"/api/actions/{prop}/email",
            json={"email": {"subject": "Attempt", "body": "Attempt"}},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 409)

    # 18. Template contains inline email editor UI elements and safeguards
    def test_18_template_has_email_editing_ui_and_unsaved_edits_guard(self):
        page = self.client.get("/ai-agent")
        html = page.get_data(as_text=True)

        # Classes and styles in template
        self.assertIn(".edit-email-btn", html)
        self.assertIn(".action-email-editor", html)
        self.assertIn(".editor-subject-input", html)
        self.assertIn(".editor-body-textarea", html)
        self.assertIn(".save-draft-btn", html)
        self.assertIn(".cancel-edit-btn", html)
        self.assertIn(".unsaved-warning", html)

        # JS logic elements
        self.assertIn("Edit Email", html)
        self.assertIn("Save Draft", html)
        self.assertIn("Cancel", html)
        self.assertIn("getHasUnsavedEdits", html)
        self.assertIn("/api/actions/${action.action_id}/email", html)
        self.assertIn("You have unsaved email edits", html)

    # 19. Template contains Execute Approved Action control, verification feedback, and safeguards
    def test_19_template_has_execute_action_controls_and_feedback(self):
        page = self.client.get("/ai-agent")
        html = page.get_data(as_text=True)

        # CSS classes and styling in template
        self.assertIn(".execute-button", html)
        self.assertIn(".action-execution-controls", html)
        self.assertIn(".execution-feedback", html)
        self.assertIn(".action-execution-result-box", html)
        self.assertIn(".exec-badge", html)
        self.assertIn(".exec-message", html)

        # JS logic and handler checks
        self.assertIn("Execute Approved Action", html)
        self.assertIn("action-execution-controls", html)
        self.assertIn("executeAction(action, executeBtn, execFeedback)", html)
        self.assertIn("/api/actions/${encodeURIComponent(action.action_id)}/execute", html)
        self.assertIn("Calling MCP update_customer_record and verifying read-back", html)
        self.assertIn("Action executed and verified via MCP", html)
        self.assertIn("Verified via MCP", html)
        self.assertIn("You have unsaved email edits. Please save your draft or cancel changes before executing.", html)

    # 20. Execution endpoint completes verified action and updates queue
    def test_20_execution_endpoint_completes_verified_action(self):
        # Configure mock executor that returns a verified result
        self.app.config["ACTION_EXECUTOR"] = lambda action: {
            "verified": True,
            "status": "COMPLETED",
            "action_id": action["action_id"],
            "account_id": action["account_id"],
            "applied_updates": {"notes": "Outreach completed"},
            "integration": "mcp_mock_crm",
            "mode": "mock",
            "message": f"Action {action['action_id']} executed and verified via MCP.",
        }

        prop = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers).get_json()["action"]["action_id"]

        # Approve action first
        app_res = self.client.post(f"/api/actions/{prop}/approve", json={}, headers=self.headers)
        self.assertEqual(app_res.status_code, 200)
        self.assertEqual(app_res.get_json()["action"]["status"], "APPROVED")

        # Execute approved action via execution endpoint
        exec_res = self.client.post(
            f"/api/actions/{prop}/execute",
            json={"payload": self.sample_proposal()["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(exec_res.status_code, 200)
        data = exec_res.get_json()
        self.assertEqual(data["message"], "Execution verified.")
        self.assertEqual(data["action"]["status"], "COMPLETED")
        self.assertTrue(data["action"]["execution_result"]["verified"])

        # Fetch entire queue to verify updated action status
        queue_res = self.client.get("/api/actions").get_json()
        matching = next(a for a in queue_res["actions"] if a["action_id"] == prop)
        self.assertEqual(matching["status"], "COMPLETED")
        self.assertTrue(matching["execution_result"]["verified"])

        # Duplicate execution is blocked with 409 Conflict
        dup_res = self.client.post(
            f"/api/actions/{prop}/execute",
            json={"payload": self.sample_proposal()["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(dup_res.status_code, 409)

    # 21. Pending and rejected actions cannot execute via API
    def test_21_pending_and_rejected_actions_blocked_from_execution_endpoint(self):
        self.app.config["ACTION_EXECUTOR"] = lambda action: {"verified": True}

        # Pending action
        prop1 = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers).get_json()["action"]["action_id"]
        exec_pending = self.client.post(
            f"/api/actions/{prop1}/execute",
            json={"payload": self.sample_proposal()["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(exec_pending.status_code, 409)

        # Rejected action
        prop2 = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers).get_json()["action"]["action_id"]
        self.client.post(f"/api/actions/{prop2}/reject", json={"reason": "Deferred"}, headers=self.headers)
        exec_rejected = self.client.post(
            f"/api/actions/{prop2}/execute",
            json={"payload": self.sample_proposal()["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(exec_rejected.status_code, 409)

    # 22. Tampered or mismatched payload is rejected by execution endpoint
    def test_22_tampered_payload_rejected_by_execution_endpoint(self):
        self.app.config["ACTION_EXECUTOR"] = lambda action: {"verified": True}

        prop = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers).get_json()["action"]["action_id"]
        self.client.post(f"/api/actions/{prop}/approve", json={}, headers=self.headers)

        tampered_payload = dict(self.sample_proposal()["proposed_payload"])
        tampered_payload["email"] = {"subject": "Tampered Subject", "body": "Tampered Body"}

        exec_res = self.client.post(
            f"/api/actions/{prop}/execute",
            json={"payload": tampered_payload},
            headers=self.headers,
        )
        self.assertEqual(exec_res.status_code, 400)
        self.assertIn("does not match", exec_res.get_json()["error"])

    # 23. Submission loading state reset, timeout protection, and false positive prevention
    def test_23_submission_failure_and_loading_state_reset(self):
        page = self.client.get("/ai-agent")
        html = page.get_data(as_text=True)

        # Verify frontend contains concurrency guard
        self.assertIn("let isSubmittingProposal = false;", html)
        self.assertIn("if (!currentDraft || isSubmittingProposal) return;", html)

        # Verify apiRequest contains AbortController timeout handling
        self.assertIn("const controller = new AbortController();", html)
        self.assertIn("Request timed out. Please try again.", html)

        # Verify try/catch/finally ensures loading state is reset
        self.assertIn("} catch (saveError) {", html)
        self.assertIn("} finally {", html)
        self.assertIn("isSubmittingProposal = false;", html)
        self.assertIn('if (proposalStatus.classList.contains("loading"))', html)
        self.assertIn('proposalStatus.classList.remove("loading");', html)

        # Verify renderBrief resets disabled state
        self.assertIn("queueProposal.disabled = false;", html)

        # Verify failed request cannot falsely claim creation
        self.assertIn("if (!savedAction || !actionId) {", html)
        self.assertIn("Proposal was not created: server returned an incomplete response.", html)

        # Backend: Invalid proposal returns 400 JSON error without creating action
        bad_res = self.client.post(
            "/api/actions",
            json={"account_id": "ACC-001"},
            headers=self.headers,
        )
        self.assertEqual(bad_res.status_code, 400)
        self.assertIn("error", bad_res.get_json())
        self.assertEqual(len(self.store.list_actions()), 0)

        # Backend: Store exception returns clean 500 JSON error
        with mock.patch.object(self.store, "create_action", side_effect=RuntimeError("Storage failure")):
            err_res = self.client.post(
                "/api/actions",
                json=self.sample_proposal(),
                headers=self.headers,
            )
            self.assertEqual(err_res.status_code, 500)
            self.assertIn("Failed to save proposal", err_res.get_json()["error"])
            self.assertEqual(len(self.store.list_actions()), 0)

    # 24. Complete lifecycle reflection across Action Queue, customer brief, and customer endpoints
    @mock.patch("ai.llm_service.generate_customer_brief")
    @mock.patch("routes.dashboard.read_output_json")
    def test_24_dashboard_and_brief_reflect_approved_and_executed_lifecycle(self, mock_summary, mock_brief_gen):
        mock_summary.return_value = {
            "priority_accounts": [
                {
                    "account_id": "ACC-001",
                    "company_name": "Northstar Components",
                    "risk_level": "Urgent",
                    "recorded_revenue_90d": 9747.17,
                }
            ]
        }
        mock_brief_gen.return_value = {
            "account_id": "ACC-001",
            "customer": {
                "account_id": "ACC-001",
                "company_name": "Northstar Components",
                "risk_level": "Urgent",
                "risk_index": 70,
            },
            "draft": {
                "issue_summary": "Usage declined 18%",
                "recommended_next_action": "Schedule executive review",
                "evidence_used": ["SRC-001-A"],
                "email_subject": "Executive Review Meeting",
                "email_body": "Dear Partner,\nWe would like to review recent usage patterns.",
            },
            "approval_required": True,
            "email_sent": False,
            "crm_updated": False,
        }

        # Step 1: Submit proposal -> PENDING
        submit_res = self.client.post("/api/actions", json=self.sample_proposal(), headers=self.headers)
        self.assertEqual(submit_res.status_code, 201)
        action_id = submit_res.get_json()["action"]["action_id"]

        # Customer brief reflects PENDING state
        brief_res = self.client.post("/api/ai-agent/brief", json={"account_id": "ACC-001"}, headers=self.headers)
        self.assertEqual(brief_res.status_code, 200)
        brief_data = brief_res.get_json()["brief"]
        self.assertEqual(brief_data["latest_action"]["status"], "PENDING")
        self.assertFalse(brief_data.get("crm_updated", False))

        # Step 2: Human approval -> APPROVED (CRM not executed yet)
        app_res = self.client.post(f"/api/actions/{action_id}/approve", json={}, headers=self.headers)
        self.assertEqual(app_res.status_code, 200)
        self.assertEqual(app_res.get_json()["action"]["status"], "APPROVED")

        # Customer brief reflects APPROVED state
        brief_res2 = self.client.post("/api/ai-agent/brief", json={"account_id": "ACC-001"}, headers=self.headers)
        self.assertEqual(brief_res2.status_code, 200)
        brief_data2 = brief_res2.get_json()["brief"]
        self.assertEqual(brief_data2["latest_action"]["status"], "APPROVED")
        self.assertFalse(brief_data2.get("crm_updated", False))

        # Step 3: Explicit execution via mock executor -> COMPLETED + mock CRM update
        from mcp_server.mock_crm_db import mock_crm_db
        mock_crm_db.update_customer_in_memory("ACC-001", {"notes": "Verified outreach note"})

        self.app.config["ACTION_EXECUTOR"] = lambda action: {
            "verified": True,
            "message": "Action executed and verified via MCP.",
            "updated_fields": {"notes": "Verified outreach note"},
        }
        exec_res = self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": self.sample_proposal()["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(exec_res.status_code, 200)
        self.assertEqual(exec_res.get_json()["action"]["status"], "COMPLETED")

        # Step 4: Verify customer brief reflects COMPLETED state and updated CRM fields
        brief_res3 = self.client.post("/api/ai-agent/brief", json={"account_id": "ACC-001"}, headers=self.headers)
        self.assertEqual(brief_res3.status_code, 200)
        brief_data3 = brief_res3.get_json()["brief"]
        self.assertEqual(brief_data3["latest_action"]["status"], "COMPLETED")
        self.assertTrue(brief_data3.get("crm_updated", False))
        self.assertIn("crm_record", brief_data3)
        self.assertEqual(brief_data3["crm_record"]["notes"], "Verified outreach note")
        # Assert verified fields synced to brief customer block directly
        self.assertEqual(brief_data3["customer"]["notes"], "Verified outreach note")
        self.assertTrue(brief_data3.get("mock_crm_synced", False))

        # Step 5: Verify customer detail endpoint reflects updated CRM record and overlaid fields
        cust_res = self.client.get("/api/customers/ACC-001")
        self.assertEqual(cust_res.status_code, 200)
        cust_data = cust_res.get_json()
        self.assertTrue(cust_data.get("mock_crm_synced", False))
        self.assertEqual(cust_data["notes"], "Verified outreach note")
        self.assertEqual(cust_data["crm_record"]["notes"], "Verified outreach note")

        # Step 6: Verify reading through a fresh MCP client session against the MCP server
        from mcp_server.client import RevOpsMCPClient
        fresh_client = RevOpsMCPClient()
        fresh_mcp_res = asyncio.run(fresh_client.get_customer("ACC-001"))
        self.assertTrue(fresh_mcp_res.get("success"))
        fresh_cust = fresh_mcp_res.get("customer", {})
        self.assertEqual(fresh_cust.get("notes"), "Verified outreach note")

    @mock.patch("routes.dashboard.read_output_csv")
    @mock.patch("routes.approval_actions.read_output_csv")
    def test_25_acc_020_executed_crm_field_updates_sync(self, mock_action_csv, mock_dash_csv):
        """Regression test for ACC-020: executed CRM update is reflected across fresh MCP, detail, and impact APIs."""
        csv_data = [
            {
                "account_id": "ACC-020",
                "company_name": "Oakridge Components 020",
                "industry": "Packaging",
                "region": "North",
                "account_owner": "Neha Sharma",
                "risk_level": "Needs Review",
                "risk_index": "40",
                "usage_change_pct": "-23.1",
                "recorded_revenue_90d": "0.0",
                "active_users_avg_30d": "35.4",
                "open_tickets": "1",
                "risk_drivers": "Usage declined 23.1% over comparison period.",
                "evidence_confidence": "100",
                "confidence_label": "High",
            }
        ]
        evidence_data = [
            {
                "account_id": "ACC-020",
                "source_record_id": "SRC-020-A",
                "polarity": "risk",
                "evidence_detail": "Negative interaction noted.",
            }
        ]

        def csv_side_effect(filename):
            if filename in ("account_features.csv", "account_risk_insights.csv"):
                return csv_data
            if filename == "account_evidence.csv":
                return evidence_data
            return []

        mock_action_csv.side_effect = csv_side_effect
        mock_dash_csv.side_effect = csv_side_effect

        from mcp_server.mock_crm_db import mock_crm_db
        from mcp_server.client import RevOpsMCPClient

        mock_crm_db.reset_mock_updates()
        self.addCleanup(mock_crm_db.reset_mock_updates)

        # 1. Propose action for ACC-020
        proposal = {
            "account_id": "ACC-020",
            "action_type": ACTION_TYPE,
            "proposed_payload": {
                "proposed_action": "Reach out to understand usage decline",
                "explanation": "Usage declined 23.1%",
                "email": {
                    "subject": "Potential Issue with Customer Account ACC-020",
                    "body": "Hello, could we connect?",
                },
            },
            "evidence_refs": ["SRC-020-A"],
        }
        res = self.client.post("/api/actions", json=proposal, headers=self.headers)
        self.assertEqual(res.status_code, 201)
        action_id = res.get_json()["action"]["action_id"]

        # 2. Approve action
        app_res = self.client.post(f"/api/actions/{action_id}/approve", json={}, headers=self.headers)
        self.assertEqual(app_res.status_code, 200)

        # 3. Execute approved action via MCPActionExecutor
        from services.mcp_executor import MCPActionExecutor
        self.app.config["ACTION_EXECUTOR"] = MCPActionExecutor(
            mcp_client=RevOpsMCPClient(),
            store=self.store,
            timeout_seconds=5.0,
        )

        exec_res = self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": proposal["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(exec_res.status_code, 200)
        exec_json = exec_res.get_json()
        self.assertEqual(exec_json["action"]["status"], "COMPLETED")
        executed_note = exec_json["action"]["execution_result"]["applied_updates"]["notes"]

        # 4. Verify fresh MCP client retrieves updated record
        fresh_client = RevOpsMCPClient()
        fresh_res = asyncio.run(fresh_client.get_customer("ACC-020"))
        self.assertTrue(fresh_res.get("success"))
        fresh_customer = fresh_res.get("customer", {})
        self.assertEqual(fresh_customer.get("notes"), executed_note)
        self.assertEqual(fresh_customer.get("customer_status"), "Active")
        self.assertEqual(fresh_customer.get("owner_email"), "neha.sharma@revops-mock.internal")

        # 5. Verify /api/customers/ACC-020 detail API
        detail_res = self.client.get("/api/customers/ACC-020")
        self.assertEqual(detail_res.status_code, 200)
        detail_json = detail_res.get_json()
        self.assertTrue(detail_json.get("mock_crm_synced"))
        self.assertEqual(detail_json.get("notes"), executed_note)
        self.assertEqual(detail_json.get("crm_record", {}).get("notes"), executed_note)
        self.assertEqual(detail_json.get("owner_email"), "neha.sharma@revops-mock.internal")

        # 6. Verify /api/customers/ACC-020/impact endpoint
        impact_res = self.client.get("/api/customers/ACC-020/impact")
        self.assertEqual(impact_res.status_code, 200)
        impact_json = impact_res.get_json()
        impact_acc = impact_json.get("account", {})
        self.assertTrue(impact_acc.get("mock_crm_synced"))
        self.assertEqual(impact_acc.get("notes"), executed_note)
        self.assertEqual(impact_acc.get("crm_record", {}).get("notes"), executed_note)

        # 7. Verify Customer 360 page template contains the verified CRM panel
        page_res = self.client.get("/customers/ACC-020")
        self.assertEqual(page_res.status_code, 200)
        html = page_res.get_data(as_text=True)
        self.assertIn("verified-crm-panel", html)
        self.assertIn("Verified CRM Updates", html)


if __name__ == "__main__":
    unittest.main()


