"""Persistent storage for customer outreach approval actions."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any


ACTION_STATUSES = (
    "PENDING",
    "APPROVED",
    "REJECTED",
    "EXECUTING",
    "COMPLETED",
    "FAILED",
    "ON_HOLD",
)

ACTION_TYPE = "customer_outreach_email"


class ActionConflict(Exception):
    """Raised when an action is no longer in the expected state."""


class ActionNotFound(Exception):
    """Raised when an action ID does not exist."""


def validate_email_action_payload(action_type: Any, payload: Any) -> dict:
    """Validate the exact payload supported by the initial email-draft flow."""
    if action_type != ACTION_TYPE or not isinstance(payload, dict):
        raise ValueError("Unsupported action type or payload.")

    if set(payload) != {"proposed_action", "explanation", "email"}:
        raise ValueError("The proposed payload contains unsupported fields.")

    for field, maximum in (("proposed_action", 1000), ("explanation", 4000)):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{field} must be a non-empty string of at most {maximum} characters.")

    email = payload.get("email")
    if not isinstance(email, dict) or set(email) != {"subject", "body"}:
        raise ValueError("Email payload must contain only a subject and body.")

    for field, maximum in (("subject", 300), ("body", 10000)):
        value = email.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"Email {field} must be non-empty and at most {maximum} characters.")

    return {
        "proposed_action": payload["proposed_action"].strip(),
        "explanation": payload["explanation"].strip(),
        "email": {
            "subject": email["subject"].strip(),
            "body": email["body"].strip(),
        },
    }


def _decode_json(value: Any) -> Any:
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value)
    return value


def _serialize(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return value.decode() if isinstance(value, (bytes, bytearray)) else value
    return value


class MySQLApprovalActionStore:
    """MySQL repository. The connection factory must be the existing admin DB."""

    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    @staticmethod
    def _record(row: dict | None) -> dict | None:
        if row is None:
            return None
        record = dict(row)
        for field in ("proposed_payload", "evidence_refs", "execution_result"):
            if record.get(field) is not None:
                record[field] = _decode_json(record[field])
        for field in (
            "created_at",
            "updated_at",
            "decided_at",
            "execution_started_at",
            "executed_at",
        ):
            if record.get(field) is not None:
                record[field] = _serialize(record[field])
        return record

    def list_actions(self, status: str | None = None) -> list[dict]:
        db = self.connection_factory()
        cursor = None
        try:
            cursor = db.cursor(dictionary=True)
            if status:
                cursor.execute(
                    """
                    SELECT * FROM approval_actions
                    WHERE status = %s
                    ORDER BY created_at DESC
                    LIMIT 200
                    """,
                    (status,),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM approval_actions
                    ORDER BY created_at DESC
                    LIMIT 200
                    """
                )
            return [self._record(row) for row in cursor.fetchall()]
        finally:
            if cursor:
                cursor.close()
            db.close()

    def get_action(self, action_id: str) -> dict | None:
        db = self.connection_factory()
        cursor = None
        try:
            cursor = db.cursor(dictionary=True)
            cursor.execute(
                "SELECT * FROM approval_actions WHERE action_id = %s",
                (action_id,),
            )
            return self._record(cursor.fetchone())
        finally:
            if cursor:
                cursor.close()
            db.close()

    def get_audit_events(self, action_id: str) -> list[dict]:
        db = self.connection_factory()
        cursor = None
        try:
            cursor = db.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT event_id, action_id, event_type, actor_admin_id,
                       from_status, to_status, reason, details, created_at
                FROM approval_action_audit
                WHERE action_id = %s
                ORDER BY created_at ASC, event_id ASC
                """,
                (action_id,),
            )
            events = cursor.fetchall()
            for event in events:
                if event.get("details") is not None:
                    event["details"] = _decode_json(event["details"])
                if event.get("created_at") is not None:
                    event["created_at"] = _serialize(event["created_at"])
            return events
        finally:
            if cursor:
                cursor.close()
            db.close()

    def _audit(
        self,
        cursor,
        action_id: str,
        event_type: str,
        actor_admin_id: int | None,
        from_status: str | None,
        to_status: str | None,
        reason: str | None = None,
        details: dict | None = None,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO approval_action_audit
                (action_id, event_type, actor_admin_id, from_status,
                 to_status, reason, details)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                action_id,
                event_type,
                actor_admin_id,
                from_status,
                to_status,
                reason,
                json.dumps(details) if details is not None else None,
            ),
        )

    def create_action(
        self,
        action_id: str,
        account_id: str,
        action_type: str,
        proposed_payload: dict,
        evidence_refs: list[str],
        actor_admin_id: int,
    ) -> dict:
        db = self.connection_factory()
        cursor = None
        try:
            cursor = db.cursor()
            cursor.execute(
                """
                INSERT INTO approval_actions
                    (action_id, account_id, action_type, proposed_payload,
                     evidence_refs, status)
                VALUES (%s, %s, %s, %s, %s, 'PENDING')
                """,
                (
                    action_id,
                    account_id,
                    action_type,
                    json.dumps(proposed_payload),
                    json.dumps(evidence_refs),
                ),
            )
            self._audit(
                cursor, action_id, "PROPOSED", actor_admin_id,
                None, "PENDING",
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            db.close()
        return self.get_action(action_id)

    def decide_action(
        self,
        action_id: str,
        decision: str,
        reviewer_id: int,
        reason: str | None = None,
    ) -> dict:
        next_status = "APPROVED" if decision == "approve" else "REJECTED"
        db = self.connection_factory()
        cursor = None
        try:
            cursor = db.cursor(dictionary=True)
            cursor.execute(
                "SELECT * FROM approval_actions WHERE action_id = %s FOR UPDATE",
                (action_id,),
            )
            action = cursor.fetchone()
            if action is None:
                raise ActionNotFound(action_id)
            if action["status"] != "PENDING":
                raise ActionConflict(action["status"])
            if decision == "approve":
                validate_email_action_payload(
                    action["action_type"],
                    _decode_json(action["proposed_payload"]),
                )
            cursor.execute(
                """
                UPDATE approval_actions
                SET status = %s, reviewer_id = %s, decision_reason = %s,
                    decided_at = CURRENT_TIMESTAMP(6)
                WHERE action_id = %s AND status = 'PENDING'
                """,
                (next_status, reviewer_id, reason, action_id),
            )
            if cursor.rowcount != 1:
                raise ActionConflict("Action was already decided.")
            self._audit(
                cursor,
                action_id,
                next_status,
                reviewer_id,
                "PENDING",
                next_status,
                reason,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            db.close()
        return self.get_action(action_id)

    def record_execution_blocked(self, action_id: str, actor_admin_id: int) -> None:
        db = self.connection_factory()
        cursor = None
        try:
            cursor = db.cursor(dictionary=True)
            cursor.execute(
                "SELECT status FROM approval_actions WHERE action_id = %s FOR UPDATE",
                (action_id,),
            )
            action = cursor.fetchone()
            if action is None:
                raise ActionNotFound(action_id)
            if action["status"] != "APPROVED":
                raise ActionConflict(action["status"])
            self._audit(
                cursor,
                action_id,
                "EXECUTION_BLOCKED",
                actor_admin_id,
                "APPROVED",
                "APPROVED",
                details={"error": "Integration not configured"},
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            db.close()

    def claim_execution(
        self, action_id: str, execution_payload: dict, actor_admin_id: int
    ) -> dict:
        db = self.connection_factory()
        cursor = None
        try:
            cursor = db.cursor(dictionary=True)
            cursor.execute(
                "SELECT * FROM approval_actions WHERE action_id = %s FOR UPDATE",
                (action_id,),
            )
            action = cursor.fetchone()
            if action is None:
                raise ActionNotFound(action_id)
            if action["status"] != "APPROVED":
                raise ActionConflict(action["status"])
            proposed_payload = _decode_json(action["proposed_payload"])
            validated_payload = validate_email_action_payload(
                action["action_type"], proposed_payload
            )
            if execution_payload != validated_payload:
                raise ValueError("Execution payload does not match the approved payload.")
            cursor.execute(
                """
                UPDATE approval_actions
                SET status = 'EXECUTING',
                    execution_started_at = CURRENT_TIMESTAMP(6)
                WHERE action_id = %s AND status = 'APPROVED'
                """,
                (action_id,),
            )
            if cursor.rowcount != 1:
                raise ActionConflict("Action execution was already claimed.")
            self._audit(
                cursor, action_id, "EXECUTION_STARTED", actor_admin_id,
                "APPROVED", "EXECUTING",
            )
            db.commit()
            action["status"] = "EXECUTING"
            action["proposed_payload"] = validated_payload
            return self._record(action)
        except Exception:
            db.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            db.close()

    def finish_execution(
        self,
        action_id: str,
        status: str,
        result: dict,
        actor_admin_id: int,
    ) -> dict:
        if status not in ("COMPLETED", "FAILED"):
            raise ValueError("Invalid execution result status.")
        db = self.connection_factory()
        cursor = None
        try:
            cursor = db.cursor(dictionary=True)
            cursor.execute(
                "SELECT status FROM approval_actions WHERE action_id = %s FOR UPDATE",
                (action_id,),
            )
            action = cursor.fetchone()
            if action is None:
                raise ActionNotFound(action_id)
            if action["status"] != "EXECUTING":
                raise ActionConflict(action["status"])
            cursor.execute(
                """
                UPDATE approval_actions
                SET status = %s, execution_result = %s,
                    executed_at = CURRENT_TIMESTAMP(6)
                WHERE action_id = %s AND status = 'EXECUTING'
                """,
                (status, json.dumps(result), action_id),
            )
            self._audit(
                cursor, action_id, status, actor_admin_id,
                "EXECUTING", status, details=result,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            db.close()
        return self.get_action(action_id)
