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
        result = procesar_pedido(attachment_path, config.PROCESSED_DIR,
                                  original_filename=original_filename)
    except Exception as e:
        log.exception(f"Error procesando pedido: {e}")
        msg = f"⚠️ No pude procesar el pedido: {e}"
        message_log.log_message("out", phone, "text", msg, {"processed": False, "error": str(e)})
        return {"error": str(e)}

    if not result:
        msg = ("⚠️ El Excel no tiene la hoja 'BD' que necesito para procesar. "
               "Por favor mándame el archivo original de EHMO con esa hoja.")
        message_log.log_message("out", phone, "text", msg, {"processed": False, "error": "no BD sheet"})
        return {"error": "no BD sheet"}

    output_path = result["output_path"]
    desconocidos = result.get("hospitales_desconocidos", [])
    sin_fyv = result.get("hospitales_si_sin_pedido_fyv", [])
    con_fyv = result.get("hospitales_con_fyv", [])
    excluidos = result.get("hospitales_excluidos_detectados", [])
    productos_cambio = result.get("productos_cambio_lote", [])
    fecha = result.get("fecha", "fecha desconocida")

    # Subir a Drive
    drive_info = drive_upload(output_path)

    # ─── Mensaje completo (lo construye Python, no se corta nunca) ───────────
    lines = [
        f"📊 *Pedido procesado* — {fecha}",
        f"Archivo: *{output_path.name}*",
    ]

    # Hospitales a surtir hoy (enumerados)
    lines.append("")
    lines.append(f"🏥 *Hospitales a surtir hoy ({len(con_fyv)}):*")
    for i, h in enumerate(con_fyv, 1):
        lines.append(f"{i}. {h}")

    # Excluidos detectados
    if excluidos:
        lines.append("")
        lines.append(f"🚫 *Excluidos detectados ({len(excluidos)}) — NO se surten:*")
        for i, h in enumerate(excluidos, 1):
            lines.append(f"{i}. {h}")

    # Productos del cambio de lote (los que el procesador realmente movió)
    if productos_cambio:
        lines.append("")
        lines.append(f"🔄 *Productos movidos Lote 1 → Lote 5 ({len(productos_cambio)}):*")
        for i, p in enumerate(productos_cambio, 1):
            lines.append(f"{i}. {p}")

    # Hospitales conocidos pero sin pedido FyV hoy
    if sin_fyv:
        lines.append("")
        lines.append(f"ℹ️ *{len(sin_fyv)} hospital(es) NO pidieron FyV hoy* (solo otros lotes):")
        for h in sin_fyv:
            lines.append(f"  • {h}")

    # Hospitales no en catálogo (revisión humana)
    if desconocidos:
        lines.append("")
        lines.append(f"⚠️ *{len(desconocidos)} hospital(es) NO en catálogo* — confirma si los surtes:")
        for h in desconocidos:
            lines.append(f"  • {h}")

    # Link de Drive
    lines.append("")
    if drive_info:
        lines.append(f"📂 Excel completo en Drive: {drive_info['link']}")
    else:
        lines.append("⚠️ Guardado local (no se pudo subir a Drive)")

    msg = "\n".join(lines)

    meta = {
        "processed": True,
        "output_name": output_path.name,
        "hospitales_desconocidos": desconocidos,
        "hospitales_si_sin_pedido_fyv": sin_fyv,
    }
    if drive_info:
        meta["drive_link"] = drive_info["link"]
        meta["drive_id"] = drive_info["id"]
    message_log.log_message("out", phone, "text", msg, meta)

    return {
        "output_path": str(output_path),
        "output_name": output_path.name,
        "drive": drive_info,
        "hospitales_si": result.get("hospitales_si", []),
        "hospitales_con_fyv": con_fyv,
        "hospitales_si_sin_pedido_fyv": sin_fyv,
        "hospitales_desconocidos": desconocidos,
        "hospitales_excluidos_detectados": result.get("hospitales_excluidos_detectados", []),
    }
