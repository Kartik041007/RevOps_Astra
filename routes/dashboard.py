
import csv
from datetime import date, datetime
import json
import os
from pathlib import Path
from typing import Any
from flask import Blueprint, jsonify, request, render_template,session, redirect, url_for, flash,current_app
from flask_wtf.csrf import generate_csrf

from flask_wtf import FlaskForm
import mysql
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Length, EqualTo
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import mysql.connector
from services.db import get_db_connection

def get_admin_db():
    return get_db_connection()


def _load_env_file():
    import sys
    if "unittest" in sys.modules:
        return
    env_file = Path(__file__).resolve().parent.parent / ".env"
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


def _get_active_crm_record(customer_id: str) -> tuple[dict[str, Any] | None, str]:
    """Retrieve customer CRM record using the active provider mode (mysql or mock)."""
    _load_env_file()
    provider = os.environ.get("CRM_PROVIDER", "mock").strip().lower()
    if provider == "mysql":
        try:
            from services.customer_repository import CustomerRepository
            repo = CustomerRepository()
            rec = repo.get_customer_record(customer_id)
            if rec:
                return rec, "mysql"
            return None, "mysql"
        except Exception as exc:
            current_app.logger.debug("MySQL CRM lookup failed for %s: %s", customer_id, exc)
            return None, "mysql"
    try:
        from mcp_server.mock_crm_db import mock_crm_db
        rec = mock_crm_db.get_customer(customer_id)
        if rec:
            return rec, "mock"
    except Exception:
        pass
    return None, provider


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = PROJECT_ROOT / "outputs"


def read_output_json(filename):
    file_path = OUTPUTS_DIR / filename
    if not file_path.exists():
        return None
    with file_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def read_output_csv(filename):
    file_path = OUTPUTS_DIR / filename
    if not file_path.exists():
        return None
    with file_path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def _get_verified_crm_updates(customer_id: str) -> dict[str, Any] | None:
    """Retrieve verified MCP execution details and receipt for a specific customer.

    Returns a dictionary of execution receipt details ONLY if a completed,
    read-back-verified CRM action exists for this exact customer_id.
    Returns None if:
    - No action exists
    - Action is only proposed (PENDING), approved (unexecuted), rejected, or failed
    - No real verified CRM mutation has been executed for this account
    """
    if not customer_id or not isinstance(customer_id, str):
        return None
    cleaned_id = customer_id.strip()

    verified_action = None
    try:
        from routes.approval_actions import _store
        store = _store()
        actions = store.list_actions()
        matching_actions = [
            a for a in actions
            if str(a.get("account_id", "")).strip() == cleaned_id
        ]
        for a in matching_actions:
            status = str(a.get("status", "")).upper()
            if status not in ("COMPLETED", "EXECUTED"):
                continue
            exec_res = a.get("execution_result")
            if isinstance(exec_res, (str, bytes, bytearray)):
                try:
                    exec_res = json.loads(exec_res)
                except Exception:
                    exec_res = None
            if isinstance(exec_res, dict) and exec_res.get("verified") is True:
                if verified_action is None:
                    verified_action = (a, exec_res)
                else:
                    existing_dt = str(verified_action[0].get("executed_at") or verified_action[0].get("created_at") or "")
                    new_dt = str(a.get("executed_at") or a.get("created_at") or "")
                    if new_dt >= existing_dt:
                        verified_action = (a, exec_res)
    except Exception as exc:
        current_app.logger.debug("Failed querying approval action store for %s: %s", cleaned_id, exc)

    if not verified_action:
        return None

    action_record, exec_res = verified_action

    provider = os.environ.get("CRM_PROVIDER", "mock").strip().lower()
    mode = exec_res.get("mode") or provider
    integration = exec_res.get("integration") or ("mcp_mysql_crm" if mode == "mysql" else "mcp_mock_crm")

    audit_event = None
    if mode == "mysql":
        try:
            from services.db import get_db_connection
            conn = get_db_connection()
            cur = conn.cursor(dictionary=True)
            action_id = action_record.get("action_id")
            if action_id:
                cur.execute(
                    """
                    SELECT log_id, action_id, actor, event_type, changes, message, created_at
                    FROM audit_logs
                    WHERE account_id = %s AND action_id = %s AND event_type = 'CRM_RECORD_UPDATED'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (cleaned_id, action_id),
                )
                audit_event = cur.fetchone()
            if not audit_event:
                cur.execute(
                    """
                    SELECT log_id, action_id, actor, event_type, changes, message, created_at
                    FROM audit_logs
                    WHERE account_id = %s AND event_type = 'CRM_RECORD_UPDATED' AND actor = 'MCP_SERVER'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (cleaned_id,),
                )
                audit_event = cur.fetchone()
            cur.close()
            conn.close()
            if audit_event and isinstance(audit_event.get("created_at"), (datetime, date)):
                audit_event["created_at"] = audit_event["created_at"].isoformat()
        except Exception as exc:
            current_app.logger.debug("Failed to query audit_logs for %s: %s", cleaned_id, exc)

    executed_at = action_record.get("executed_at")
    if hasattr(executed_at, "isoformat"):
        executed_at = executed_at.isoformat()

    return {
        "action_id": action_record.get("action_id"),
        "status": action_record.get("status", "COMPLETED"),
        "verified": True,
        "executed_at": executed_at,
        "applied_updates": exec_res.get("applied_updates") or {},
        "integration": integration,
        "mode": mode,
        "verified_record": exec_res.get("verified_record") or {},
        "message": exec_res.get("message"),
        "audit_event": audit_event,
    }


