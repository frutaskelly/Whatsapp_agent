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

    # Adjuntos: imagen, PDF o Excel
    if attachment_path:
        content.extend(_attachment_to_blocks(attachment_path))

    # Texto
    if text:
        content.append({"type": "text", "text": text})
    elif attachment_path:
        content.append({"type": "text", "text": "[Adjunto sin texto - analiza el documento/imagen]"})

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
        # En dev mostramos el error real para depurar; en prod un mensaje amigable.
        if config.ENVIRONMENT == "development":
            err_msg = f"[Error AI] {type(e).__name__}: {e}"
        else:
            err_msg = "Tuve un problema técnico, ¿puedes repetir tu mensaje?"
        return {
            "intencion": "otro",
            "respuesta_para_ehmo": err_msg,
            "accion": "nada",
            "datos": {}
        }


def load_conversation(phone: str) -> list:
    """Carga el historial de conversación con un contacto."""
    conv_file = config.CONVERSATIONS_DIR / f"{phone}.json"
    if not conv_file.exists():
        return []
    try:
        return json.loads(conv_file.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_conversation(phone: str, messages: list):
    """Guarda el historial de conversación."""
    conv_file = config.CONVERSATIONS_DIR / f"{phone}.json"
    # Limitar a últimos 20 turnos para no crecer indefinido
    messages = messages[-20:]
    conv_file.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")


def chat(phone: str, text: str, attachment_path: Path | None = None) -> dict:
    """Procesa un mensaje (texto y/o adjunto) a través de Claude.

    Soporta texto, imagen (jpg/png/webp), PDF y Excel (xlsx/xls).
    Carga el historial del contacto, llama a Claude, guarda el historial
    actualizado y devuelve el dict estructurado:
        {intencion, respuesta_para_ehmo, accion, datos}
    """
    history = load_conversation(phone)
    # Pasar copia para que interpret_message no mute la lista local
    result = interpret_message(
        text=text,
        attachment_path=attachment_path,
        conversation_history=list(history),
    )
    reply = result.get("respuesta_para_ehmo", "") or ""

    user_content = text or ""
    if attachment_path:
        suffix = f"[adjunto: {attachment_path.name}]"
        user_content = f"{user_content}\n{suffix}".strip()
    history.append({"role": "user", "content": user_content})
    history.append({"role": "assistant", "content": reply})
    save_conversation(phone, history)
    return result


# ─── Helpers de adjuntos ──────────────────────────────────────────────────────
def _attachment_to_blocks(path: Path) -> list[dict]:
    """Convierte un adjunto a bloques de contenido para la API de Claude."""
    suffix = path.suffix.lower()

    # Imagen
    if suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = suffix.lstrip(".")
        media_type = "image/jpeg" if ext == "jpg" else f"image/{ext}"
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode()
        return [{
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }]

    # PDF (soporte nativo de Claude)
    if suffix == ".pdf":
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode()
        return [{
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": data},
        }]

    # Excel: convertir hoja BD (o primera) a texto pipe-separated
    if suffix in (".xlsx", ".xls"):
        text = _excel_to_text(path)
        return [{"type": "text", "text": f"[Adjunto Excel: {path.name}]\n\n{text}"}]

    # Cualquier otro: mensaje de fallback
    return [{"type": "text", "text": f"[Adjunto no soportado: {path.name}]"}]


def _excel_to_text(path: Path, max_rows: int = 1500) -> str:
    """Lee la hoja BD (o la primera) y la devuelve como texto pipe-separated.

    Limita a max_rows para no reventar la ventana de contexto.
    """
    import pandas as pd  # import local: pandas tarda en cargar
    try:
        df = pd.read_excel(path, sheet_name="BD")
        sheet = "BD"
    except Exception:
        df = pd.read_excel(path)
        sheet = "primera hoja"
    total = len(df)
    truncated = ""
    if total > max_rows:
        df = df.head(max_rows)
        truncated = f"\n\n[NOTA: el Excel tiene {total} filas, solo se muestran las primeras {max_rows}]"
    csv_text = df.to_csv(index=False, sep="|")
    return f"Hoja: {sheet} ({total} filas, {len(df.columns)} columnas)\n\n{csv_text}{truncated}"
