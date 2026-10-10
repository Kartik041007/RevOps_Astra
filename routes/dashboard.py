
from flask import Blueprint, jsonify, request, render_template

from services.analytics import (
    load_customers,
    get_summary,
    get_churn_distribution,
    get_risk_distribution,
    get_revenue_analysis,
    get_customer_detail,
    get_risk_queue,
)

dashboard_bp = Blueprint("dashboard", __name__)


def read_data():
    try:
        return load_customers(), None
    except (OSError, ValueError, UnicodeError) as exc:
        return None, str(exc)


def json_safe(value):
    """Convert pandas/numpy values to JSON-safe Python values."""
    import json
    import math

    def clean(item):
        if hasattr(item, "item"):
            item = item.item()
        if isinstance(item, dict):
            return {str(k): clean(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(v) for v in item]
        if isinstance(item, float) and not math.isfinite(item):
            return None
        return item

    return jsonify(clean(value))


@dashboard_bp.get("/")
def home():
    return render_template("index.html")


@dashboard_bp.get("/api/health")
def health():
    return jsonify({"status": "ok", "project": "RevOps Astra"})


@dashboard_bp.get("/api/summary")
def summary():
    df, error = read_data()
    if error:
        return jsonify({"error": error}), 500

    result = get_summary(df)
    if not result["data_available"]:
        return jsonify({
            **result,
            "message": "Processed dataset has not been added yet."
        }), 200

    return jsonify(result)


@dashboard_bp.get("/api/churn-distribution")
def churn_distribution():
    df, error = read_data()
    if error:
        return jsonify({"error": error}), 500
    return json_safe(get_churn_distribution(df))


@dashboard_bp.get("/api/risk-distribution")
def risk_distribution():
    df, error = read_data()
    if error:
        return jsonify({"error": error}), 500
    return json_safe(get_risk_distribution(df))


@dashboard_bp.get("/api/revenue-analysis")
def revenue_analysis():
    df, error = read_data()
    if error:
        return jsonify({"error": error}), 500
    return json_safe(get_revenue_analysis(df))


@dashboard_bp.get("/api/customers")
def customers():
    df, error = read_data()
    if error:
        return jsonify({"error": error}), 500

    if df.empty:
        return jsonify({
            "data_available": False,
            "items": [],
            "page": 1,
            "limit": 20,
            "total": 0,
        })

    # Optional filters
    search = request.args.get("search", "").strip().lower()
    risk = request.args.get("risk", "").strip().upper()
    segment = request.args.get("segment", "").strip()

    if search:
        searchable = df.astype(str).apply(
            lambda col: col.str.lower().str.contains(
                search, regex=False, na=False
            )
        ).any(axis=1)
        df = df[searchable]

    if risk and "risk_level" in df:
        df = df[
            df["risk_level"].astype(str).str.upper() == risk
        ]

    if segment and "segment" in df:
        df = df[df["segment"].astype(str) == segment]

    total = len(df)
    try:
        page = max(1, int(request.args.get("page", 1)))
        limit = min(100, max(1, int(request.args.get("limit", 20))))
    except ValueError:
        return jsonify({"error": "page and limit must be integers"}), 400

    start = (page - 1) * limit
    page_df = df.iloc[start:start + limit]

    records = page_df.astype(object).where(
        page_df.notna(), None
    ).to_dict(orient="records")

    return json_safe({
        "data_available": True,
        "items": records,
        "page": page,
        "limit": limit,
        "total": total,
    })


@dashboard_bp.get("/api/customers/<customer_id>")
def customer_detail(customer_id):
    df, error = read_data()
    if error:
        return jsonify({"error": error}), 500

    customer = get_customer_detail(df, customer_id)
    if customer is None:
        return jsonify({"error": "Customer not found"}), 404

    return json_safe(customer)


@dashboard_bp.get("/api/risk-queue")
def risk_queue():
    df, error = read_data()
    if error:
        return jsonify({"error": error}), 500

    items = get_risk_queue(df)

    try:
        limit = min(100, max(1, int(request.args.get("limit", 20))))
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400

    return json_safe({
        "data_available": bool(
            not df.empty and "risk_level" in df.columns
        ),
        "items": items[:limit],
        "total": len(items),
    })
