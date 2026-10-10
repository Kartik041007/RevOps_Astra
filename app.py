
from flask import Flask, jsonify
from routes.dashboard import dashboard_bp


def create_app():
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)

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
