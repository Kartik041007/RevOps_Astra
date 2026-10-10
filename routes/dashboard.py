
import json
from pathlib import Path
from flask import Blueprint, jsonify, request, render_template

dashboard_bp = Blueprint("dashboard", __name__)
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