class registerform(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    username = StringField(
        "Username",
        validators=[DataRequired(), Length(min=3, max=50)]
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=8)]
    )
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[
            DataRequired(),
            EqualTo("password", message="Passwords must match")
        ]
    )
    submit = SubmitField("Register")


class loginform(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Login")


dashboard_bp = Blueprint("dashboard", __name__)

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "admin_id" not in session:
            return redirect(url_for("dashboard.login"))
        return view(*args, **kwargs)
    return wrapped


# ---------------- REGISTER ----------------
@dashboard_bp.route("/register", methods=["GET", "POST"])
def register():
    form = registerform()

    if form.validate_on_submit():
        name = form.name.data.strip()
        username = form.username.data.strip()
        password = form.password.data

        db = None
        cursor = None

        try:
            db = get_admin_db()
            cursor = db.cursor()

            cursor.execute(
                """
                INSERT INTO admins (name, username, password_hash)
                VALUES (%s, %s, %s)
                """,
                (
                    name,
                    username,
                    generate_password_hash(password)
                )
            )
            db.commit()

            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("dashboard.login"))

        except mysql.connector.IntegrityError:
            if db:
                db.rollback()
            flash("That username already exists.", "error")

        except mysql.connector.Error:
            if db:
                db.rollback()
            flash("Database error. Please try again.", "error")

        finally:
            if cursor:
                cursor.close()
            if db and db.is_connected():
                db.close()

    return render_template("register.html", form=form)


# ---------------- LOGIN ----------------
@dashboard_bp.route("/", methods=["GET", "POST"])
def login():
    form = loginform()

    if form.validate_on_submit():
        username = form.username.data.strip()
        password = form.password.data

        db = None
        cursor = None

        try:
            db = get_admin_db()
            cursor = db.cursor(dictionary=True)

            cursor.execute(
                """
                SELECT id, name, username, password_hash
                FROM admins
                WHERE username = %s
                """,
                (username,)
            )
            admin = cursor.fetchone()

            if admin and check_password_hash(
                admin["password_hash"], password
            ):
                session.clear()
                session["admin_id"] = admin["id"]
                session["admin_name"] = admin["name"]
                session["role"] = "admin"

                flash("Login successful.", "success")
                return redirect(url_for("dashboard.dashboard_page"))

            flash("Invalid username or password.", "error")

        except mysql.connector.Error:
            flash("Database connection failed.", "error")

        finally:
            if cursor:
                cursor.close()
            if db and db.is_connected():
                db.close()

    return render_template("login.html", form=form)


# ---------------- LOGOUT ----------------
@dashboard_bp.route("/logout", methods=["POST"])
@admin_required
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("dashboard.login"))

