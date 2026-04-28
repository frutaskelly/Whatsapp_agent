"""Pedidos EXTRA para cubrir desabasto de otros lotes (Abarrote, etc.).

Cuando EHMO solicita productos extra (que NO son parte del Lote 5 / FyV
estándar) para cubrir desabasto de otro lote (típicamente abarrotes),
estos extras se manejan en un FLUJO PARALELO al pedido del día:
  - Estado independiente en storage/extras_dia/<fecha-iso>.json
  - Hoja de surtido (sin precios) APARTE — el personal sabe que es extra
  - Nota de remisión con precios APARTE — para cobranza separada

Operaciones soportadas:
  - "agregar": añadir un extra (puede crear el primer extra del día)
  - "cancelar": eliminar un extra ya agregado

El precio se intenta resolver primero con la lista de precios oficial
(productos como "CHILE ANCHO SECO" sí están). Si no se encuentra, se
guarda con precio = 0 y se marca para que el operador lo confirme.
"""
import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path
import pandas as pd

from . import config
from .event_log import log_event
from .pricing import buscar_precio
from .ajuste_entrega import _resolver_hospital, _norm
from .pedido_processor import HOSPITALES_CONOCIDOS_SI, fecha_a_iso

log = logging.getLogger(__name__)

_state_lock = threading.Lock()


def _extras_dir() -> Path:
    d = config.BASE_DIR / "storage" / "extras_dia"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_file(fecha_iso: str) -> Path:
    return _extras_dir() / f"{fecha_iso}.json"


def _hoy_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def cargar_extras(fecha_iso: str) -> dict | None:
    p = _state_file(fecha_iso)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _resolver_fecha_destino(fecha_iso_explicita: str | None) -> tuple[str, str]:
    """Devuelve (fecha_iso, fecha_legible) para los extras.

    Si no se da fecha explícita, usa la del último estado de pedido normal.
    Como fallback final, hoy.
    """
    if fecha_iso_explicita:
        return fecha_iso_explicita, fecha_iso_explicita

    from .estado_pedido import cargar_estado_mas_reciente
    estado, fecha = cargar_estado_mas_reciente()
    if estado and fecha:
        return fecha, estado.get("fecha_legible", fecha)

    hoy = _hoy_iso()
    legible = datetime.now().strftime("%d de %B").replace("January", "enero")  # rough
    return hoy, legible


def _empty_state(fecha_iso: str, fecha_legible: str) -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "fecha": fecha_iso,
        "fecha_legible": fecha_legible,
        "creado": now,
        "ultima_modificacion": now,
        "extras": [],
    }


def agregar_extras(extras_input: list[dict],
                   fecha_iso: str | None = None,
                   fecha_legible: str | None = None) -> dict:
    """Agrega extras al estado del día.

    extras_input = [
      {"hospital": "Comitán", "alimento": "Chile ancho seco",
       "cantidad": 50, "presentacion": "KG", "precio": 187.50,
       "motivo": "Desabasto de abarrotes"},
      ...
    ]
    Si "precio" no se da, lo busca en Lista_Precios_EHMO.xlsx.
    Si "presentacion" no se da, default "KG".
    """
    fecha_iso, fecha_legible_default = _resolver_fecha_destino(fecha_iso)
    fecha_legible = fecha_legible or fecha_legible_default

    state = cargar_extras(fecha_iso) or _empty_state(fecha_iso, fecha_legible)

    # Hospitales válidos para resolver nombres
    hospitales_catalogo = list(HOSPITALES_CONOCIDOS_SI.keys())

    cambios = []
    sin_precio = []
    for inp in extras_input:
        alimento = (inp.get("alimento") or "").strip()
        if not alimento:
            continue
        try:
            cantidad = float(inp.get("cantidad", 0) or 0)
        except (TypeError, ValueError):
            cantidad = 0
        if cantidad <= 0:
            continue

        # Resolver hospital (opcional — puede venir vacío o explícitamente
        # "almacén EHMO" cuando el extra no es para un hospital específico).
        hospital_input = (inp.get("hospital") or "").strip()
        hospital_resuelto = None
        if hospital_input:
            # Detectar referencias al almacén / EHMO general
            hi_lower = hospital_input.lower()
            if any(k in hi_lower for k in ["almac", "ehmo", "general", "centro"]):
                # match si menciona almacén, ehmo, etc — destino "ALMACÉN EHMO"
                if "ehmo" in hi_lower or "almac" in hi_lower:
                    hospital_resuelto = "ALMACÉN EHMO"
            if not hospital_resuelto:
                hospital_resuelto = _resolver_hospital(hospital_input, hospitales_catalogo)
                if not hospital_resuelto:
                    hospital_resuelto = hospital_input  # acepta texto libre
        if not hospital_resuelto:
            hospital_resuelto = "ALMACÉN EHMO"

        # Resolver precio
        precio_dado = inp.get("precio")
        if precio_dado is not None:
            try:
                precio = float(precio_dado)
            except (TypeError, ValueError):
                precio = 0
        else:
            precio = 0
        # IMPORTANTE: respetar el nombre que envió el cliente. Solo usamos la
        # lista de precios para resolver el precio numérico, no para renombrar.
        alimento_canonico = alimento
        presentacion = (inp.get("presentacion") or "").strip()
        if precio == 0:
            match = buscar_precio(alimento)
            if match:
                precio = float(match["precio"])
                if not presentacion:
                    presentacion = match.get("unidad", "KG")
        if not presentacion:
            presentacion = "KG"

        importe = round(cantidad * precio, 2)
        extra = {
            "id": str(uuid.uuid4())[:8],
            "hospital": hospital_resuelto,
            "alimento": alimento_canonico,
            "presentacion": presentacion,
            "cantidad": cantidad,
            "precio_unitario": precio,
            "importe": importe,
            "motivo": inp.get("motivo") or "Extra para cubrir necesidades especiales",
            "tiene_precio": precio > 0,
            "agregado": datetime.now().isoformat(timespec="seconds"),
        }
        state["extras"].append(extra)
        cambios.append(extra)
        if not extra["tiene_precio"]:
            sin_precio.append(alimento_canonico)

    state["ultima_modificacion"] = datetime.now().isoformat(timespec="seconds")
    with _state_lock:
        _state_file(fecha_iso).write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    total_extras = sum(e["importe"] for e in state["extras"] if e["cantidad"] > 0)
    log_event("processor",
              f"📦 {len(cambios)} extra(s) agregados al pedido del {fecha_iso}",
              {"cambios": cambios, "total_acumulado": total_extras,
               "sin_precio": sin_precio})

    return {
        "ok": True,
        "fecha": fecha_iso,
        "fecha_legible": state["fecha_legible"],
        "cambios": cambios,
        "sin_precio": sin_precio,
        "count_total": len([e for e in state["extras"] if e["cantidad"] > 0]),
        "total_extras": round(total_extras, 2),
    }


