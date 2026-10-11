"""Authenticated API for persisted approval and action workflows."""

import re
import uuid
from functools import wraps

import mysql.connector
from flask import Blueprint, current_app, jsonify, request, session
from flask_wtf.csrf import validate_csrf
from wtforms.validators import ValidationError

from routes.dashboard import get_admin_db, read_output_csv
from services.approval_actions import (
    ACTION_STATUSES,
    ACTION_TYPE,
    ActionConflict,
    ActionNotFound,
    MySQLApprovalActionStore,
    validate_email_action_payload,
)


approval_bp = Blueprint("approval_actions", __name__)
ACCOUNT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _store():
    return current_app.config.get("APPROVAL_ACTION_STORE") or MySQLApprovalActionStore(
        get_admin_db
    )


def _reviewer_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "admin_id" not in session or session.get("role") != "admin":
            return jsonify({"error": "An authenticated admin reviewer is required."}), 401
        return view(*args, **kwargs)

    return wrapped


def _csrf_error():
    try:
        validate_csrf(request.headers.get("X-CSRFToken", ""))
    except ValidationError:
        return jsonify({"error": "CSRF validation failed. Refresh the page and retry."}), 400
    return None


def _json_object():
    if not request.is_json:
        return None
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _account_context():
    rows = read_output_csv("account_risk_insights.csv")
    if rows is None:
        return None, "Account risk data is unavailable."
    return rows, None


def _evidence_references(account_id):
    rows = read_output_csv("account_evidence.csv")
    if rows is None:
        return None
    references = set()
    for row in rows:
        if row.get("account_id") == account_id:
            references.update(
                value.strip()
                for value in str(row.get("source_record_id", "")).split(";")
                if value.strip()
            )
    return references


def _account_metadata(account_id, risk_rows):
    account = next(
        (row for row in risk_rows if row.get("account_id") == account_id),
        {},
    )
    return {
        "customer_name": account.get("company_name") or "Unknown customer",
        "risk_level": account.get("risk_level") or "Unknown",
    }


def _enrich_action(action, risk_rows):
    return {**action, **_account_metadata(action["account_id"], risk_rows)}


def _database_error():
    current_app.logger.exception("Approval action storage request failed.")
    return jsonify({
        "error": "Approval storage is unavailable. Confirm MySQL connectivity and apply the approval-action migration."
    }), 503


@approval_bp.get("/api/actions")
@_reviewer_required
def list_actions():
    status = request.args.get("status")
    if status and status not in ACTION_STATUSES:
        return jsonify({"error": "Invalid action status."}), 400
    risk_rows, error = _account_context()
    try:
        actions = _store().list_actions(status=status)
    except mysql.connector.Error:
        return _database_error()
    return jsonify({
        "count": len(actions),
        "actions": [_enrich_action(action, risk_rows or []) for action in actions],
        "integration_configured": callable(current_app.config.get("ACTION_EXECUTOR")),
    }), 200


@approval_bp.get("/api/actions/<action_id>")
@_reviewer_required
def get_action(action_id):
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", action_id):
        return jsonify({"error": "Invalid action ID."}), 400
    risk_rows, _ = _account_context()
    try:
        store = _store()
        action = store.get_action(action_id)
        audit_events = store.get_audit_events(action_id) if action else []
    except mysql.connector.Error:
        return _database_error()
    if action is None:
        return jsonify({"error": "Action not found."}), 404
    return jsonify({
        "action": _enrich_action(action, risk_rows or []),
        "audit_events": audit_events,
    }), 200


