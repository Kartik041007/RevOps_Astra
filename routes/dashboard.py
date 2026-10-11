
import json
from pathlib import Path
from flask import Blueprint, jsonify, request, render_template,session, redirect, url_for, flash,current_app

from flask_wtf import FlaskForm
import mysql
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Length, EqualTo
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import mysql.connector
def get_admin_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="1234",  # Apna actual MySQL password
        database="RevOps_Astra"
    )


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
    data = read_output_json("dashboard_summary.json")

    if data is None:
        return jsonify({"error": "Dashboard summary file not found"}), 404

    accounts = data.get("priority_accounts", data.get("top_priority_accounts", []))

    for account in accounts:
        if account.get("account_id") == customer_id:
            return jsonify(account), 200

    return jsonify({"error": "Account not found in priority accounts"}), 404

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

    # Add risk information where available.
    for row in rows:
        risk = risk_by_id.get(row.get("account_id"), {})
        row.update({
            key: value
            for key, value in risk.items()
            if value not in (None, "")
        })

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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = PROJECT_ROOT / "outputs"


def read_output_json(filename):
    file_path = OUTPUTS_DIR / filename

    if not file_path.exists():
        return None

    with file_path.open("r", encoding="utf-8") as file:
        return json.load(file)


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

import csv


def read_output_csv(filename):
    file_path = OUTPUTS_DIR / filename

    if not file_path.exists():
        return None

    with file_path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))



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
    return render_template("ai_agent_3d.html")

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