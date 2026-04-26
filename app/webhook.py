"""Endpoints del webhook de WhatsApp.

Meta envía POST a /webhook con cada mensaje entrante.
Meta envía GET a /webhook al configurar (verificación).
"""
import json
import hmac
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from flask import Blueprint, request, jsonify
from . import config
from .whatsapp_client import WhatsAppClient
from .message_log import log_message
from .ai_agent import chat as ai_chat
from .drive_uploader import upload_file as drive_upload

log = logging.getLogger(__name__)
bp = Blueprint("webhook", __name__)
wa = WhatsAppClient()


# ─── Verificación inicial (GET) ───────────────────────────────────────────────
@bp.route("/webhook", methods=["GET"])
def verify_webhook():
    """Meta llama esto al guardar el webhook URL en el dashboard."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == config.WHATSAPP_VERIFY_TOKEN:
        log.info("✓ Webhook verificado correctamente")
        return challenge, 200

    log.warning(f"✗ Verificación fallida. mode={mode} token={token}")
    return "Forbidden", 403


# ─── Recepción de mensajes (POST) ─────────────────────────────────────────────
@bp.route("/webhook", methods=["POST"])
def receive_message():
    """Recibe cada mensaje/evento de WhatsApp."""
    # Verificar firma (opcional pero recomendado)
    if config.WHATSAPP_APP_SECRET and not verify_signature(request):
        log.warning("✗ Firma inválida")
        return "Forbidden", 403

    payload = request.get_json(silent=True) or {}
    log.info(f"Webhook recibido: {json.dumps(payload, ensure_ascii=False)[:500]}")

    # Guardar payload crudo para debug
    save_raw_payload(payload)

    # Procesar
    try:
        process_webhook(payload)
    except Exception as e:
        log.exception(f"Error procesando webhook: {e}")

    # SIEMPRE responder 200 a Meta o reintenta
    return "OK", 200


# ─── Procesamiento ────────────────────────────────────────────────────────────
def process_webhook(payload: dict):
    """Extrae los mensajes del payload y los procesa uno por uno."""
    if payload.get("object") != "whatsapp_business_account":
        log.info(f"Objeto ignorado: {payload.get('object')}")
        return

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            # Mensajes entrantes
            for msg in value.get("messages", []):
                handle_message(msg, value.get("contacts", []))

            # Cambios de estado de mensajes que tú enviaste (sent/delivered/read)
            for status in value.get("statuses", []):
                log.info(f"Status update: {status.get('status')} - {status.get('id')}")


def handle_message(msg: dict, contacts: list):
    """Procesa un mensaje individual."""
    msg_id = msg.get("id")
    from_number = msg.get("from")
    msg_type = msg.get("type")
    timestamp = msg.get("timestamp")

    # Whitelist: solo procesar mensajes de EHMO si está configurado
    if config.EHMO_PHONE and from_number != config.EHMO_PHONE:
        log.info(f"Mensaje de {from_number} ignorado (no es EHMO)")
        wa.send_text(from_number, "Hola, este número es solo para EHMO. Si necesitas algo, contacta a Cristian directamente.")
        return

    log.info(f"📩 Mensaje {msg_type} de {from_number} (id={msg_id})")

    # Logueo del mensaje entrante para el dashboard
    body_preview = _extract_body_preview(msg, msg_type)
    log_message("in", from_number, msg_type, body_preview, {"id": msg_id})

    # Marcar como leído + reaccionar para que vea que lo recibimos
    try:
        wa.mark_as_read(msg_id)
        wa.send_reaction(from_number, msg_id, "👀")
    except Exception as e:
        log.warning(f"No se pudo marcar como leído: {e}")

    # Router por tipo
    if msg_type == "text":
        handle_text(msg, from_number)
    elif msg_type == "image":
        handle_media(msg, from_number, "image", "jpg")
    elif msg_type == "document":
        handle_document(msg, from_number)
    elif msg_type == "audio":
        handle_media(msg, from_number, "audio", "ogg")
    elif msg_type == "video":
        handle_media(msg, from_number, "video", "mp4")
    else:
        log.info(f"Tipo no manejado: {msg_type}")
        wa.send_text(from_number, f"Recibí tu mensaje pero por ahora no proceso '{msg_type}'.")


def handle_text(msg: dict, from_number: str):
    """Procesa mensaje de texto pasándoselo al AI agent (Claude)."""
    text = msg.get("text", {}).get("body", "")
    log.info(f"Texto: {text[:200]}")

    try:
        result = ai_chat(from_number, text)
        reply = result.get("respuesta_para_ehmo") or "Recibí tu mensaje, déjame revisarlo."
    except Exception as e:
        log.exception(f"Error llamando a Claude: {e}")
        reply = "Tuve un problema técnico, ¿puedes repetir tu mensaje?"

    wa.send_text(from_number, reply)
    # Para texto puro no hay archivo que procesar; el procesamiento se dispara
    # cuando llega un Excel en handle_document.


def handle_media(msg: dict, from_number: str, kind: str, ext: str):
    """Descarga foto/audio/video y lo guarda en inbox."""
    media_obj = msg.get(kind, {})
    media_id = media_obj.get("id")
    if not media_id:
        log.warning(f"Sin media_id en {kind}")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{from_number}_{kind}.{ext}"
    save_path = config.INBOX_DIR / filename

    result = wa.download_media(media_id, save_path)
    if result:
        # Subir a Drive en background (si está configurado)
        drive_upload(save_path)
        wa.send_text(from_number, f"✓ Recibí tu {kind}. Procesando...")
        # TODO: mandar a Claude AI con visión para imágenes
    else:
        wa.send_text(from_number, f"⚠️ No pude descargar tu {kind}. Intenta de nuevo.")


def handle_document(msg: dict, from_number: str):
    """Procesa documentos adjuntos (Excel, PDF)."""
    doc = msg.get("document", {})
    media_id = doc.get("id")
    filename_orig = doc.get("filename", "documento")
    mime_type = doc.get("mime_type", "")

    if not media_id:
        return

    # Extensión basada en mime type
    if "spreadsheet" in mime_type or filename_orig.lower().endswith((".xlsx", ".xls")):
        ext = "xlsx"
        kind_label = "Excel"
    elif "pdf" in mime_type or filename_orig.lower().endswith(".pdf"):
        ext = "pdf"
        kind_label = "PDF"
    else:
        ext = "bin"
        kind_label = "documento"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c for c in filename_orig if c.isalnum() or c in "._-")[:50]
    filename = f"{timestamp}_{from_number}_{safe_name}"
    save_path = config.INBOX_DIR / filename

    result = wa.download_media(media_id, save_path)
    if not result:
        wa.send_text(from_number, f"⚠️ No pude descargar tu {kind_label}. Intenta de nuevo.")
        return

    drive_upload(save_path, original_name=filename_orig)
    wa.send_text(from_number, f"✓ Recibí tu {kind_label}: {filename_orig}. Lo estoy revisando...")

    # Mandar a Claude para que decida si procesar
    try:
        ai_result = ai_chat(from_number, f"[Adjunto recibido: {filename_orig}]",
                            attachment_path=save_path)
        ai_reply = ai_result.get("respuesta_para_ehmo")
        if ai_reply:
            wa.send_text(from_number, ai_reply)
    except Exception as e:
        log.exception(f"Error con Claude en handle_document: {e}")
        return

    # Si Claude dice "procesar_archivo", dispara el pipeline
    from .processing_runner import maybe_process
    processed = maybe_process(from_number, save_path, ai_result, original_filename=filename_orig)
    if processed and processed.get("drive"):
        wa.send_text(from_number,
                     f"📊 Pedido procesado y disponible en Drive:\n{processed['drive']['link']}")
    elif processed and processed.get("error"):
        wa.send_text(from_number, f"⚠️ {processed['error']}")


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _extract_body_preview(msg: dict, msg_type: str) -> str:
    """Devuelve un texto representativo del mensaje para mostrar en el log."""
    if msg_type == "text":
        return msg.get("text", {}).get("body", "")
    if msg_type == "document":
        d = msg.get("document", {})
        return f"[documento: {d.get('filename', '?')}]"
    if msg_type == "image":
        cap = msg.get("image", {}).get("caption", "")
        return f"[imagen]{' — ' + cap if cap else ''}"
    if msg_type == "audio":
        return "[audio]"
    if msg_type == "video":
        cap = msg.get("video", {}).get("caption", "")
        return f"[video]{' — ' + cap if cap else ''}"
    return f"[{msg_type}]"


def verify_signature(req) -> bool:
    """Verifica que el webhook venga de Meta usando HMAC-SHA256."""
    signature = req.headers.get("X-Hub-Signature-256", "")
    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(
        config.WHATSAPP_APP_SECRET.encode(),
        req.get_data(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


def save_raw_payload(payload: dict):
    """Guarda el payload crudo para debug."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    debug_file = config.INBOX_DIR.parent / "raw_webhooks" / f"{timestamp}.json"
    debug_file.parent.mkdir(parents=True, exist_ok=True)
    debug_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
