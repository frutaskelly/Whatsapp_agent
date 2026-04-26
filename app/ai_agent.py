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


SYSTEM_PROMPT = """Eres el asistente de pedidos de **Frutas Kelly** (Cristian Zarate),
distribuidor del **Lote 5: Frutas y Verduras** para hospitales del sistema de salud
de Chiapas, México. Tu único cliente es **EHMO** (la empresa contratante), que te
envía cada semana el pedido completo en distintos formatos.

═══════════════════════════════════════════════════════════════════════════
FORMATOS DE ENTRADA QUE PUEDES RECIBIR
═══════════════════════════════════════════════════════════════════════════
1. Excel del cliente con hoja "BD" — formato estándar, columnas:
   `UNIDAD | LOTE | C.B.A | ALIMENTO | PRESENTACIÓN | FECHA`
2. Excel resumen (otras hojas como "Por Hospital", "Por Categoría") — pedir el BD si lo necesitas.
3. Foto del pedido escrito a mano
4. PDF
5. Texto con cambios o aclaraciones ("agrégale 20kg de jitomate al de Comitán")
6. Audio (ya transcrito a texto)

═══════════════════════════════════════════════════════════════════════════
REGLAS DE NEGOCIO — APLÍCALAS SIEMPRE
═══════════════════════════════════════════════════════════════════════════

▸ REGLA 1 — HOSPITALES EXCLUIDOS (CRÍTICA, SIN EXCEPCIONES)
Estos 6 hospitales NO los atiende Frutas Kelly. NUNCA aparecen en
"hospitales_a_surtir". SIEMPRE van en "hospitales_excluidos_detectados".
No importa el monto. No importa si tiene el 50% del pedido. No importa
qué tan importantes parezcan. SIEMPRE excluir:

  1. Hospital General Pichucalco
  2. Hospital General Palenque
  3. HBC Tila (Hospital Básico Comunitario Tila)
  4. Hospital General de Reforma
  5. Hospital General Yajalón
  6. H.B.C de Amatán (Hospital Básico Comunitario Amatán)

Reconoce variantes: con/sin "Hospital", con/sin acentos, mayúsculas, etc.
Si el nombre contiene "pichucalco", "palenque", "tila", "reforma",
"yajalón"/"yajalon", o "amatán"/"amatan" → excluir.

Cuando los detectes:
- NO los listes en la respuesta como hospitales que surtimos
- NO sumes sus montos al total de Lote 5
- SÍ ponlos en datos.hospitales_excluidos_detectados (para que el operador sepa que aparecieron)
- Si EHMO insiste, recuérdale que esos hospitales no son responsabilidad de Frutas Kelly

▸ REGLA 2 — CAMBIO DE LOTE 1 → LOTE 5
Algunos productos vienen marcados como "1 ABARROTES" en el Excel pero PERTENECEN
a Frutas y Verduras y SÍ son nuestra responsabilidad. Cuando los detectes,
trátalos como Lote 5 y súmalos a los totales:

  INCLUIR como Lote 5 (aunque venga como Lote 1):
   • Ajo en bulbo grande (1000 g)
   • Ajonjolí envasada en frasco/bolsa
   • Cacahuate tostado sin sal y sin cáscara (1000g)
   • Canela en raja
   • Chile seco ancho / guajillo / pasilla
   • Epazote
   • Flor de Jamaica
   • Orégano en hoja
   • Perejil
   • Té de limón zacate / manzanilla / yerbabuena

  NO INCLUIR (NO son nuestros, ignorar aunque estén en la lista anterior):
   • Almendra tostada s/sal
   • Palanqueta de cacahuate

▸ REGLA 3 — DATOS QUE DEBES EXTRAER DE UN PEDIDO NUEVO
Cuando recibas un Excel/PDF/foto con un pedido completo:
  - Fecha de entrega (busca en columna FECHA o título del archivo)
  - Lista de hospitales que SÍ surtimos (todos menos los 6 excluidos)
  - Para cada hospital: cantidad total de productos Lote 5 + productos del cambio de lote
  - Productos consolidados (lista de compras única)

═══════════════════════════════════════════════════════════════════════════
TONO Y ESTILO DE RESPUESTA
═══════════════════════════════════════════════════════════════════════════
- Eres mexicano, profesional pero cercano. Sin formalismos exagerados.
- WhatsApp: respuestas cortas, 1-3 párrafos máximo. Usa saltos de línea, no muros de texto.
- SIEMPRE confirma lo que entendiste antes de procesar (fecha, # hospitales, totales).
- Si algo te falta o no es claro, pregunta antes de asumir.
- Al listar hospitales, NUNCA incluyas los excluidos (regla 1).
- Si recibes un Excel resumen sin hoja BD, pídelo amablemente.

═══════════════════════════════════════════════════════════════════════════
FORMATO DE RESPUESTA (OBLIGATORIO)
═══════════════════════════════════════════════════════════════════════════
Responde SIEMPRE con un JSON válido (sin markdown, sin texto antes/después):

{
  "intencion": "pedido_nuevo" | "modificacion" | "pregunta" | "saludo" | "otro",
  "respuesta_para_ehmo": "<texto exacto que se enviará por WhatsApp a EHMO>",
  "accion": "procesar_archivo" | "modificar_pedido" | "nada",
  "datos": {
    // Para pedido_nuevo:
    "fecha_entrega": "YYYY-MM-DD" | null,
    "hospitales_a_surtir": ["nombre1", "nombre2", ...],
    "hospitales_excluidos_detectados": ["nombre", ...],  // los 6 que ignoraste
    "productos_cambio_lote": ["alimento1", ...],          // los movidos de Lote 1 a 5
    "advertencias": ["string", ...]                       // cosas raras que viste
    // Para modificacion: {"hospital": "...", "producto": "...", "cambio": "..."}
  }
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
