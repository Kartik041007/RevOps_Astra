
import os
from pathlib import Path
from flask import Flask, jsonify
from routes.dashboard import dashboard_bp
from routes.approval_actions import approval_bp
from services.mcp_executor import get_default_mcp_executor


def _load_env_file():
    import sys
    if "unittest" in sys.modules:
        return
    env_file = Path(__file__).resolve().parent / ".env"
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


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get(
        "RevOps_Astra_SECRET_KEY",
        "dev-only-change-this-secret",
    )
    app.config["ACTION_EXECUTOR"] = get_default_mcp_executor()

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(approval_bp)

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"error": "Endpoint not found"}), 404 

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({"error": "Internal server error"}), 500

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
