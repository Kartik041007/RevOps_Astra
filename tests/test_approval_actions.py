import re
import unittest
from unittest import mock

from app import create_app
from services.approval_actions import (
    ACTION_TYPE,
    ActionConflict,
    ActionNotFound,
    validate_email_action_payload,
)


class MemoryActionStore:
    """Test-only store; production requests use the MySQL repository."""

    def __init__(self):
        self.actions = {}
        self.audit = {}

    def list_actions(self, status=None):
        actions = list(self.actions.values())
        if status:
            actions = [action for action in actions if action["status"] == status]
        return actions

    def get_action(self, action_id):
        return self.actions.get(action_id)

    def get_audit_events(self, action_id):
        return self.audit.get(action_id, [])

    def create_action(
        self, action_id, account_id, action_type, proposed_payload,
        evidence_refs, actor_admin_id,
    ):
        action = {
            "action_id": action_id,
            "account_id": account_id,
            "action_type": action_type,
            "proposed_payload": proposed_payload,
            "evidence_refs": evidence_refs,
            "status": "PENDING",
            "reviewer_id": None,
            "decision_reason": None,
            "execution_result": None,
        }
        self.actions[action_id] = action
        self.audit[action_id] = [{
            "event_type": "PROPOSED",
            "actor_admin_id": actor_admin_id,
            "to_status": "PENDING",
        }]
        return action

    def update_email_draft(self, action_id, email, editor_admin_id):
        action = self.actions.get(action_id)
        if action is None:
            raise ActionNotFound(action_id)
        if action["status"] not in ("PENDING", "APPROVED"):
            raise ActionConflict(action["status"])
        payload = action.get("proposed_payload") or {}
        if "email" not in payload:
            raise ValueError("Action does not have an email draft to edit.")
        new_subject = str(email.get("subject", "")).strip()
        new_body = str(email.get("body", "")).strip()
        if not new_subject or len(new_subject) > 300:
            raise ValueError("Email subject must be between 1 and 300 characters.")
        if not new_body or len(new_body) > 10000:
            raise ValueError("Email body must be between 1 and 10000 characters.")
        payload["email"] = {
            "subject": new_subject,
            "body": new_body,
        }
        action["proposed_payload"] = payload
        from_status = action["status"]
        if from_status == "APPROVED":
            action["status"] = "PENDING"
            action["reviewer_id"] = None
            action["decision_reason"] = None
        self.audit[action_id].append({
            "event_type": "EMAIL_EDITED",
            "actor_admin_id": editor_admin_id,
            "from_status": from_status,
            "to_status": "PENDING",
            "details": {"subject": new_subject},
        })
        return action

    def decide_action(self, action_id, decision, reviewer_id, reason=None):
        action = self.actions.get(action_id)
        if action is None:
            raise ActionNotFound(action_id)
        if action["status"] != "PENDING":
            raise ActionConflict(action["status"])
        if decision == "approve":
            if action["action_type"] == ACTION_TYPE:
                validate_email_action_payload(
                    action["action_type"], action["proposed_payload"]
                )
            action["status"] = "APPROVED"
        else:
            action["status"] = "REJECTED"
        action["reviewer_id"] = reviewer_id
        action["decision_reason"] = reason
        self.audit[action_id].append({
            "event_type": action["status"],
            "actor_admin_id": reviewer_id,
            "reason": reason,
        })
        return action

    def record_execution_blocked(self, action_id, actor_admin_id):
        action = self.actions.get(action_id)
        if action is None:
            raise ActionNotFound(action_id)
        if action["status"] != "APPROVED":
            raise ActionConflict(action["status"])
        self.audit[action_id].append({
            "event_type": "EXECUTION_BLOCKED",
            "actor_admin_id": actor_admin_id,
        })

    def claim_execution(self, action_id, execution_payload, actor_admin_id):
        action = self.actions.get(action_id)
        if action is None:
            raise ActionNotFound(action_id)
        if action["status"] != "APPROVED":
            raise ActionConflict(action["status"])
        payload = validate_email_action_payload(
            action["action_type"], action["proposed_payload"]
        )
        if payload != execution_payload:
            raise ValueError("Execution payload does not match the approved payload.")
        action["status"] = "EXECUTING"
        self.audit[action_id].append({
            "event_type": "EXECUTION_STARTED",
            "actor_admin_id": actor_admin_id,
        })
        return action

    def finish_execution(self, action_id, status, result, actor_admin_id):
        action = self.actions.get(action_id)
        if action is None:
            raise ActionNotFound(action_id)
        if action["status"] != "EXECUTING":
            raise ActionConflict(action["status"])
        action["status"] = status
        action["execution_result"] = result
        self.audit[action_id].append({
            "event_type": status,
            "actor_admin_id": actor_admin_id,
            "details": result,
        })
        return action


class ApprovalActionApiTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryActionStore()
        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="approval-test-secret",
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
            session["admin_id"] = 7
            session["admin_name"] = "Reviewer"
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

    @staticmethod
    def csv_rows(filename):
        if filename == "account_risk_insights.csv":
            return [{
                "account_id": "ACC-TEST",
                "company_name": "Test Account",
                "risk_level": "Needs Review",
            }]
        if filename == "account_evidence.csv":
            return [{
                "account_id": "ACC-TEST",
                "source_record_id": "SRC-TEST-1",
            }]
        return []

    @staticmethod
    def proposal():
        return {
            "account_id": "ACC-TEST",
            "action_type": ACTION_TYPE,
            "proposed_payload": {
                "proposed_action": "Send a follow-up",
                "explanation": "Usage has declined in the provided evidence.",
                "email": {
                    "subject": "Checking in",
                    "body": "Would you be available to discuss recent usage?",
                },
            },
            "evidence_refs": ["SRC-TEST-1"],
        }

    def create_proposal(self):
        response = self.client.post(
            "/api/actions",
            json=self.proposal(),
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["action"]["action_id"]

    def approve(self, action_id):
        return self.client.post(
            f"/api/actions/{action_id}/approve",
            json={},
            headers=self.headers,
        )

    def test_approve_persists_decision_without_executing(self):
        action_id = self.create_proposal()
        response = self.approve(action_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["action"]["status"], "APPROVED")
        self.assertEqual(
            self.store.get_audit_events(action_id)[-1]["event_type"],
            "APPROVED",
        )

    def test_reject_requires_reason_and_prevents_execution(self):
        action_id = self.create_proposal()
        rejected = self.client.post(
            f"/api/actions/{action_id}/reject",
            json={"reason": "  "},
            headers=self.headers,
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.store.get_action(action_id)["status"], "PENDING")

        rejected = self.client.post(
            f"/api/actions/{action_id}/reject",
            json={"reason": "Do not contact this customer."},
            headers=self.headers,
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.get_json()["action"]["status"], "REJECTED")
        self.app.config["ACTION_EXECUTOR"] = lambda action: {"verified": True}
        executed = self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": self.proposal()["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(executed.status_code, 409)

    def test_unauthorized_reviewer_is_denied(self):
        action_id = self.create_proposal()
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get("/api/actions").status_code, 401)
        self.assertEqual(self.approve(action_id).status_code, 401)

    def test_invalid_payload_and_account_evidence_are_rejected(self):
        proposal = self.proposal()
        proposal["proposed_payload"]["email"]["unexpected"] = "field"
        response = self.client.post(
            "/api/actions", json=proposal, headers=self.headers
        )
        self.assertEqual(response.status_code, 400)

        proposal = self.proposal()
        proposal["evidence_refs"] = ["SRC-OTHER-ACCOUNT"]
        response = self.client.post(
            "/api/actions", json=proposal, headers=self.headers
        )
        self.assertEqual(response.status_code, 400)

    def test_duplicate_approval_and_execution_are_rejected(self):
        action_id = self.create_proposal()
        self.assertEqual(self.approve(action_id).status_code, 200)
        self.assertEqual(self.approve(action_id).status_code, 409)

        self.app.config["ACTION_EXECUTOR"] = lambda action: {
            "verified": True,
            "result_id": "verified-by-test-adapter",
        }
        execution_payload = self.proposal()["proposed_payload"]
        response = self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": execution_payload},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["action"]["status"], "COMPLETED")
        duplicate = self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": execution_payload},
            headers=self.headers,
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_execution_requires_exact_payload_and_logs_failure(self):
        action_id = self.create_proposal()
        self.assertEqual(self.approve(action_id).status_code, 200)
        self.app.config["ACTION_EXECUTOR"] = lambda action: {
            "verified": True,
        }
        changed_payload = self.proposal()["proposed_payload"]
        changed_payload["email"]["body"] = "Changed after approval"
        mismatch = self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": changed_payload},
            headers=self.headers,
        )
        self.assertEqual(mismatch.status_code, 400)
        self.assertEqual(self.store.get_action(action_id)["status"], "APPROVED")

        self.app.config["ACTION_EXECUTOR"] = lambda action: (_ for _ in ()).throw(
            RuntimeError("adapter unavailable")
        )
        failed = self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": self.proposal()["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(self.store.get_action(action_id)["status"], "FAILED")
        self.assertEqual(
            self.store.get_audit_events(action_id)[-1]["event_type"],
            "FAILED",
        )

    def test_state_changing_endpoint_requires_csrf(self):
        action_id = self.create_proposal()
        response = self.client.post(
            f"/api/actions/{action_id}/approve",
            json={},
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_integration_keeps_approval_waiting(self):
        action_id = self.create_proposal()
        self.assertEqual(self.approve(action_id).status_code, 200)
        response = self.client.post(
            f"/api/actions/{action_id}/execute",
            json={"payload": self.proposal()["proposed_payload"]},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.store.get_action(action_id)["status"], "APPROVED")
        self.assertEqual(
            self.store.get_audit_events(action_id)[-1]["event_type"],
            "EXECUTION_BLOCKED",
        )

    def test_ai_agent_page_renders_action_queue_button_and_separate_panel(self):
        response = self.client.get("/ai-agent")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        # 1. Action Queue button replaces Account Health label in w5 position
        self.assertIn('id="openQueueButton"', html)
        self.assertIn('class="word w5 action-queue-btn"', html)
        self.assertIn('id="queueBadge"', html)
        self.assertNotIn("Account Health</div>", html)

        # 2. Separate floating Action Queue panel
        self.assertIn('id="queuePanel"', html)
        self.assertIn('id="closeQueue"', html)
        self.assertIn('id="refreshQueue"', html)
        self.assertIn('id="queueLoading"', html)
        self.assertIn('id="queueStatus"', html)
        self.assertIn('id="actionQueue"', html)

        # 3. Customer brief panel does NOT contain the action queue
        agent_panel_match = re.search(r'<section class="agent-panel">(.*?)</section>', html, re.DOTALL)
        self.assertIsNotNone(agent_panel_match)
        agent_panel_html = agent_panel_match.group(1)
        self.assertIn('id="briefForm"', agent_panel_html)
        self.assertIn('id="queueProposal"', agent_panel_html)
        self.assertIn('id="proposalStatus"', agent_panel_html)
        self.assertNotIn('id="actionQueue"', agent_panel_html)
        self.assertNotIn('class="queue-divider"', agent_panel_html)

    def test_proposal_lifecycle_and_queue_listing(self):
        # Initial queue is empty
        res0 = self.client.get("/api/actions")
        self.assertEqual(res0.status_code, 200)
        self.assertEqual(res0.get_json()["count"], 0)

        # Create proposal
        action_id = self.create_proposal()
        self.assertIsNotNone(action_id)

        # Queue now has 1 pending action with real ID and enriched customer details
        res1 = self.client.get("/api/actions")
        self.assertEqual(res1.status_code, 200)
        data1 = res1.get_json()
        self.assertEqual(data1["count"], 1)
        action = data1["actions"][0]
        self.assertEqual(action["action_id"], action_id)
        self.assertEqual(action["status"], "PENDING")
        self.assertEqual(action["customer_name"], "Test Account")
        self.assertEqual(action["risk_level"], "Needs Review")

        # Approve action
        res_app = self.approve(action_id)
        self.assertEqual(res_app.status_code, 200)
        self.assertEqual(res_app.get_json()["action"]["status"], "APPROVED")

        # Queue reflects updated status
        res2 = self.client.get("/api/actions")
        self.assertEqual(res2.get_json()["actions"][0]["status"], "APPROVED")


if __name__ == "__main__":
    unittest.main()