def _state_a_dataframe(state: dict) -> pd.DataFrame:
    rows = []
    for e in state.get("extras", []):
        if e.get("cantidad", 0) > 0:
            rows.append({
                "UNIDAD": e["hospital"],
                "ALIMENTO": e["alimento"],
                "PRESENTACION": e["presentacion"],
                "CANTIDAD": e["cantidad"],
            })
    return pd.DataFrame(rows, columns=["UNIDAD", "ALIMENTO", "PRESENTACION", "CANTIDAD"])


def regenerar_archivos_extras(fecha_iso: str) -> dict:
    """Genera los 2 PDFs de extras (hoja surtido + nota remisión) y sube a Drive."""
    from .pedido_pdf import generar_pdf_pedido
    from .nota_remision import generar_notas_remision
    from .drive_uploader import upload_file as drive_upload

    state = cargar_extras(fecha_iso)
    if not state or not state.get("extras"):
        return {"error": "No hay extras para este día"}

    df = _state_a_dataframe(state)
    if df.empty:
        return {"error": "Todos los extras tienen cantidad 0"}

    fecha_legible = state.get("fecha_legible", fecha_iso)
    ts = datetime.now().strftime("%H%M%S")

    # Hoja de surtido (sin precios) — banner morado para distinguir
    hoja_path = config.PROCESSED_DIR / f"EXTRAS Hoja Surtido {fecha_legible} {ts}.pdf"
    drive_hoja = None
    try:
        generar_pdf_pedido(
            df, fecha_legible, hoja_path,
            subtitulo="Cubrir desabasto / necesidades especiales",
            titulo_principal="EXTRAS — PEDIDO PARA CUBRIR DESABASTO",
        )
        drive_hoja = drive_upload(hoja_path, subfolder=fecha_iso)
    except Exception as e:
        log.exception(f"Error hoja surtido extras: {e}")
        log_event("processor", f"⚠️ Error hoja extras: {e}", level="warn")

    # Notas con precios — capturar folios y guardarlos en el state
    notas_path = config.PROCESSED_DIR / f"EXTRAS Notas Remisión {fecha_legible} {ts}.pdf"
    drive_notas = None
    notas_total = 0
    folios_existentes = state.get("folios_por_destino") or {}
    try:
        info = generar_notas_remision(df, fecha_legible, notas_path, tipo="extras",
                                       folios_existentes=folios_existentes)
        notas_total = info["total_general"]
        # Persistir folios por destino (ej. ALMACÉN EHMO → 0000000013)
        state["folios_por_destino"] = info.get("folios", {})
        with _state_lock:
            _state_file(fecha_iso).write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        drive_notas = drive_upload(notas_path, subfolder=fecha_iso)
    except Exception as e:
        log.exception(f"Error notas extras: {e}")
        log_event("processor", f"⚠️ Error notas extras: {e}", level="warn")

    return {
        "drive_hoja_surtido": drive_hoja,
        "drive_notas": drive_notas,
        "notas_total": notas_total,
        "count": len(df),
        "fecha": fecha_iso,
    }


def cancelar_extra(extra_id: str, fecha_iso: str | None = None) -> dict:
    """Cancela un extra por su ID (cantidad → 0)."""
    fecha_iso, _ = _resolver_fecha_destino(fecha_iso)
    state = cargar_extras(fecha_iso)
    if not state:
        return {"ok": False, "error": "No hay extras para este día"}
    encontrado = None
    for e in state["extras"]:
        if e["id"] == extra_id:
            e["cantidad"] = 0
            e["importe"] = 0
            encontrado = e
            break
    if not encontrado:
        return {"ok": False, "error": f"Extra {extra_id} no encontrado"}
    state["ultima_modificacion"] = datetime.now().isoformat(timespec="seconds")
    with _state_lock:
        _state_file(fecha_iso).write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "extra": encontrado, "fecha": fecha_iso}
