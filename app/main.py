"""Entry point del agente WhatsApp.

Local:        python app/main.py
Producción:   gunicorn --bind 0.0.0.0:$PORT app.main:app
"""
import logging
import sys
from pathlib import Path
from flask import Flask, jsonify, request, Response
from . import config
from .webhook import bp as webhook_bp
from . import message_log

DASHBOARD_PATH = Path(__file__).parent / "dashboard.html"

# Forzar UTF-8 en stdout para que los emojis no revienten en consola Windows (cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
        # Leer en cada request para reflejar ediciones sin reiniciar el server
        return Response(DASHBOARD_PATH.read_text(encoding="utf-8"), mimetype="text/html")

    @app.route("/api/status")
    def api_status():
        return jsonify({
            "service": "Frutas Kelly WhatsApp Agent",
            "status": "running",
            "environment": config.ENVIRONMENT,
        })

    @app.route("/api/messages")
    def api_messages():
        try:
            limit = int(request.args.get("limit", 200))
        except ValueError:
            limit = 200
        return jsonify({"messages": message_log.read_messages(limit)})

    @app.route("/api/simulate", methods=["POST"])
    def api_simulate():
        """Simula un mensaje entrante (texto y/o adjunto) y llama a Claude.
        No envía nada por WhatsApp, solo loguea ambas direcciones para el dashboard.
        Acepta JSON {message, phone} o multipart/form-data con 'message', 'phone' y 'file'.
        """
        from datetime import datetime
        from werkzeug.utils import secure_filename
        from .ai_agent import chat as ai_chat

        # Detectar formato del request
        ctype = request.content_type or ""
        uploaded = None
        if ctype.startswith("multipart/"):
            text = (request.form.get("message") or "").strip()
            phone = request.form.get("phone") or "simulator"
            uploaded = request.files.get("file")
        else:
            data = request.get_json(silent=True) or {}
            text = (data.get("message") or "").strip()
            phone = data.get("phone") or "simulator"

        # Guardar adjunto si vino
        attachment_path = None
        original_name = None
        if uploaded and uploaded.filename:
            original_name = uploaded.filename
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe = secure_filename(uploaded.filename) or "archivo"
            attachment_path = config.INBOX_DIR / f"{ts}_{phone}_{safe}"
            uploaded.save(attachment_path)

        if not text and not attachment_path:
            return jsonify({"error": "se requiere message o file"}), 400

        # Subir a Drive (si está configurado)
        drive_info = None
        if attachment_path:
            from .drive_uploader import upload_file as drive_upload
            drive_info = drive_upload(attachment_path, original_name=original_name)

        # Log incoming
        in_body = text or ""
        in_meta = {"simulated": True}
        if attachment_path:
            label = f"[adjunto: {attachment_path.name}]"
            in_body = f"{in_body}\n{label}".strip()
            in_meta["attachment"] = attachment_path.name
        if drive_info:
            in_meta["drive_link"] = drive_info["link"]
            in_meta["drive_id"] = drive_info["id"]
        message_log.log_message("in", phone, "text", in_body, in_meta)

        try:
            result = ai_chat(phone, text, attachment_path=attachment_path)
        except Exception as e:
            log.exception(f"Error en /api/simulate: {e}")
            err = f"Error: {e}"
            message_log.log_message("out", phone, "text", err, {"simulated": True, "error": True})
            return jsonify({"error": str(e)}), 500

        reply = result.get("respuesta_para_ehmo") or "(Claude no devolvió respuesta)"
        message_log.log_message(
            "out", phone, "text", reply,
            {"simulated": True, "intencion": result.get("intencion"), "accion": result.get("accion")},
        )
        return jsonify({
            "reply": reply,
            "intencion": result.get("intencion"),
            "accion": result.get("accion"),
            "datos": result.get("datos"),
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