@dashboard_bp.route("/api/health", methods=["GET"])
def health_check():
    return jsonify({
        "status": "ok",
        "message": "RevOps Astra Flask API is running"
    }), 200
@dashboard_bp.route("/api/risk-queue", methods=["GET"])
def risk_queue():
    data = read_output_json("dashboard_summary.json")

    if data is None:
        return jsonify({"error": "Dashboard summary file not found"}), 404

    accounts = data.get("priority_accounts", data.get("top_priority_accounts", []))

    limit = request.args.get("limit", default=20, type=int)
    if not 1 <= limit <= 100:
        return jsonify({"error": "limit must be between 1 and 100"}), 400

    return jsonify({
        "count": len(accounts),
        "accounts": accounts[:limit]
    }), 200
@dashboard_bp.route("/api/customers/<customer_id>", methods=["GET"])
def customer_detail(customer_id):
    account_obj = None

    # 1. First look in dashboard_summary priority accounts
    summary_data = read_output_json("dashboard_summary.json")
    if summary_data:
        priority_accounts = summary_data.get("priority_accounts", summary_data.get("top_priority_accounts", []))
        for acc in priority_accounts:
            if acc.get("account_id") == customer_id:
                account_obj = dict(acc)
                break

    # 2. If not found in priority accounts, fall back to account_features.csv + risk insights
    if account_obj is None:
        feature_rows = read_output_csv("account_features.csv") or []
        for row in feature_rows:
            if row.get("account_id") == customer_id:
                account_obj = dict(row)
                break

        if account_obj is not None:
            insight_rows = read_output_csv("account_risk_insights.csv") or []
            for insight in insight_rows:
                if insight.get("account_id") == customer_id:
                    for k, v in insight.items():
                        if v not in (None, "") and k not in account_obj:
                            account_obj[k] = v
                    break

    # 3. If still not found, check active CRM store
    if account_obj is None:
        try:
            crm_rec, _ = _get_active_crm_record(customer_id)
            if crm_rec:
                account_obj = {
                    "account_id": customer_id,
                    "company_name": crm_rec.get("company_name", "Unknown"),
                    "industry": crm_rec.get("industry", "Unknown"),
                    "region": crm_rec.get("region", "Unknown"),
                    "account_owner": crm_rec.get("account_owner", "Unassigned"),
                    "risk_level": crm_rec.get("risk_level", "Unknown"),
                    "risk_index": 100 - int(crm_rec.get("health_score", 50)),
                }
        except Exception:
            pass

    if account_obj is None:
        return jsonify({"error": f"Customer account '{customer_id}' not found"}), 404

    # Merge latest CRM record (ordinary attributes)
    try:
        crm_record, provider_mode = _get_active_crm_record(customer_id)
        if crm_record:
            account_obj["crm_record"] = crm_record
            account_obj["crm_provider"] = provider_mode
            # Overlay CRM fields onto account object
            if "notes" in crm_record:
                account_obj["notes"] = crm_record["notes"]
            if "customer_status" in crm_record:
                account_obj["customer_status"] = crm_record["customer_status"]
                account_obj["status"] = crm_record["customer_status"]
            if "owner_email" in crm_record:
                account_obj["owner_email"] = crm_record["owner_email"]
    except Exception:
        pass

    # Check verified CRM mutation
    try:
        verified_update = _get_verified_crm_updates(customer_id)
        account_obj["verified_update"] = verified_update
        account_obj["has_verified_mutation"] = bool(verified_update)
        account_obj["crm_synced"] = bool(verified_update)
        account_obj["mock_crm_synced"] = bool(verified_update)
    except Exception:
        account_obj["verified_update"] = None
        account_obj["has_verified_mutation"] = False
        account_obj["crm_synced"] = False
        account_obj["mock_crm_synced"] = False

    return jsonify(account_obj), 200

@dashboard_bp.route("/customers", methods=["GET"])
def customers_page():
    return render_template("customers.html")

