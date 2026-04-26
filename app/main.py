"""Entry point del agente WhatsApp.

Local:        python app/main.py
Producción:   gunicorn --bind 0.0.0.0:$PORT app.main:app
"""
import logging
import sys
from flask import Flask, jsonify
from . import config
from .webhook import bp as webhook_bp

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    app.register_blueprint(webhook_bp)

    @app.route("/")
    def index():
        return jsonify({
            "service": "Frutas Kelly WhatsApp Agent",
            "status": "running",
            "environment": config.ENVIRONMENT,
        })

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"}), 200

    return app


app = create_app()


if __name__ == "__main__":
    config.validate_config()
    log.info(f"🚀 Frutas Kelly WhatsApp Agent — entorno: {config.ENVIRONMENT}")
    log.info(f"   Webhook URL local: http://localhost:{config.PORT}/webhook")
    log.info(f"   Verify token: {config.WHATSAPP_VERIFY_TOKEN}")
    app.run(host="0.0.0.0", port=config.PORT, debug=(config.ENVIRONMENT == "development"))
