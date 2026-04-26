"""Disparador del procesamiento de pedidos.

Cuando Claude responde con accion="procesar_archivo" y hay un Excel adjunto,
este módulo ejecuta el procesador, sube el resultado a Drive y devuelve la
información para mandar como mensaje de seguimiento al usuario.
"""
import logging
from pathlib import Path

from . import config
from . import message_log
from .pedido_processor import procesar_pedido
from .drive_uploader import upload_file as drive_upload

log = logging.getLogger(__name__)


def maybe_process(phone: str, attachment_path: Path | None, ai_result: dict,
                  original_filename: str | None = None) -> dict | None:
    """Si Claude dijo procesar_archivo y hay Excel, ejecuta el pipeline completo.

    Devuelve un dict con info del resultado (o None si no aplica). Loguea
    automáticamente un mensaje "out" para que el dashboard lo refleje.
    `original_filename` ayuda al procesador a extraer la fecha del nombre real.
    """
    if ai_result.get("accion") != "procesar_archivo":
        return None
    if not attachment_path:
        return None
    if attachment_path.suffix.lower() not in (".xlsx", ".xls"):
        return None

    # Procesar
    try:
        output_path = procesar_pedido(attachment_path, config.PROCESSED_DIR,
                                       original_filename=original_filename)
    except Exception as e:
        log.exception(f"Error procesando pedido: {e}")
        msg = f"⚠️ No pude procesar el pedido: {e}"
        message_log.log_message("out", phone, "text", msg, {"processed": False, "error": str(e)})
        return {"error": str(e)}

    if not output_path:
        msg = ("⚠️ El Excel no tiene la hoja 'BD' que necesito para procesar. "
               "Por favor mándame el archivo original de EHMO con esa hoja.")
        message_log.log_message("out", phone, "text", msg, {"processed": False, "error": "no BD sheet"})
        return {"error": "no BD sheet"}

    # Subir a Drive
    drive_info = drive_upload(output_path)

    # Mensaje de seguimiento
    if drive_info:
        msg = (f"📊 ¡Listo! Procesé el pedido.\n"
               f"Archivo: *{output_path.name}*\n"
               f"📂 Disponible en Drive: {drive_info['link']}")
    else:
        msg = (f"📊 ¡Listo! Procesé el pedido.\n"
               f"Archivo: *{output_path.name}* (guardado localmente, no se pudo subir a Drive)")

    meta = {"processed": True, "output_name": output_path.name}
    if drive_info:
        meta["drive_link"] = drive_info["link"]
        meta["drive_id"] = drive_info["id"]
    message_log.log_message("out", phone, "text", msg, meta)

    return {
        "output_path": str(output_path),
        "output_name": output_path.name,
        "drive": drive_info,
    }