@dashboard_bp.route("/api/customers", methods=["GET"])
def all_customers():
    rows = read_output_csv("account_features.csv")

    if rows is None:
        return jsonify({"error": "account_features.csv not found"}), 404

    summary = read_output_json("dashboard_summary.json") or {}
    risk_rows = read_output_csv("account_risk_insights.csv") or []

    # Index risk insights by account ID.
    risk_by_id = {}
    for row in risk_rows:
        account_id = row.get("account_id")
        if account_id:
            risk_by_id.setdefault(account_id, {}).update(row)

    # Check mock CRM updates if any
    crm_updates = {}
    try:
        from mcp_server.mock_crm_db import MOCK_UPDATES_FILE
        if MOCK_UPDATES_FILE.exists():
            with open(MOCK_UPDATES_FILE, mode="r", encoding="utf-8") as f:
                crm_updates = json.load(f) or {}
    except Exception:
        pass

    # Add risk information and mock CRM update badges where available.
    for row in rows:
        acc_id = row.get("account_id")
        risk = risk_by_id.get(acc_id, {})
        row.update({
            key: value
            for key, value in risk.items()
            if value not in (None, "")
        })
        if acc_id and acc_id in crm_updates:
            row["mock_crm_updated"] = True
            row["mock_crm_fields"] = crm_updates[acc_id]

    # Summary risk levels are available for the portfolio.
    risk_counts = summary.get("accounts_by_risk_level", {})

    return jsonify({
        "count": len(rows),
        "risk_counts": risk_counts,
        "customers": rows
    }), 200


@dashboard_bp.route("/dashboard", methods=["GET"])
def dashboard_page():
    return render_template("index.html")

@dashboard_bp.route("/api/summary", methods=["GET"])
def summary():
    data = read_output_json("dashboard_summary.json")

    if data is None:
        return jsonify({
            "error": "Dashboard summary file not found"
        }), 404

    return jsonify(data)


@dashboard_bp.route("/api/charts", methods=["GET"])
def charts():
    data = read_output_json("dashboard_charts.json")

    if data is None:
        return jsonify({
            "error": "Dashboard charts file not found"
        }), 404

    return jsonify(data)



@dashboard_bp.route("/api/customers/<customer_id>/impact", methods=["GET"])
def customer_impact(customer_id):
    feature_rows = read_output_csv("account_features.csv")
    evidence_rows = read_output_csv("account_evidence.csv")
    insight_rows = read_output_csv("account_risk_insights.csv")
    summary_data = read_output_json("dashboard_summary.json") or {}

    if feature_rows is None or evidence_rows is None:
        return jsonify({
            "error": "Required customer feature or evidence file is missing"
        }), 404

    account = next(
        (row for row in feature_rows
         if row.get("account_id") == customer_id),
        None
    )

    if account is None:
        return jsonify({"error": "Customer account not found"}), 404

    matching_evidence = [
        row for row in evidence_rows
        if row.get("account_id") == customer_id
    ]

    matching_insights = [
        row for row in (insight_rows or [])
        if row.get("account_id") == customer_id
    ]

    # Add risk fields from insights when they exist.
    for insight in matching_insights:
        for key, value in insight.items():
            if value not in (None, "") and key not in account:
                account[key] = value

    # Use summary scores only when the account is present there.
    priority_accounts = (
        summary_data.get("top_priority_accounts")
        or summary_data.get("priority_accounts")
        or []
    )

    priority_account = next(
        (row for row in priority_accounts
         if row.get("account_id") == customer_id),
        None
    )

    if priority_account:
        for key, value in priority_account.items():
            if value not in (None, ""):
                account[key] = value

    try:
        crm_record, provider_mode = _get_active_crm_record(customer_id)
        if crm_record:
            account["crm_record"] = crm_record
            account["crm_provider"] = provider_mode
            if "notes" in crm_record:
                account["notes"] = crm_record["notes"]
            if "customer_status" in crm_record:
                account["customer_status"] = crm_record["customer_status"]
                account["status"] = crm_record["customer_status"]
            if "owner_email" in crm_record:
                account["owner_email"] = crm_record["owner_email"]
    except Exception:
        pass

    try:
        verified_update = _get_verified_crm_updates(customer_id)
        account["verified_update"] = verified_update
        account["has_verified_mutation"] = bool(verified_update)
        account["crm_synced"] = bool(verified_update)
        account["mock_crm_synced"] = bool(verified_update)
    except Exception:
        account["verified_update"] = None
        account["has_verified_mutation"] = False
        account["crm_synced"] = False
        account["mock_crm_synced"] = False

    return jsonify({
        "account_id": customer_id,
        "account": account,
        "evidence": matching_evidence,
        "insights": matching_insights,
        "evidence_count": len(matching_evidence),
        "insight_count": len(matching_insights)
    }), 200

