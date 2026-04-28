"""Control de estados de los folios para facturación.

Cada folio (hospital o destino de extras) tiene un estado:
  - "vigente"    → recién procesado, sin cambios
  - "modificado" → tuvo modificaciones o ajustes (auto)
  - "aceptado"   → operador aprobó para facturar (manual, BLOQUEA cambios)
  - "cancelado"  → operador anuló o quedó en total 0 (queda en relación con badge)

Reglas:
  - Si un folio está "aceptado", NO se permite modificación ni ajuste.
  - Si un ajuste deja total en 0, el estado pasa a "cancelado".
  - "cancelar" explícito → estado="cancelado", cantidades NO se tocan (rastro).
  - "reactivar" → vuelve a "vigente" o "modificado" según historial.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from . import config
from .event_log import log_event
from .estado_pedido import (
    cargar_estado, cargar_estado_mas_reciente, _state_file, _state_lock,
)

log = logging.getLogger(__name__)

ESTADOS = {"vigente", "modificado", "aceptado", "cancelado"}
ESTADO_ICON = {
    "vigente": "🆕",
    "modificado": "🔄",
    "aceptado": "✅",
    "cancelado": "❌",
}


def _normalizar_folio(folio_input) -> str | None:
    """Convierte '13' o '0000000013' o 13 al formato canónico '0000000013'."""
    try:
        n = int(str(folio_input).lstrip("0") or "0")
        return f"{n:010d}"
    except (TypeError, ValueError):
        return None


def _buscar_folio(state: dict, fecha_iso: str, folio_str: str) -> dict | None:
    """Devuelve {tipo, key, info_dict} para el folio dado, o None."""
    # En hospitales del pedido normal
    for h, info in state.get("hospitales", {}).items():
        if info.get("folio_remision") == folio_str:
            return {"tipo": "hospital", "key": h, "info": info, "state": state}
    # En destinos de extras
    try:
        from .extras_pedido import cargar_extras
        ex_state = cargar_extras(fecha_iso)
        if ex_state:
            folios_dest = ex_state.get("folios_por_destino") or {}
            for destino, f in folios_dest.items():
                if f == folio_str:
                    return {"tipo": "extras", "key": destino,
                            "info": ex_state, "state": state}
    except Exception:
        pass
    return None


def _set_estado_hospital(fecha_iso: str, hospital: str, nuevo_estado: str) -> dict:
    state = cargar_estado(fecha_iso)
    if not state:
        return {"error": "no hay pedido del día"}
    if hospital not in state.get("hospitales", {}):
        return {"error": f"hospital '{hospital}' no en estado"}
    info = state["hospitales"][hospital]
    estado_anterior = info.get("estado", "vigente")
    info["estado"] = nuevo_estado
    info["estado_actualizado"] = datetime.now().isoformat(timespec="seconds")
    state["ultima_modificacion"] = info["estado_actualizado"]
    with _state_lock:
        _state_file(fecha_iso).write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "anterior": estado_anterior, "nuevo": nuevo_estado,
            "hospital": hospital, "folio": info.get("folio_remision")}


def _set_estado_extras(fecha_iso: str, destino: str, nuevo_estado: str) -> dict:
    from .extras_pedido import cargar_extras, _state_file as _ex_state_file, _state_lock as _ex_lock
    ex_state = cargar_extras(fecha_iso)
    if not ex_state:
        return {"error": "no hay extras del día"}
    estados_destinos = ex_state.setdefault("estados_por_destino", {})
    estado_anterior = estados_destinos.get(destino, "vigente")
    estados_destinos[destino] = nuevo_estado
    ex_state["ultima_modificacion"] = datetime.now().isoformat(timespec="seconds")
    with _ex_lock:
        _ex_state_file(fecha_iso).write_text(
            json.dumps(ex_state, ensure_ascii=False, indent=2), encoding="utf-8")
    folio = (ex_state.get("folios_por_destino") or {}).get(destino)
    return {"ok": True, "anterior": estado_anterior, "nuevo": nuevo_estado,
            "destino": destino, "folio": folio}


def aceptar_folio(folio_input, fecha_iso: str | None = None) -> dict:
    """Marca un folio como 'aceptado' (listo para facturar). BLOQUEA cambios."""
    folio_str = _normalizar_folio(folio_input)
    if not folio_str:
        return {"error": f"folio '{folio_input}' inválido"}
    if not fecha_iso:
        state, fecha_iso = cargar_estado_mas_reciente()
    else:
        state = cargar_estado(fecha_iso)
    if not state:
        return {"error": "no hay pedido del día"}

    found = _buscar_folio(state, fecha_iso, folio_str)
    if not found:
        return {"error": f"folio {int(folio_str)} no existe"}

    if found["tipo"] == "hospital":
        info = found["info"]
        if info.get("estado") == "cancelado":
            return {"error": f"folio {int(folio_str)} está cancelado, no se puede aceptar"}
        return _set_estado_hospital(fecha_iso, found["key"], "aceptado")
    else:
        return _set_estado_extras(fecha_iso, found["key"], "aceptado")


def cancelar_folio(folio_input, fecha_iso: str | None = None) -> dict:
    """Cancela un folio (queda en la relación con badge ❌, cantidades NO se tocan)."""
    folio_str = _normalizar_folio(folio_input)
    if not folio_str:
        return {"error": f"folio '{folio_input}' inválido"}
    if not fecha_iso:
        state, fecha_iso = cargar_estado_mas_reciente()
    else:
        state = cargar_estado(fecha_iso)
    if not state:
        return {"error": "no hay pedido del día"}

    found = _buscar_folio(state, fecha_iso, folio_str)
    if not found:
        return {"error": f"folio {int(folio_str)} no existe"}

    if found["tipo"] == "hospital":
        return _set_estado_hospital(fecha_iso, found["key"], "cancelado")
    else:
        return _set_estado_extras(fecha_iso, found["key"], "cancelado")


def reactivar_folio(folio_input, fecha_iso: str | None = None) -> dict:
    """Reactiva un folio cancelado/aceptado: vuelve a 'modificado' si tuvo cambios,
    o 'vigente' si nunca tuvo."""
    folio_str = _normalizar_folio(folio_input)
    if not folio_str:
        return {"error": f"folio '{folio_input}' inválido"}
    if not fecha_iso:
        state, fecha_iso = cargar_estado_mas_reciente()
    else:
        state = cargar_estado(fecha_iso)
    if not state:
        return {"error": "no hay pedido del día"}

    found = _buscar_folio(state, fecha_iso, folio_str)
    if not found:
        return {"error": f"folio {int(folio_str)} no existe"}

    # Determinar nuevo estado: si hay ajustes para este hospital → modificado, sino vigente
    if found["tipo"] == "hospital":
        hospital = found["key"]
        tuvo_ajustes = any(a.get("hospital") == hospital for a in state.get("ajustes", []))
        nuevo = "modificado" if tuvo_ajustes else "vigente"
        return _set_estado_hospital(fecha_iso, hospital, nuevo)
    else:
        return _set_estado_extras(fecha_iso, found["key"], "vigente")


def folio_aceptado(state: dict, hospital: str) -> bool:
    """Verifica si un hospital tiene su folio aceptado (bloqueado para cambios)."""
    info = state.get("hospitales", {}).get(hospital, {})
    return info.get("estado") == "aceptado"


def folio_cancelado(state: dict, hospital: str) -> bool:
    """Verifica si un hospital está cancelado."""
    info = state.get("hospitales", {}).get(hospital, {})
    return info.get("estado") == "cancelado"


def marcar_modificado(state: dict, hospital: str) -> bool:
    """Cambia un hospital a 'modificado' si no está aceptado/cancelado.
    Devuelve True si pudo cambiar, False si está bloqueado."""
    info = state.get("hospitales", {}).get(hospital)
    if not info:
        return False
    estado = info.get("estado", "vigente")
    if estado == "aceptado":
        return False  # bloqueado
    if estado == "cancelado":
        return False  # cancelado, no cambiar
    info["estado"] = "modificado"
    info["estado_actualizado"] = datetime.now().isoformat(timespec="seconds")
    return True


def reporte_estados(fecha_iso: str | None = None) -> dict:
    """Devuelve un dict con todos los folios agrupados por estado."""
    if not fecha_iso:
        state, fecha_iso = cargar_estado_mas_reciente()
    else:
        state = cargar_estado(fecha_iso)
    if not state:
        return {"error": "no hay pedido del día"}

    grupos = {e: [] for e in ESTADOS}
    for h, info in state.get("hospitales", {}).items():
        if info.get("total", 0) <= 0 and info.get("estado") != "cancelado":
            # Hospital sin productos vigentes pero no cancelado explícitamente
            continue
        estado = info.get("estado", "vigente")
        if estado not in grupos:
            grupos[estado] = []
        grupos[estado].append({
            "tipo": "hospital",
            "destino": h,
            "folio": info.get("folio_remision"),
            "total": info.get("total", 0),
        })

    # Extras
    try:
        from .extras_pedido import cargar_extras
        ex_state = cargar_extras(fecha_iso)
        if ex_state:
            estados_dest = ex_state.get("estados_por_destino") or {}
            folios_dest = ex_state.get("folios_por_destino") or {}
            por_destino = {}
            for e in ex_state.get("extras", []):
                if e.get("cantidad", 0) <= 0:
                    continue
                d = e.get("hospital", "ALMACÉN EHMO")
                por_destino.setdefault(d, 0)
                por_destino[d] += e.get("importe", 0)
            for destino, total in por_destino.items():
                estado = estados_dest.get(destino, "vigente")
                if estado not in grupos:
                    grupos[estado] = []
                grupos[estado].append({
                    "tipo": "extras",
                    "destino": f"{destino} (EXTRA)",
                    "folio": folios_dest.get(destino),
                    "total": total,
                })
    except Exception:
        pass

    return {"fecha": fecha_iso, "fecha_legible": state.get("fecha_legible", fecha_iso),
            "grupos": grupos}


def estado_a_texto(folio_input, fecha_iso: str | None = None) -> str:
    """Texto compacto para el operador con el estado actual de un folio."""
    folio_str = _normalizar_folio(folio_input)
    if not folio_str:
        return f"⚠️ folio '{folio_input}' inválido"
    if not fecha_iso:
        state, fecha_iso = cargar_estado_mas_reciente()
    else:
        state = cargar_estado(fecha_iso)
    if not state:
        return "⚠️ no hay pedido del día"
    found = _buscar_folio(state, fecha_iso, folio_str)
    if not found:
        return f"⚠️ folio {int(folio_str)} no existe"
    if found["tipo"] == "hospital":
        info = found["info"]
        estado = info.get("estado", "vigente")
        return (f"{ESTADO_ICON.get(estado, '?')} folio {int(folio_str)} — "
                f"{found['key']}: {estado.upper()}")
    else:
        ex = found["info"]
        estado = (ex.get("estados_por_destino") or {}).get(found["key"], "vigente")
        return (f"{ESTADO_ICON.get(estado, '?')} folio {int(folio_str)} — "
                f"{found['key']} (EXTRA): {estado.upper()}")
