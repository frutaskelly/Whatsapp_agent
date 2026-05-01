"""Aplicar ajustes de entrega al pedido del día y regenerar la nota corregida.

Flujo:
  1. Personal entrega los pedidos físicamente.
  2. Si faltó algún producto, manda mensaje al agente:
     "Faltó en Comitán: jitomate 5kg, papa blanca 2kg"
  3. Claude detecta intencion=ajuste_entrega y devuelve datos estructurados.
  4. Este módulo:
     a) Carga el estado del día.
     b) Encuentra el hospital (fuzzy match con catálogo conocido).
     c) Para cada ajuste, encuentra el producto y resta/elimina cantidad.
     d) Recalcula totales, persiste el estado actualizado.
     e) Regenera la nota de remisión con precios (PDF nuevo).
     f) Sube a Drive con sufijo _corregida_HHMMSS.
  5. Devuelve resumen del cambio para mostrar al usuario.
"""
import json
import logging
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path

from . import config
from .event_log import log_event
from .estado_pedido import (
    cargar_estado, cargar_estado_mas_reciente, estado_a_dataframe, _state_file, _state_lock,
)
from .pedido_processor import HOSPITALES_CONOCIDOS_SI, _hospital_canonico
from .nota_remision import generar_notas_remision
from .drive_uploader import upload_file as drive_upload
from .pedido_processor import fecha_a_iso

log = logging.getLogger(__name__)


def _norm(s: str) -> str:
    s = str(s or "").lower().strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"\s+", " ", s)
    return s


def _resolver_hospital(hospital_input: str, hospitales_estado: list[str]) -> str | None:
    """Encuentra el hospital del estado que coincide con el input del usuario.

    1. Match contra HOSPITALES_CONOCIDOS_SI (catálogo oficial).
    2. Match parcial por substring contra los hospitales en el estado.
    """
    canonical = _hospital_canonico(hospital_input)
    if canonical and canonical in hospitales_estado:
        return canonical
    # Fallback: substring contra hospitales del estado
    h_norm = _norm(hospital_input)
    for h in hospitales_estado:
        if h_norm in _norm(h) or _norm(h) in h_norm:
            return h
    # Última opción: alguna palabra significativa
    h_words = [w for w in h_norm.split() if len(w) > 3]
    for h in hospitales_estado:
        h_words_b = _norm(h).split()
        for w in h_words:
            if w in h_words_b:
                return h
    return None


def _encontrar_producto(nombre_input: str, productos: list[dict]) -> dict | None:
    """Busca un producto del estado que matchee con el input (substring normalizado)."""
    n = _norm(nombre_input)
    if not n:
        return None
    candidatos = []
    for p in productos:
        a = _norm(p["alimento"])
        if n in a or a in n:
            candidatos.append((max(len(n), len(a) - len(a.replace(n, ""))), p))
            continue
        # Palabra a palabra
        n_words = [w for w in n.split() if len(w) > 3]
        if any(w in a for w in n_words):
            candidatos.append((1, p))
    if not candidatos:
        return None
    # Devuelve el match con mayor score
    candidatos.sort(key=lambda x: -x[0])
    return candidatos[0][1]


