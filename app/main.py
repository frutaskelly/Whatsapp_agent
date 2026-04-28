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
from . import event_log
from .event_log import log_event

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

    @app.route("/api/events")
    def api_events():
        try:
            limit = int(request.args.get("limit", 200))
        except ValueError:
            limit = 200
        return jsonify({"events": event_log.read_events(limit)})

    @app.route("/api/consolidar-notas", methods=["POST", "GET"])
    def api_consolidar_notas():
        """Genera un PDF único con TODAS las notas vigentes del día."""
        from .processing_runner import _consolidar_notas
        return jsonify(_consolidar_notas("api") or {})

    @app.route("/api/reload-prices", methods=["POST", "GET"])
    def api_reload_prices():
        """Refresca la lista de precios desde el Excel (sin reiniciar Flask).
        Útil después de editar Lista_Precios_EHMO.xlsx para agregar productos."""
        from .pricing import cargar_lista_precios
        cargar_lista_precios.cache_clear()
        items = cargar_lista_precios()
        from . import config
        log_event("system", f"🔄 Lista de precios recargada: {len(items)} productos",
                  {"path": config.LISTA_PRECIOS_PATH})
        return jsonify({
            "ok": True,
            "productos_cargados": len(items),
            "ruta": config.LISTA_PRECIOS_PATH,
        })

    @app.route("/api/relacion")
    def api_relacion():
        """Relación por DÍA (default) o semanal.

        Query params:
          - fecha: fecha-iso (YYYY-MM-DD). Si se da, genera relación del día.
          - semana + year: si no hay fecha, genera relación semanal completa.
        """
        from .relacion_documentos import generar_relacion_dia, generar_relacion_semanal
        from .drive_uploader import upload_file as drive_upload
        from datetime import datetime

        fecha = request.args.get("fecha")
        if fecha:
            result = generar_relacion_dia(fecha)
            if result.get("error"):
                return jsonify(result), 404
            drive_info = drive_upload(result["output_path"], subfolder=fecha)
            return jsonify({
                "ok": True,
                "tipo": "dia",
                "fecha": fecha,
                "hospitales": result["hospitales_count"],
                "total": result["total_general"],
                "output_local": str(result["output_path"]),
                "drive_link": (drive_info or {}).get("link"),
            })

        # Semanal (legacy / opcional)
        try:
            semana = int(request.args.get("semana") or datetime.now().isocalendar()[1] - 1)
            year = int(request.args.get("year") or datetime.now().year)
        except ValueError:
            return jsonify({"error": "semana/year inválidos"}), 400
        result = generar_relacion_semanal(semana, year)
        subfolder = f"SEM{semana} ({result['rango_inicio']} a {result['rango_fin']})"
        drive_info = drive_upload(result["output_path"], subfolder=subfolder)
        return jsonify({
            "ok": True,
            "tipo": "semana",
            "semana": semana,
            "rango": f"{result['rango_inicio']} a {result['rango_fin']}",
            "dias_con_data": result["dias_con_data"],
            "output_local": str(result["output_path"]),
            "drive_link": (drive_info or {}).get("link"),
        })

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

        log_event("webhook", f"📩 Mensaje del simulador recibido",
                  {"phone": phone, "has_text": bool(text), "has_file": bool(uploaded and uploaded.filename)})

        # Guardar adjunto si vino
        attachment_path = None
        original_name = None
        if uploaded and uploaded.filename:
            original_name = uploaded.filename
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe = secure_filename(uploaded.filename) or "archivo"
            attachment_path = config.INBOX_DIR / f"{ts}_{phone}_{safe}"
            uploaded.save(attachment_path)
            log_event("storage", f"💾 Archivo guardado: {original_name}",
                      {"path": attachment_path.name, "size_kb": attachment_path.stat().st_size // 1024})

        if not text and not attachment_path:
            return jsonify({"error": "se requiere message o file"}), 400

        # Subir a Drive (si está configurado) — agrupado por fecha del pedido
        drive_info = None
        if attachment_path:
            from .drive_uploader import upload_file as drive_upload
            from .pedido_processor import _extraer_fecha, fecha_a_iso
            fecha_iso = fecha_a_iso(_extraer_fecha(original_name or "")) if original_name else None
            drive_info = drive_upload(attachment_path, original_name=original_name,
                                       subfolder=fecha_iso)

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

        # Si Claude pidió procesar el archivo, dispara el pipeline (genera el
        # Excel de salida + sube a Drive + manda mensaje de seguimiento)
        from .processing_runner import maybe_process
        processed = maybe_process(phone, attachment_path, result,
                                   original_filename=original_name)

        return jsonify({
            "reply": reply,
            "intencion": result.get("intencion"),
            "accion": result.get("accion"),
            "datos": result.get("datos"),
            "processed": processed,
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
