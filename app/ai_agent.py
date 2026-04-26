"""Agente AI con Claude — interpreta mensajes y decide acciones.

PRÓXIMA FASE: aquí se conecta Claude API para que entienda
texto/imagen/PDF/audio y devuelva una acción estructurada.
"""
import json
import logging
import base64
from pathlib import Path
from anthropic import Anthropic
from . import config

log = logging.getLogger(__name__)

client = Anthropic(api_key=config.ANTHROPIC_API_KEY) if config.ANTHROPIC_API_KEY else None


SYSTEM_PROMPT = """Eres el asistente de pedidos de Frutas Kelly, distribuidor de
Frutas y Verduras (Lote 5) para hospitales de Chiapas vía contrato con EHMO.

EHMO te manda mensajes con pedidos en distintos formatos:
- Excel adjunto (formato estándar con hoja "BD")
- Foto de pedido escrito a mano
- PDF
- Mensajes de texto con cambios o aclaraciones
- Audio (transcrito automáticamente)

Tu trabajo:
1. Identificar si el mensaje es un pedido nuevo, modificación, pregunta o saludo.
2. Si es pedido nuevo con archivo: confirmar fecha y total de hospitales/productos.
3. Si es modificación: identificar qué hospital, qué producto, qué cambio.
4. Si falta información: preguntar amablemente.
5. Mantener tono profesional pero cercano (eres mexicano, sin formalismos exagerados).
6. SIEMPRE confirma lo que entendiste antes de procesar.

Responde en JSON estructurado:
{
  "intencion": "pedido_nuevo|modificacion|pregunta|saludo|otro",
  "respuesta_para_ehmo": "texto que se enviará por WhatsApp",
  "accion": "procesar_archivo|modificar_pedido|nada",
  "datos": { ... }
}
"""


def interpret_message(text: str | None = None, attachment_path: Path | None = None,
                      conversation_history: list = None) -> dict:
    """Manda el mensaje a Claude y devuelve la decisión estructurada."""
    if not client:
        log.error("ANTHROPIC_API_KEY no configurada")
        return {
            "intencion": "otro",
            "respuesta_para_ehmo": "Sistema en mantenimiento, intenta más tarde.",
            "accion": "nada",
            "datos": {}
        }

    content = []

    # Adjuntar imagen si existe
    if attachment_path and attachment_path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        media_type = f"image/{attachment_path.suffix.lower().lstrip('.')}"
        if media_type == "image/jpg":
            media_type = "image/jpeg"
        with open(attachment_path, "rb") as f:
            img_data = base64.standard_b64encode(f.read()).decode()
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": img_data,
            }
        })

    # Texto
    if text:
        content.append({"type": "text", "text": text})
    else:
        content.append({"type": "text", "text": "[Adjunto sin texto - analiza la imagen/documento]"})

    messages = conversation_history or []
    messages.append({"role": "user", "content": content})

    try:
        resp = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        raw = resp.content[0].text
        log.info(f"Claude respondió: {raw[:300]}")

        # Intentar parsear JSON
        try:
            # Si la respuesta tiene markdown, extraer el JSON
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning(f"Claude no devolvió JSON parseable, devuelvo raw")
            return {
                "intencion": "otro",
                "respuesta_para_ehmo": raw,
                "accion": "nada",
                "datos": {}
            }
    except Exception as e:
        log.exception(f"Error llamando a Claude: {e}")
        return {
            "intencion": "otro",
            "respuesta_para_ehmo": "Tuve un problema técnico, ¿puedes repetir tu mensaje?",
            "accion": "nada",
            "datos": {}
        }


def load_conversation(phone: str) -> list:
    """Carga el historial de conversación con un contacto."""
    conv_file = config.CONVERSATIONS_DIR / f"{phone}.json"
    if not conv_file.exists():
        return []
    try:
        return json.loads(conv_file.read_text())
    except Exception:
        return []


def save_conversation(phone: str, messages: list):
    """Guarda el historial de conversación."""
    conv_file = config.CONVERSATIONS_DIR / f"{phone}.json"
    # Limitar a últimos 20 turnos para no crecer indefinido
    messages = messages[-20:]
    conv_file.write_text(json.dumps(messages, ensure_ascii=False, indent=2))