def aplicar_ajustes(hospital_input: str, ajustes: list[dict],
                     fecha_iso: str | None = None,
                     solo_estado: bool = False) -> dict:
    """Aplica ajustes de "no entregado" al estado y regenera notas.

    `ajustes` = [{"alimento": "jitomate", "cantidad_no_entregada": 5}, ...]
       cantidad_no_entregada puede ser numérica o "todo" (= eliminar).
    `solo_estado=True`: actualiza JSON pero NO regenera PDFs ni sube a Drive.
       Útil para replays históricos donde los PDFs originales ya existen.

    Devuelve:
      {
        "ok": bool,
        "error": str | None,
        "hospital_resuelto": str,
        "cambios": [...],
        "subtotal_anterior": float,
        "subtotal_nuevo": float,
        "ahorro": float,
        "drive_link": str | None,
      }
    """
    # Cargar estado
    if fecha_iso:
        state = cargar_estado(fecha_iso)
    else:
        state, fecha_iso = cargar_estado_mas_reciente()
    if not state:
        return {"ok": False, "error": "No hay pedido del día procesado todavía.",
                "cambios": []}

    hospitales_estado = list(state["hospitales"].keys())
    hospital_resuelto = _resolver_hospital(hospital_input, hospitales_estado)
    if not hospital_resuelto:
        return {"ok": False,
                "error": f"No reconocí el destino '{hospital_input}'. "
                         f"Destinos del día: {hospitales_estado}",
                "cambios": []}

    info_hosp = state["hospitales"][hospital_resuelto]
    # Bloquear si el folio está aceptado para facturar
    if info_hosp.get("estado") == "aceptado":
        return {"ok": False,
                "error": f"❌ El folio {int(info_hosp.get('folio_remision','0'))} de "
                         f"{hospital_resuelto} está ACEPTADO para facturar. "
                         f"Cancela la aceptación primero con 'reactiva el folio X'.",
                "cambios": []}
    subtotal_anterior = info_hosp["subtotal"]

    cambios = []
    no_encontrados = []
    for aj in ajustes:
        alimento_input = aj.get("alimento", "")
        cant_no_entregada = aj.get("cantidad_no_entregada", "todo")

        producto = _encontrar_producto(alimento_input, info_hosp["productos"])
        if not producto:
            no_encontrados.append(alimento_input)
            continue

        cantidad_anterior = producto["cantidad"]

        # Determinar cantidad nueva
        if isinstance(cant_no_entregada, str) and cant_no_entregada.lower() in ("todo", "completo", "todos", "completa"):
            cantidad_nueva = 0
        else:
            try:
                resta = float(cant_no_entregada)
                cantidad_nueva = max(0, cantidad_anterior - resta)
            except (TypeError, ValueError):
                no_encontrados.append(f"{alimento_input} (cantidad no parseable)")
                continue

        # Aplicar
        producto["cantidad"] = cantidad_nueva
        producto["importe"] = round(cantidad_nueva * producto["precio_unitario"], 2)

        cambios.append({
            "alimento": producto["alimento"],
            "presentacion": producto["presentacion"],
            "cantidad_anterior": cantidad_anterior,
            "cantidad_nueva": cantidad_nueva,
            "diferencia_cantidad": cantidad_nueva - cantidad_anterior,
            "importe_anterior": round(cantidad_anterior * producto["precio_unitario"], 2),
            "importe_nuevo": producto["importe"],
            "diferencia_importe": producto["importe"] - cantidad_anterior * producto["precio_unitario"],
        })

    # Recalcular subtotales del hospital
    productos_activos = [p for p in info_hosp["productos"] if p["cantidad"] > 0]
    info_hosp["subtotal"] = round(sum(p["importe"] for p in productos_activos), 2)
    info_hosp["total"] = info_hosp["subtotal"]
    # Auto-transition: vigente → modificado
    if info_hosp.get("estado") in (None, "vigente"):
        info_hosp["estado"] = "modificado"
        info_hosp["estado_actualizado"] = datetime.now().isoformat(timespec="seconds")
    # Si total quedó en 0 → cancelado
    if info_hosp["total"] <= 0:
        info_hosp["estado"] = "cancelado"
        info_hosp["estado_actualizado"] = datetime.now().isoformat(timespec="seconds")

    # Registrar el ajuste en el log del estado
    state["ultima_modificacion"] = datetime.now().isoformat(timespec="seconds")
    state["ajustes"].append({
        "timestamp": state["ultima_modificacion"],
        "hospital": hospital_resuelto,
        "cambios": cambios,
        "no_encontrados": no_encontrados,
        "diferencia_total": info_hosp["subtotal"] - subtotal_anterior,
    })

    # Guardar estado
    with _state_lock:
        _state_file(fecha_iso).write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    log_event("processor",
              f"📝 Ajuste aplicado en {hospital_resuelto}: {len(cambios)} cambio(s), "
              f"ahorro ${(subtotal_anterior - info_hosp['subtotal']):,.2f}",
              {"hospital": hospital_resuelto, "cambios": cambios,
               "subtotal_anterior": subtotal_anterior, "subtotal_nuevo": info_hosp["subtotal"]})

    if solo_estado:
        return {
            "ok": True,
            "error": None,
            "hospital_resuelto": hospital_resuelto,
            "cambios": cambios,
            "no_encontrados": no_encontrados,
            "subtotal_anterior": subtotal_anterior,
            "subtotal_nuevo": info_hosp["subtotal"],
            "ahorro": round(subtotal_anterior - info_hosp["subtotal"], 2),
            "drive_link": None,
        }

    # Regenerar nota de remisión SOLO del hospital afectado (no las 12)
    df_actualizado = estado_a_dataframe(state)
    df_solo_hospital = df_actualizado[df_actualizado["UNIDAD"] == hospital_resuelto]
    fecha_legible = state["fecha_legible"]
    ts = datetime.now().strftime("%H%M%S")
    # Nombre corto del hospital para el archivo
    hospital_short = hospital_resuelto.replace("Hospital ", "").replace(
        "Básico Comunitario ", "HBC ")[:40].strip()
    output_path = (config.PROCESSED_DIR /
                   f"Nota CORREGIDA {hospital_short} {fecha_legible} {ts}.pdf")
    drive_link = None
    folios_existentes = {h: hi.get("folio_remision")
                          for h, hi in state["hospitales"].items()
                          if hi.get("folio_remision")}
    try:
        # Si el hospital quedó sin productos (todo cancelado), no generamos PDF
        if df_solo_hospital.empty:
            log_event("processor",
                      f"⚠️ {hospital_resuelto} quedó sin productos tras ajuste — sin nota nueva",
                      level="warn")
        else:
            info = generar_notas_remision(df_solo_hospital, fecha_legible, output_path,
                                           folios_existentes=folios_existentes)
            # Persistir folios (caso edge: ningun folio existia, se asignan nuevos)
            for h, folio in info.get("folios", {}).items():
                if h in state["hospitales"]:
                    state["hospitales"][h]["folio_remision"] = folio
            with _state_lock:
                _state_file(fecha_iso).write_text(
                    json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            drive_info = drive_upload(output_path, subfolder=fecha_iso)
            if drive_info:
                drive_link = drive_info["link"]
    except Exception as e:
        log.exception(f"Error regenerando nota tras ajuste: {e}")
        log_event("processor", f"⚠️ Error regenerando nota corregida: {e}", level="warn")

    # Regenerar también la Relación de Documentos del día (totales cambiaron)
    drive_relacion_link = None
    drive_relacion_pdf_link = None
    try:
        from .relacion_documentos import generar_relacion_dia, generar_relacion_dia_pdf
        ts_rel = datetime.now().strftime("%H%M%S")
        rel = generar_relacion_dia(fecha_iso, fecha_legible=fecha_legible,
                                    output_path=config.PROCESSED_DIR /
                                    f"Relación Documentos {fecha_legible} ACTUALIZADA {ts_rel}.xlsx")
        if rel and not rel.get("error"):
            d = drive_upload(rel["output_path"], subfolder=fecha_iso)
            if d: drive_relacion_link = d["link"]
        rel_pdf = generar_relacion_dia_pdf(fecha_iso, fecha_legible=fecha_legible,
                                            output_path=config.PROCESSED_DIR /
                                            f"Relación Documentos {fecha_legible} ACTUALIZADA {ts_rel}.pdf")
        if rel_pdf and not rel_pdf.get("error"):
            d = drive_upload(rel_pdf["output_path"], subfolder=fecha_iso)
            if d: drive_relacion_pdf_link = d["link"]
    except Exception as e:
        log.exception(f"Error regenerando relación tras ajuste: {e}")
        log_event("processor", f"⚠️ Error regenerando relación: {e}", level="warn")

    return {
        "ok": True,
        "error": None,
        "hospital_resuelto": hospital_resuelto,
        "cambios": cambios,
        "no_encontrados": no_encontrados,
        "subtotal_anterior": subtotal_anterior,
        "subtotal_nuevo": info_hosp["subtotal"],
        "ahorro": round(subtotal_anterior - info_hosp["subtotal"], 2),
        "drive_link": drive_link,
        "drive_relacion_link": drive_relacion_link,
        "drive_relacion_pdf_link": drive_relacion_pdf_link,
        "fecha": fecha_iso,
    }
