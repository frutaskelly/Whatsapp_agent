"""Aplicar modificaciones del cliente al pedido del día (pre-surtido).

A diferencia de `ajuste_entrega.py` (post-surtido, solo regenera notas),
este módulo se invoca cuando el cliente EHMO o Cristian cambia el pedido
ANTES de que el personal salga a surtir. Por eso regenera:
  - PDF imprimible (sin precios) — la hoja de surtido del personal
  - Notas de remisión (con precios) — para el cobro

Operaciones soportadas:
  - "agregar":  sumar a una cantidad existente o crear producto nuevo
  - "restar":   reducir cantidad
  - "cancelar": cantidad → 0 (elimina del pedido)
  - "fijar":    establecer cantidad exacta
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from . import config
from .event_log import log_event
from .estado_pedido import (
    cargar_estado, cargar_estado_mas_reciente, estado_a_dataframe,
    _state_file, _state_lock,
)
from .ajuste_entrega import _resolver_hospital, _encontrar_producto, _norm
from .pricing import buscar_precio
from .nota_remision import generar_notas_remision
from .pedido_pdf import generar_pdf_pedido
from .drive_uploader import upload_file as drive_upload
from .pedido_processor import fecha_a_iso

log = logging.getLogger(__name__)


def _aplicar_modificacion_a_producto(producto: dict, operacion: str,
                                      cantidad: float | None) -> tuple[float, float]:
    """Aplica una modificación in-place al dict de producto.

    Devuelve (cantidad_anterior, cantidad_nueva).
    """
    cant_ant = float(producto.get("cantidad", 0))
    op = (operacion or "").lower()
    if op == "agregar":
        cant_nva = cant_ant + (cantidad or 0)
    elif op == "restar":
        cant_nva = max(0.0, cant_ant - (cantidad or 0))
    elif op in ("cancelar", "eliminar"):
        cant_nva = 0.0
    elif op == "fijar":
        cant_nva = float(cantidad or 0)
    else:
        cant_nva = cant_ant  # no-op
    producto["cantidad"] = cant_nva
    producto["importe"] = round(cant_nva * float(producto.get("precio_unitario", 0)), 2)
    return cant_ant, cant_nva


def aplicar_modificaciones(hospital_input: str, modificaciones: list[dict],
                            fecha_iso: str | None = None) -> dict:
    """Aplica modificaciones del cliente al estado y regenera archivos.

    `modificaciones` = [
      {"operacion": "agregar", "alimento": "jitomate", "cantidad": 5},
      {"operacion": "cancelar", "alimento": "papaya"},
      ...
    ]
    """
    if fecha_iso:
        state = cargar_estado(fecha_iso)
    else:
        state, fecha_iso = cargar_estado_mas_reciente()
    if not state:
        return {"ok": False, "error": "No hay pedido del día procesado todavía."}

    hospitales_estado = list(state["hospitales"].keys())
    hospital_resuelto = _resolver_hospital(hospital_input, hospitales_estado)
    if not hospital_resuelto:
        return {"ok": False,
                "error": f"No reconocí el destino '{hospital_input}'. "
                         f"Destinos del día: {hospitales_estado}"}

    info_hosp = state["hospitales"][hospital_resuelto]
    # Verificar si el folio está bloqueado (aceptado para facturar)
    if info_hosp.get("estado") == "aceptado":
        return {"ok": False,
                "error": f"❌ El folio {int(info_hosp.get('folio_remision','0'))} de {hospital_resuelto} "
                         f"está ACEPTADO para facturar. Cancela la aceptación primero "
                         f"con 'reactiva el folio X'."}
    subtotal_anterior = info_hosp["subtotal"]

    cambios = []
    no_encontrados = []
    for mod in modificaciones:
        operacion = (mod.get("operacion") or "").lower()
        alimento_input = mod.get("alimento", "")
        cantidad = mod.get("cantidad")

        # AGREGAR puede crear un producto nuevo si no existe
        producto = _encontrar_producto(alimento_input, info_hosp["productos"])
        if not producto:
            if operacion == "agregar":
                # Crear línea nueva — busca precio en lista, pero respeta el
                # nombre que envió el cliente (no lo renombra al canónico).
                match = buscar_precio(alimento_input)
                if not match:
                    no_encontrados.append(f"{alimento_input} (sin precio en lista)")
                    continue
                producto = {
                    "alimento": alimento_input,  # mantener nombre del cliente
                    "presentacion": match.get("unidad", "KG"),
                    "cantidad": 0,
                    "cantidad_original": 0,
                    "precio_unitario": match["precio"],
                    "importe": 0.0,
                    "tiene_precio": True,
                    "agregado_por_modificacion": True,
                }
                info_hosp["productos"].append(producto)
            else:
                no_encontrados.append(alimento_input)
                continue

        cant_ant, cant_nva = _aplicar_modificacion_a_producto(producto, operacion, cantidad)
        cambios.append({
            "operacion": operacion,
            "alimento": producto["alimento"],
            "presentacion": producto["presentacion"],
            "cantidad_anterior": cant_ant,
            "cantidad_nueva": cant_nva,
            "diferencia_cantidad": cant_nva - cant_ant,
            "precio_unitario": producto["precio_unitario"],
            "importe_anterior": round(cant_ant * producto["precio_unitario"], 2),
            "importe_nuevo": producto["importe"],
            "diferencia_importe": round((cant_nva - cant_ant) * producto["precio_unitario"], 2),
        })

    # Recalcular subtotal del hospital con productos vivos (cantidad > 0)
    productos_activos = [p for p in info_hosp["productos"] if p["cantidad"] > 0]
    info_hosp["subtotal"] = round(sum(p["importe"] for p in productos_activos), 2)
    info_hosp["total"] = info_hosp["subtotal"]
    # Auto-transition de estado: vigente/None → modificado (a menos que esté cancelado o aceptado)
    if info_hosp.get("estado") in (None, "vigente"):
        info_hosp["estado"] = "modificado"
        info_hosp["estado_actualizado"] = datetime.now().isoformat(timespec="seconds")
    # Si quedó en total 0 → cancelado automático
    if info_hosp["total"] <= 0:
        info_hosp["estado"] = "cancelado"
        info_hosp["estado_actualizado"] = datetime.now().isoformat(timespec="seconds")

    # Registrar la modificación
    state["ultima_modificacion"] = datetime.now().isoformat(timespec="seconds")
    state["ajustes"].append({
        "tipo": "modificacion_pedido",
        "timestamp": state["ultima_modificacion"],
        "hospital": hospital_resuelto,
        "cambios": cambios,
        "no_encontrados": no_encontrados,
        "diferencia_total": round(info_hosp["subtotal"] - subtotal_anterior, 2),
    })

    with _state_lock:
        _state_file(fecha_iso).write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    log_event("processor",
              f"📝 Modificación pre-surtido en {hospital_resuelto}: {len(cambios)} cambio(s)",
              {"hospital": hospital_resuelto, "cambios": cambios,
               "subtotal_anterior": subtotal_anterior, "subtotal_nuevo": info_hosp["subtotal"]})

    return {
        "ok": True,
        "error": None,
        "hospital_resuelto": hospital_resuelto,
        "cambios": cambios,
        "no_encontrados": no_encontrados,
        "subtotal_anterior": subtotal_anterior,
        "subtotal_nuevo": info_hosp["subtotal"],
        "diferencia": round(info_hosp["subtotal"] - subtotal_anterior, 2),
        "fecha": fecha_iso,
    }


def regenerar_archivos(fecha_iso: str) -> dict:
    """Regenera PDF imprimible y notas con el estado actual del día.

    Sube ambos a Drive en la subcarpeta de la fecha. Devuelve los links.
    """
    state = cargar_estado(fecha_iso)
    if not state:
        return {"error": f"No hay estado para {fecha_iso}"}

    df = estado_a_dataframe(state)
    fecha_legible = state.get("fecha_legible", fecha_iso)
    ts = datetime.now().strftime("%H%M%S")

    # PDF imprimible (sin precios)
    pdf_imprimible_path = config.PROCESSED_DIR / f"Pedido {fecha_legible} ACTUALIZADO {ts}.pdf"
    drive_pdf = None
    try:
        generar_pdf_pedido(df, fecha_legible, pdf_imprimible_path)
        drive_pdf = drive_upload(pdf_imprimible_path, subfolder=fecha_iso)
    except Exception as e:
        log.exception(f"Error regenerando PDF imprimible: {e}")
        log_event("processor", f"⚠️ Error regenerando PDF imprimible: {e}", level="warn")

    # Lista de compras consolidada (también se actualiza pre-surtido) — PDF + Excel
    from .lista_compras_pdf import generar_lista_compras_pdf, generar_lista_compras_xlsx
    lista_compras_path = config.PROCESSED_DIR / f"Lista de Compras {fecha_legible} ACTUALIZADA {ts}.pdf"
    lista_compras_xlsx_path = config.PROCESSED_DIR / f"Lista de Compras {fecha_legible} ACTUALIZADA {ts}.xlsx"
    drive_lista_compras = None
    drive_lista_compras_xlsx = None
    try:
        generar_lista_compras_pdf(df, fecha_legible, lista_compras_path)
        drive_lista_compras = drive_upload(lista_compras_path, subfolder=fecha_iso)
    except Exception as e:
        log.exception(f"Error regenerando lista de compras PDF: {e}")
        log_event("processor", f"⚠️ Error regenerando lista de compras PDF: {e}", level="warn")
    try:
        generar_lista_compras_xlsx(df, fecha_legible, lista_compras_xlsx_path)
        drive_lista_compras_xlsx = drive_upload(lista_compras_xlsx_path, subfolder=fecha_iso)
    except Exception as e:
        log.exception(f"Error regenerando lista de compras Excel: {e}")
        log_event("processor", f"⚠️ Error regenerando lista de compras Excel: {e}", level="warn")

    # Notas con precios — reutilizar folios del estado
    folios_existentes = {h: info.get("folio_remision")
                          for h, info in state["hospitales"].items()
                          if info.get("folio_remision")}
    notas_path = config.PROCESSED_DIR / f"Notas Remisión {fecha_legible} ACTUALIZADA {ts}.pdf"
    drive_notas = None
    notas_total = 0
    try:
        info = generar_notas_remision(df, fecha_legible, notas_path,
                                       folios_existentes=folios_existentes)
        notas_total = info["total_general"]
        # Persistir cualquier folio nuevo que se haya asignado
        for h, folio in info.get("folios", {}).items():
            if h in state["hospitales"]:
                state["hospitales"][h]["folio_remision"] = folio
        from .estado_pedido import _state_file, _state_lock
        import json as _json
        with _state_lock:
            _state_file(fecha_iso).write_text(
                _json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        drive_notas = drive_upload(notas_path, subfolder=fecha_iso)
    except Exception as e:
        log.exception(f"Error regenerando notas: {e}")
        log_event("processor", f"⚠️ Error regenerando notas: {e}", level="warn")

    total_general = sum(h["total"] for h in state["hospitales"].values())
    # Relación de documentos (Excel + PDF) — actualizada con totales nuevos
    from .relacion_documentos import generar_relacion_dia, generar_relacion_dia_pdf
    drive_relacion = None
    drive_relacion_pdf = None
    try:
        rel = generar_relacion_dia(fecha_iso, fecha_legible=fecha_legible,
                                    output_path=config.PROCESSED_DIR /
                                    f"Relación Documentos {fecha_legible} ACTUALIZADA {ts}.xlsx")
        if rel and not rel.get("error"):
            drive_relacion = drive_upload(rel["output_path"], subfolder=fecha_iso)
        rel_pdf = generar_relacion_dia_pdf(fecha_iso, fecha_legible=fecha_legible,
                                            output_path=config.PROCESSED_DIR /
                                            f"Relación Documentos {fecha_legible} ACTUALIZADA {ts}.pdf")
        if rel_pdf and not rel_pdf.get("error"):
            drive_relacion_pdf = drive_upload(rel_pdf["output_path"], subfolder=fecha_iso)
    except Exception as e:
        log.exception(f"Error regenerando relación: {e}")
        log_event("processor", f"⚠️ Error regenerando relación: {e}", level="warn")

    return {
        "drive_pdf_imprimible": drive_pdf,
        "drive_lista_compras": drive_lista_compras,
        "drive_lista_compras_xlsx": drive_lista_compras_xlsx,
        "drive_notas": drive_notas,
        "drive_relacion": drive_relacion,
        "drive_relacion_pdf": drive_relacion_pdf,
        "total_general": total_general,
        "notas_total_generado": notas_total,
    }
