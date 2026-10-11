
from flask import Flask, app, jsonify
from routes.dashboard import dashboard_bp
from routes.approval_actions import approval_bp


def create_app():
    app = Flask(__name__)
    import os
    app.config["SECRET_KEY"] = os.environ.get(
    "RevOps_Astra_SECRET_KEY",
    "dev-only-change-this-secret"
)


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