@approval_bp.post("/api/actions")
@_reviewer_required
def create_action():
    csrf_response = _csrf_error()
    if csrf_response:
        return csrf_response
    data = _json_object()
    if data is None or set(data) != {
        "account_id", "action_type", "proposed_payload", "evidence_refs"
    }:
        return jsonify({"error": "Invalid proposal request."}), 400

    account_id = data.get("account_id")
    if not isinstance(account_id, str) or not ACCOUNT_ID_PATTERN.fullmatch(account_id):
        return jsonify({"error": "Please provide a valid account ID."}), 400
    risk_rows, error = _account_context()
    if error:
        return jsonify({"error": error}), 503
    if not any(row.get("account_id") == account_id for row in risk_rows):
        return jsonify({"error": "Account not found in current risk data."}), 404

    try:
        payload = validate_email_action_payload(
            data.get("action_type"), data.get("proposed_payload")
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    evidence_refs = data.get("evidence_refs")
    if not isinstance(evidence_refs, list) or len(evidence_refs) > 50:
        return jsonify({"error": "Evidence references must be a list of at most 50 IDs."}), 400
    if any(
        not isinstance(reference, str)
        or not reference.strip()
        or len(reference) > 200
        for reference in evidence_refs
    ):
        return jsonify({"error": "Invalid evidence reference."}), 400
    evidence_refs = list(dict.fromkeys(reference.strip() for reference in evidence_refs))
    valid_refs = _evidence_references(account_id)
    if valid_refs is None:
        return jsonify({"error": "Account evidence data is unavailable."}), 503
    if not set(evidence_refs).issubset(valid_refs):
        return jsonify({"error": "Evidence references do not belong to this account."}), 400

    try:
        action = _store().create_action(
            action_id=str(uuid.uuid4()),
            account_id=account_id,
            action_type=ACTION_TYPE,
            proposed_payload=payload,
            evidence_refs=evidence_refs,
            actor_admin_id=session["admin_id"],
        )
    except mysql.connector.Error:
        return _database_error()
    return jsonify({
        "action": _enrich_action(action, risk_rows),
        "message": "Proposal saved for review. No email was sent and no CRM changes were made.",
    }), 201


def _decide(action_id, decision):
    csrf_response = _csrf_error()
    if csrf_response:
        return csrf_response
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", action_id):
        return jsonify({"error": "Invalid action ID."}), 400
    data = _json_object()
    if data is None:
        return jsonify({"error": "A JSON object is required."}), 400
    allowed_keys = {"reason"} if decision == "reject" else set()
    if set(data) - allowed_keys:
        return jsonify({"error": "Unexpected decision fields."}), 400

    reason = data.get("reason") if decision == "reject" else None
    if decision == "reject" and (
        not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 2000
    ):
        return jsonify({"error": "A rejection reason of 1 to 2000 characters is required."}), 400
    if decision == "approve" and "reason" in data:
        return jsonify({"error": "Unexpected decision fields."}), 400
    try:
        action = _store().decide_action(
            action_id,
            decision,
            session["admin_id"],
            reason.strip() if isinstance(reason, str) else None,
        )
    except ActionNotFound:
        return jsonify({"error": "Action not found."}), 404
    except ActionConflict as exc:
        return jsonify({"error": f"Action cannot be {decision}d from its current status ({exc})."}), 409
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except mysql.connector.Error:
        return _database_error()
    risk_rows, _ = _account_context()
    status_message = (
        "Approved — awaiting integration/execution."
        if decision == "approve"
        else "Rejected. This action cannot be executed."
    )
    return jsonify({
        "action": _enrich_action(action, risk_rows or []),
        "message": status_message,
    }), 200


@approval_bp.post("/api/actions/<action_id>/approve")
@_reviewer_required
def approve_action(action_id):
    return _decide(action_id, "approve")


@approval_bp.post("/api/actions/<action_id>/reject")
@_reviewer_required
def reject_action(action_id):
    return _decide(action_id, "reject")


@approval_bp.route("/api/actions/<action_id>/email", methods=["PUT", "PATCH"])
@_reviewer_required
def update_action_email(action_id):
    csrf_response = _csrf_error()
    if csrf_response:
        return csrf_response
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", action_id):
        return jsonify({"error": "Invalid action ID."}), 400
    data = _json_object()
    if data is None:
        return jsonify({"error": "A JSON object is required."}), 400

    email_data = data.get("email") if isinstance(data.get("email"), dict) else data
    subject = email_data.get("subject")
    body = email_data.get("body")

    if not isinstance(subject, str) or not subject.strip() or len(subject.strip()) > 300:
        return jsonify({"error": "Email subject must be a non-empty string of at most 300 characters."}), 400
    if not isinstance(body, str) or not body.strip() or len(body.strip()) > 10000:
        return jsonify({"error": "Email body must be a non-empty string of at most 10000 characters."}), 400

    try:
        action = _store().update_email_draft(
            action_id=action_id,
            email={"subject": subject.strip(), "body": body.strip()},
            editor_admin_id=session["admin_id"],
        )
    except ActionNotFound:
        return jsonify({"error": "Action not found."}), 404
    except ActionConflict as exc:
        return jsonify({"error": f"Action cannot be edited from its current status ({exc})."}), 409
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except mysql.connector.Error:
        return _database_error()

    risk_rows, _ = _account_context()
    return jsonify({
        "action": _enrich_action(action, risk_rows or []),
        "message": "Email draft updated successfully. Please review before approval.",
    }), 200


@approval_bp.post("/api/actions/<action_id>/execute")
@_reviewer_required
def execute_action(action_id):
    csrf_response = _csrf_error()
    if csrf_response:
        return csrf_response
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", action_id):
        return jsonify({"error": "Invalid action ID."}), 400
    data = _json_object()
    if data is None or set(data) != {"payload"} or not isinstance(data["payload"], dict):
        return jsonify({"error": "The exact approved payload is required."}), 400

    try:
        execution_payload = validate_email_action_payload(
            ACTION_TYPE, data["payload"]
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    executor = current_app.config.get("ACTION_EXECUTOR")
    if not callable(executor):
        try:
            _store().record_execution_blocked(action_id, session["admin_id"])
        except ActionNotFound:
            return jsonify({"error": "Action not found."}), 404
        except ActionConflict as exc:
            return jsonify({"error": f"Action is not approved for execution ({exc})."}), 409
        except mysql.connector.Error:
            return _database_error()
        return jsonify({
            "error": "Integration not configured. The action remains approved and has not been executed.",
            "status": "APPROVED",
        }), 503

    try:
        action = _store().claim_execution(
            action_id, execution_payload, session["admin_id"]
        )
    except ActionNotFound:
        return jsonify({"error": "Action not found."}), 404
    except ActionConflict as exc:
        return jsonify({"error": f"Action is not available for execution ({exc})."}), 409
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except mysql.connector.Error:
        return _database_error()

    try:
        result = executor(action)
        if not isinstance(result, dict) or result.get("verified") is not True:
            raise RuntimeError("Executor did not verify the operation.")
    except Exception as exc:
        current_app.logger.exception("Action execution failed for %s.", action_id)
        failure = {
            "error": "Integration execution failed.",
            "error_type": type(exc).__name__,
        }
        try:
            action = _store().finish_execution(
                action_id, "FAILED", failure, session["admin_id"]
            )
        except mysql.connector.Error:
            return _database_error()
        return jsonify({
            "error": "Execution failed and was recorded.",
            "action": action,
        }), 502

    try:
        action = _store().finish_execution(
            action_id, "COMPLETED", result, session["admin_id"]
        )
    except mysql.connector.Error:
        return _database_error()
    return jsonify({"action": action, "message": "Execution verified."}), 200
