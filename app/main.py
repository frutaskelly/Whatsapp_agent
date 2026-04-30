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

    @app.route("/api/reload-keywords", methods=["POST", "GET"])
    def api_reload_keywords():
        """Refresca las listas de keywords (cambio_kw / ignorar_kw / excluidos_kw)
        desde storage/keywords.json. Útil después de agregar productos nuevos al
        archivo sin necesidad de reiniciar Flask."""
        from .pedido_processor import recargar_keywords
        stats = recargar_keywords()
        log_event("system", "🔄 Keywords extras recargadas", stats)
        return jsonify({"ok": True, **stats})

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

    # ─── Dashboard data endpoints ───────────────────────────────────────────
    @app.route("/api/dias-procesados")
    def api_dias_procesados():
        """Lista los días procesados con sus archivos generados.

        Para cada día (basado en storage/pedidos_dia/<fecha>.json) devuelve:
        fecha, fecha_legible, totales, # hospitales, # ajustes, archivos
        en storage/processed/ que matcheen con la fecha legible.
        """
        import json
        import re
        dias = []
        pedidos_dir = config.BASE_DIR / "storage" / "pedidos_dia"
        extras_dir = config.BASE_DIR / "storage" / "extras_dia"
        processed_dir = config.PROCESSED_DIR

        for state_path in sorted(pedidos_dir.glob("*.json"), reverse=True):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            fecha_iso = state.get("fecha", state_path.stem)
            fecha_legible = state.get("fecha_legible", fecha_iso)

            total_reg = sum(h.get("total", 0) for h in state.get("hospitales", {}).values())
            n_hospitales = len(state.get("hospitales", {}))
            n_ajustes = len(state.get("ajustes", []))

            # Sumar extras del mismo día si existen
            ex_path = extras_dir / f"{fecha_iso}.json"
            total_ex = 0.0
            n_extras = 0
            if ex_path.exists():
                try:
                    ex = json.loads(ex_path.read_text(encoding="utf-8"))
                    total_ex = sum(e.get("importe", 0) for e in ex.get("extras", [])
                                    if e.get("cantidad", 0) > 0)
                    n_extras = sum(1 for e in ex.get("extras", []) if e.get("cantidad", 0) > 0)
                except Exception:
                    pass

            # Files: buscar archivos en processed/ cuyo nombre contenga la fecha legible
            # (ej. "27 de abril", "27 de April") o la fecha-iso
            archivos = []
            if processed_dir.exists():
                # Patrones posibles: "27 de abril", "27 de April", "(2026-04-27)"
                patterns = [fecha_legible, fecha_legible.replace("abril", "April"),
                            f"({fecha_iso})", fecha_iso]
                for f in sorted(processed_dir.iterdir()):
                    if not f.is_file():
                        continue
                    name = f.name
                    if any(p in name for p in patterns):
                        # Clasificar por tipo
                        nl = name.lower()
                        if "lista de compras" in nl or "lista_compras" in nl:
                            tipo = "lista_compras"
                        elif "nota" in nl and "remisión" in nl or "remision" in nl:
                            tipo = "nota_remision"
                        elif "relación" in nl or "relacion" in nl:
                            tipo = "relacion"
                        elif "extras" in nl:
                            tipo = "extras"
                        elif "pedido" in nl:
                            tipo = "pedido"
                        else:
                            tipo = "otro"
                        archivos.append({
                            "name": name,
                            "url": f"/files/processed/{name}",
                            "tipo": tipo,
                            "size_kb": round(f.stat().st_size / 1024, 1),
                            "modified": f.stat().st_mtime,
                        })

            # Drive folder URL — si tenemos GOOGLE_DRIVE_FOLDER_ID, link general
            drive_root = None
            if getattr(config, "GOOGLE_DRIVE_FOLDER_ID", None):
                drive_root = f"https://drive.google.com/drive/folders/{config.GOOGLE_DRIVE_FOLDER_ID}"

            dias.append({
                "fecha_iso": fecha_iso,
                "fecha_legible": fecha_legible,
                "hospitales": n_hospitales,
                "ajustes": n_ajustes,
                "extras": n_extras,
                "total_regular": round(total_reg, 2),
                "total_extras": round(total_ex, 2),
                "total_dia": round(total_reg + total_ex, 2),
                "archivos": archivos,
                "drive_folder": drive_root,
            })
        return jsonify({"dias": dias})

    @app.route("/api/relaciones-todas")
    def api_relaciones_todas():
        """Tabla consolidada de TODAS las remisiones (regulares + extras) en todos los días."""
        import json
        remisiones = []
        pedidos_dir = config.BASE_DIR / "storage" / "pedidos_dia"
        extras_dir = config.BASE_DIR / "storage" / "extras_dia"

        for state_path in sorted(pedidos_dir.glob("*.json"), reverse=True):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            fecha_iso = state.get("fecha", state_path.stem)
            fecha_legible = state.get("fecha_legible", fecha_iso)
            for hospital, info in state.get("hospitales", {}).items():
                folio = info.get("folio_remision") or ""
                try:
                    folio_int = int(folio) if folio else 0
                except (TypeError, ValueError):
                    folio_int = 0
                remisiones.append({
                    "fecha_iso": fecha_iso,
                    "fecha_legible": fecha_legible,
                    "folio": folio_int,
                    "folio_str": folio,
                    "destino": hospital,
                    "tipo": "regular",
                    "estado": info.get("estado", "vigente"),
                    "total": round(info.get("total", 0), 2),
                    "productos": sum(1 for p in info.get("productos", [])
                                       if p.get("cantidad", 0) > 0),
                })

        # Extras al ALMACÉN EHMO u otros destinos
        for ex_path in sorted(extras_dir.glob("*.json"), reverse=True):
            try:
                ex_state = json.loads(ex_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            fecha_iso = ex_state.get("fecha", ex_path.stem)
            fecha_legible = ex_state.get("fecha_legible", fecha_iso)
            folios_destinos = ex_state.get("folios_por_destino") or {}
            estados_destinos = ex_state.get("estados_por_destino") or {}

            # Agrupar extras por destino
            por_destino: dict[str, list[dict]] = {}
            for e in ex_state.get("extras", []):
                if e.get("cantidad", 0) <= 0:
                    continue
                por_destino.setdefault(e.get("hospital", "ALMACÉN EHMO"), []).append(e)

            for destino, items in por_destino.items():
                folio = folios_destinos.get(destino) or ""
                try:
                    folio_int = int(folio) if folio else 0
                except (TypeError, ValueError):
                    folio_int = 0
                total = sum(e.get("importe", 0) for e in items)
                remisiones.append({
                    "fecha_iso": fecha_iso,
                    "fecha_legible": fecha_legible,
                    "folio": folio_int,
                    "folio_str": folio,
                    "destino": f"{destino} (EXTRA)",
                    "tipo": "extra",
                    "estado": estados_destinos.get(destino, "vigente"),
                    "total": round(total, 2),
                    "productos": len(items),
                })

        # Orden: fecha desc, luego folio asc
        remisiones.sort(key=lambda r: (r["fecha_iso"], -r["folio"] if r["folio"] else 0), reverse=True)
        return jsonify({"remisiones": remisiones})

    # ─── Lista de precios (editable) ────────────────────────────────────────
    @app.route("/api/lista-precios", methods=["GET"])
    def api_lista_precios_get():
        """Lee la lista de precios actual y devuelve los renglones editables."""
        import openpyxl
        from pathlib import Path
        path = Path(config.LISTA_PRECIOS_PATH)
        if not path.exists():
            return jsonify({"error": "Lista de precios no encontrada", "path": str(path)}), 404
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb["Lista de Precios"] if "Lista de Precios" in wb.sheetnames else wb.active
        # Header
        headers = [c.value for c in ws[1]]
        rows = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if all(v is None for v in row):
                continue
            rows.append({
                "n": row[0] if len(row) > 0 else None,
                "producto": row[1] if len(row) > 1 else "",
                "unidad": row[2] if len(row) > 2 else "",
                "precio": float(row[3]) if len(row) > 3 and row[3] is not None else 0.0,
            })
        wb.close()
        return jsonify({
            "path": str(path),
            "headers": headers,
            "items": rows,
            "total_items": len(rows),
        })

    @app.route("/api/lista-precios", methods=["POST"])
    def api_lista_precios_save():
        """Guarda la lista de precios completa desde el dashboard.

        Body JSON: {"items": [{"n": 1, "producto": "...", "unidad": "...", "precio": 25.5}, ...]}
        Crea backup antes de sobrescribir, recarga la cache de pricing.
        """
        import openpyxl
        from openpyxl.styles import Font
        from pathlib import Path
        from datetime import datetime as _dt
        import shutil

        body = request.get_json(silent=True) or {}
        items = body.get("items") or []
        if not items:
            return jsonify({"ok": False, "error": "No hay items para guardar"}), 400

        path = Path(config.LISTA_PRECIOS_PATH)
        if not path.exists():
            return jsonify({"ok": False, "error": f"Lista no existe: {path}"}), 404

        # Backup automático
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_suffix(f".backup_{ts}.xlsx")
        shutil.copy2(path, backup_path)

        wb = openpyxl.load_workbook(path)
        ws = wb["Lista de Precios"] if "Lista de Precios" in wb.sheetnames else wb.active

        # Limpiar filas existentes (preservando header)
        max_row = ws.max_row
        for r in range(2, max_row + 1):
            for c in range(1, 5):
                ws.cell(row=r, column=c).value = None

        # Escribir nuevas filas
        for i, it in enumerate(items, 1):
            ws.cell(row=i + 1, column=1, value=int(it.get("n") or i))
            ws.cell(row=i + 1, column=2, value=str(it.get("producto") or "").strip())
            ws.cell(row=i + 1, column=3, value=str(it.get("unidad") or "").strip())
            try:
                precio = float(it.get("precio") or 0)
            except (TypeError, ValueError):
                precio = 0.0
            ws.cell(row=i + 1, column=4, value=precio)
        wb.save(path)
        wb.close()

        # Recargar cache de pricing en memoria
        from .pricing import cargar_lista_precios
        cargar_lista_precios.cache_clear()
        n_loaded = len(cargar_lista_precios())

        log_event("system", f"💲 Lista de precios actualizada desde dashboard ({len(items)} items)",
                  {"backup": backup_path.name, "items": len(items), "cargados": n_loaded})

        return jsonify({
            "ok": True,
            "items_guardados": len(items),
            "items_cargados": n_loaded,
            "backup": backup_path.name,
        })

    # ─── Clientes (editables) ──────────────────────────────────────────────
    def _clientes_path():
        from pathlib import Path
        return Path(config.BASE_DIR) / "storage" / "clientes.json"

    @app.route("/api/clientes", methods=["GET"])
    def api_clientes_get():
        import json as _json
        p = _clientes_path()
        if not p.exists():
            return jsonify({"clientes": [], "lista_precios_disponibles": []})
        try:
            data = _json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            return jsonify({"error": f"clientes.json corrupto: {e}"}), 500
        return jsonify({
            "clientes": data.get("clientes", []),
            "lista_precios_disponibles": data.get("lista_precios_disponibles", []),
        })

    @app.route("/api/clientes", methods=["POST"])
    def api_clientes_save():
        """Guarda el catálogo completo de clientes y listas disponibles."""
        import json as _json
        body = request.get_json(silent=True) or {}
        clientes = body.get("clientes")
        listas = body.get("lista_precios_disponibles")
        if clientes is None:
            return jsonify({"ok": False, "error": "Falta 'clientes' en body"}), 400

        p = _clientes_path()
        # Preservar campos meta (como _documentacion) si ya existían
        existing = {}
        if p.exists():
            try:
                existing = _json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass

        new_data = {
            "_documentacion": existing.get("_documentacion",
                "Catálogo editable de clientes. Editable desde el dashboard."),
            "lista_precios_disponibles": listas if listas is not None else
                existing.get("lista_precios_disponibles", []),
            "clientes": clientes,
        }
        p.write_text(_json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8")
        log_event("system", f"👥 Clientes actualizados desde dashboard ({len(clientes)} clientes)",
                  {"clientes": len(clientes)})
        return jsonify({"ok": True, "clientes_guardados": len(clientes)})

    @app.route("/files/processed/<path:filename>")
    def serve_processed(filename):
        """Sirve archivos generados desde storage/processed/ para descargar/ver."""
        from flask import send_from_directory, abort
        directory = Path(config.PROCESSED_DIR).resolve()
        # Validación: el archivo debe existir Y estar dentro del directorio (no ../)
        target = (directory / filename).resolve()
        try:
            target.relative_to(directory)
        except ValueError:
            return abort(403)
        if not target.exists() or not target.is_file():
            return abort(404)
        return send_from_directory(str(directory), filename)

    return app


app = create_app()


if __name__ == "__main__":
    config.validate_config()
    log.info(f"🚀 Frutas Kelly WhatsApp Agent — entorno: {config.ENVIRONMENT}")
    log.info(f"   Webhook URL local: http://localhost:{config.PORT}/webhook")
    log.info(f"   Verify token: {config.WHATSAPP_VERIFY_TOKEN}")
    app.run(host="0.0.0.0", port=config.PORT, debug=(config.ENVIRONMENT == "development"))
