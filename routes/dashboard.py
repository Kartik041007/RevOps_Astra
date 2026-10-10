
import json
from pathlib import Path
from flask import Blueprint, jsonify, request, render_template

dashboard_bp = Blueprint("dashboard", __name__)

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