@dashboard_bp.route("/ai-agent", methods=["GET"])
def ai_agent_page():
    return render_template("ai_agent_3d.html", csrf_token=generate_csrf())

@dashboard_bp.route("/api/ai-agent/brief", methods=["POST"])
@admin_required
def ai_agent_brief_api():
    data = request.get_json(silent=True) or {}
    account_id = str(data.get("account_id", "")).strip()

    if not account_id or len(account_id) > 64:
        return jsonify({
            "success": False,
            "error": "Please provide a valid account ID."
        }), 400

    try:
        from ai.llm_service import generate_customer_brief

        brief = generate_customer_brief(account_id)

        # Layer in active approval actions for this account so the brief reflects current queue state
        try:
            from routes.approval_actions import _store
            store = _store()
            actions = store.list_actions()
            account_actions = [a for a in actions if a.get("account_id") == account_id]
            if account_actions:
                account_actions.sort(
                    key=lambda a: {
                        "COMPLETED": 4, "EXECUTED": 4, "APPROVED": 3, "PENDING": 2, "REJECTED": 1
                    }.get(a.get("status", ""), 0),
                    reverse=True
                )
                latest = account_actions[0]
                brief["latest_action"] = {
                    "action_id": latest.get("action_id"),
                    "status": latest.get("status"),
                    "action_type": latest.get("action_type"),
                    "proposed_payload": latest.get("proposed_payload"),
                    "execution_result": latest.get("execution_result"),
                }
                if latest.get("status") in ("COMPLETED", "EXECUTED"):
                    brief["crm_updated"] = True
                    brief["approval_required"] = False
                elif latest.get("status") == "APPROVED":
                    brief["approval_required"] = False
        except Exception as e:
            current_app.logger.debug("Could not query store for account actions: %s", e)

        # Layer in latest CRM record if available and sync verified fields to customer block
        try:
            crm_record, provider_mode = _get_active_crm_record(account_id)
            if crm_record:
                brief["crm_record"] = crm_record
                brief["crm_provider"] = provider_mode
                customer_dict = brief.setdefault("customer", {})
                if "notes" in crm_record:
                    customer_dict["notes"] = crm_record["notes"]
                if "customer_status" in crm_record:
                    customer_dict["customer_status"] = crm_record["customer_status"]
                    customer_dict["status"] = crm_record["customer_status"]
                if "owner_email" in crm_record:
                    customer_dict["owner_email"] = crm_record["owner_email"]

            verified_update = _get_verified_crm_updates(account_id)
            brief["verified_update"] = verified_update
            brief["has_verified_mutation"] = bool(verified_update)
            brief["crm_synced"] = bool(verified_update)
            brief["mock_crm_synced"] = bool(verified_update)
        except Exception as e:
            current_app.logger.debug("Could not attach CRM record or verified update to brief: %s", e)

        return jsonify({
            "success": True,
            "brief": brief
        }), 200

    except ValueError as exc:
        return jsonify({
            "success": False,
            "error": str(exc)
        }), 404

    except (RuntimeError, FileNotFoundError) as exc:
        current_app.logger.warning("ASTRA AI unavailable: %s", exc)

        return jsonify({
            "success": False,
            "error": str(exc)
        }), 503

    except Exception:
        current_app.logger.exception(
            "Unexpected ASTRA AI brief generation error"
        )

        return jsonify({
            "success": False,
            "error": "Could not generate the AI brief. Check the Flask terminal."
        }), 500

@dashboard_bp.route("/customer-impact", methods=["GET"])
def customer_impact_page():
    return render_template("customer_impact.html")

@dashboard_bp.route("/customers/<customer_id>", methods=["GET"])
def customer_page(customer_id):
    return render_template("customer_impact.html")