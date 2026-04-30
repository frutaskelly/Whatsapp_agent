"""Procesa pedidos extraídos de libreta (foto manuscrita) y genera documentos
de surtido SIN PRECIOS.

Usado por el agente SUREÑA Comedores (requires_pesos=True). El AI extrae los
destinos+productos+cantidades de la foto y, tras doble confirmación con el
operador (contenido + fecha), invoca esta función con los datos estructurados.

Output: Pedido <fecha>.pdf (una página por destino) y Lista de Compras
<fecha>.pdf+.xlsx (consolidado para mayoreo). NO genera notas de remisión
porque las cantidades reales (kg) se conocerán al pesar al surtir.
"""
from __future__ import annotations
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import config
from .display_names import corregir_nombre, presentacion_canonica
from .estado_pedido import guardar_estado
from .event_log import log_event
from .lista_compras_pdf import generar_lista_compras_pdf, generar_lista_compras_xlsx
from .pedido_pdf import generar_pdf_pedido

log = logging.getLogger(__name__)


def _normalizar_presentacion(p: str | None) -> str:
    """Normaliza unidades comunes que escribe a mano el operador.

    'kg', 'KG', 'Kgs' → 'Kilo'
    'manojo', 'manojos' → 'Manojo'
    'pz', 'pza', 'pieza', 'piezas' → 'Pieza'
    """
    if not p:
        return "Pieza"
    s = str(p).strip().lower()
    if not s:
        return "Pieza"
    if s.startswith(("kg", "kil")):
        return "Kilo"
    if s.startswith("manoj"):
        return "Manojo"
    if s.startswith(("pz", "pza", "pieza")):
        return "Pieza"
    if s.startswith("caj"):
        return "Caja"
    if s.startswith(("paquete", "paq")):
        return "Paquete"
    if s.startswith(("emp", "bolsa", "bol")):
        return "Bolsa"
    # default: capitalize lo que mandó
    return str(p).strip().capitalize()


def _construir_df_libreta(destinos: list[dict]) -> pd.DataFrame:
    """Convierte la estructura del AI a DataFrame compatible con los generadores.

    Estructura esperada:
      destinos = [
        {"destino": "Comedor Patria",
         "productos": [
           {"alimento": "Papas", "cantidad": 50, "presentacion": "Kilo"},
           ...
         ]},
        ...
      ]
    """
    rows = []
    for d in destinos or []:
        nombre_destino = (d.get("destino") or "").strip()
        if not nombre_destino:
            continue
        # Normalizar nombre con prefijo "Comedor" si aplica (catálogo SUREÑA)
        if not nombre_destino.lower().startswith("comedor "):
            # Si es uno de los 6 conocidos, anteponer "Comedor "
            CONOCIDOS = {"patria", "cci", "6 de junio", "seis de junio",
                          "shanka", "jobo", "copoya"}
            if nombre_destino.lower() in CONOCIDOS:
                nombre_destino = f"Comedor {nombre_destino}"
        for p in d.get("productos", []) or []:
            alimento = (p.get("alimento") or "").strip()
            if not alimento:
                continue
            try:
                cantidad = float(p.get("cantidad") or 0)
            except (TypeError, ValueError):
                cantidad = 0
            if cantidad <= 0:
                continue
            presentacion = _normalizar_presentacion(p.get("presentacion"))
            # Aplicar override de presentación si el alimento tiene uno canónico
            presentacion = presentacion_canonica(alimento, fallback=presentacion)
            # Aplicar correcciones de display de nombres (typos)
            alimento_corr = corregir_nombre(alimento)
            rows.append({
                "UNIDAD": nombre_destino,
                "ALIMENTO": alimento_corr,
                "PRESENTACION": presentacion,
                "CANTIDAD": cantidad,
            })
    if not rows:
        return pd.DataFrame(columns=["UNIDAD", "ALIMENTO", "PRESENTACION", "CANTIDAD"])
    return pd.DataFrame(rows)


def procesar_pedido_libreta(destinos: list[dict],
                             fecha_entrega_iso: str,
                             fecha_entrega_legible: str,
                             output_dir: Path | None = None,
                             agente: dict | None = None,
                             subtitulo: str = "Comedores SUREÑA · para surtir") -> dict:
    """Genera documentos de surtido (sin precios) para un pedido de libreta.

    Args:
      destinos: lista de dicts {destino, productos: [{alimento, cantidad, presentacion}]}
      fecha_entrega_iso: 'YYYY-MM-DD'
      fecha_entrega_legible: '30 de abril'
      output_dir: opcional, default config.PROCESSED_DIR
      agente: dict del agente activo (para metadata + flag requires_pesos)

    Devuelve dict con paths generados y stats:
      {pdf_path, lista_compras_path, lista_compras_xlsx_path,
       drive_pdf, drive_lc_pdf, drive_lc_xlsx,
       n_destinos, n_productos_lineas, df_fyv}
    """
    output_dir = Path(output_dir) if output_dir else Path(config.PROCESSED_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    df_fyv = _construir_df_libreta(destinos)
    if df_fyv.empty:
        log_event("processor",
                  f"⚠️ Libreta sin productos válidos para procesar ({fecha_entrega_iso})",
                  level="warn")
        return {"error": "sin productos válidos en la libreta"}

    n_destinos = df_fyv["UNIDAD"].nunique()
    n_lineas = len(df_fyv)
    log_event("processor",
              f"⚙️ Procesando libreta — {n_destinos} destino(s), {n_lineas} línea(s)",
              {"fecha": fecha_entrega_iso, "agente": (agente or {}).get("id"),
               "destinos": list(df_fyv["UNIDAD"].unique())})

    # 1. PDF imprimible (página por destino, sin precios)
    pdf_path = output_dir / f"Pedido {fecha_entrega_legible} Comedores.pdf"
    try:
        generar_pdf_pedido(df_fyv, fecha_entrega_legible, pdf_path,
                            subtitulo=subtitulo,
                            titulo_principal=None)
    except Exception as e:
        log.exception(f"Error generando PDF imprimible: {e}")
        log_event("processor", f"⚠️ PDF imprimible falló: {e}", level="warn")
        pdf_path = None

    # 2. Lista de Compras consolidada
    lc_pdf_path = output_dir / f"Lista de Compras {fecha_entrega_legible} Comedores.pdf"
    lc_xlsx_path = output_dir / f"Lista de Compras {fecha_entrega_legible} Comedores.xlsx"
    try:
        generar_lista_compras_pdf(df_fyv, fecha_entrega_legible, lc_pdf_path)
    except Exception as e:
        log.exception(f"Error generando lista de compras PDF: {e}")
        log_event("processor", f"⚠️ Lista compras PDF falló: {e}", level="warn")
        lc_pdf_path = None
    try:
        generar_lista_compras_xlsx(df_fyv, fecha_entrega_legible, lc_xlsx_path)
    except Exception as e:
        log.exception(f"Error generando lista de compras XLSX: {e}")
        log_event("processor", f"⚠️ Lista compras XLSX falló: {e}", level="warn")
        lc_xlsx_path = None

    # 3. Persistir estado del día — marcado requires_pesos para que el flujo
    #    de notas espere a que el operador reporte los kg pesados al surtir.
    try:
        guardar_estado(
            fecha_entrega_iso, fecha_entrega_legible, df_fyv,
            folios={},
            extra={
                "requires_pesos": True,
                "fuente": "libreta",
                "agente_id": (agente or {}).get("id"),
                "creado_de_libreta": datetime.now().isoformat(timespec="seconds"),
            },
        )
    except Exception as e:
        log.exception(f"Error guardando estado del día (libreta): {e}")
        log_event("processor", f"⚠️ Estado libreta falló: {e}", level="warn")

    # 4. Subir a Drive (subcarpeta por fecha)
    drive_pdf = drive_lc_pdf = drive_lc_xlsx = None
    try:
        from .drive_uploader import upload_file as drive_upload
        if pdf_path:
            drive_pdf = drive_upload(pdf_path, subfolder=fecha_entrega_iso)
        if lc_pdf_path:
            drive_lc_pdf = drive_upload(lc_pdf_path, subfolder=fecha_entrega_iso)
        if lc_xlsx_path:
            drive_lc_xlsx = drive_upload(lc_xlsx_path, subfolder=fecha_entrega_iso)
    except Exception as e:
        log.warning(f"Drive upload falló parcial/total: {e}")

    log_event("processor",
              f"✓ Documentos de surtido generados (libreta) — {n_destinos} destinos",
              {"pdf": pdf_path.name if pdf_path else None,
               "lista_compras": lc_pdf_path.name if lc_pdf_path else None,
               "fecha": fecha_entrega_iso})

    return {
        "pdf_path": pdf_path,
        "lista_compras_path": lc_pdf_path,
        "lista_compras_xlsx_path": lc_xlsx_path,
        "drive_pdf": drive_pdf,
        "drive_lc_pdf": drive_lc_pdf,
        "drive_lc_xlsx": drive_lc_xlsx,
        "fecha_iso": fecha_entrega_iso,
        "fecha_legible": fecha_entrega_legible,
        "n_destinos": n_destinos,
        "n_productos_lineas": n_lineas,
        "destinos": sorted(df_fyv["UNIDAD"].unique()),
    }


# ──────────────────────────────────────────────────────────────────────────
# PASO 2 — Aplicar pesos reportados al state y emitir notas de remisión
# ──────────────────────────────────────────────────────────────────────────

def _resolver_destino_canonico(input_destino: str, hospitales_estado: list[str]) -> str | None:
    """Match flexible: 'patria' → 'Comedor Patria', etc."""
    if not input_destino:
        return None
    s = str(input_destino).lower().strip()
    # Match exacto
    for h in hospitales_estado:
        if h.lower() == s:
            return h
    # Substring directo
    for h in hospitales_estado:
        if s in h.lower() or h.lower() in s:
            return h
    # Sin la palabra "comedor"
    s_clean = s.replace("comedor ", "").strip()
    for h in hospitales_estado:
        h_clean = h.lower().replace("comedor ", "").strip()
        if s_clean == h_clean or s_clean in h_clean:
            return h
    return None


def _resolver_alimento_en_productos(input_alimento: str, productos: list[dict]) -> dict | None:
    """Encuentra el producto en la lista que coincide con el nombre dado."""
    if not input_alimento:
        return None
    s = str(input_alimento).lower().strip()
    # Match por substring de palabra significativa (4+ chars)
    palabras = [w for w in s.split() if len(w) >= 4]
    if not palabras:
        palabras = [s]
    mejor = None
    mejor_score = 0
    for p in productos:
        nombre = (p.get("alimento") or "").lower()
        score = sum(1 for w in palabras if w in nombre)
        if score > mejor_score:
            mejor_score = score
            mejor = p
    return mejor if mejor_score >= 1 else None


def aplicar_pesos(pesos: list[dict], fecha_iso: str,
                   cliente: str = "SURENA",
                   output_dir: Path | None = None) -> dict:
    """Aplica pesos reportados al state del día y emite notas de remisión.

    pesos = [
        {"destino": "Comedor Patria", "alimento": "Espinacas", "kg": 4.5},
        {"destino": "Patria", "alimento": "Sandías", "kg": 18},
        {"destino": "Comedor CCI", "alimento": "Espinacas", "kg": 5},
        ...
    ]

    Para cada peso:
      - Resuelve destino y producto contra el state actual
      - Reemplaza cantidad y presentación → kg / Kilo
      - Recalcula importe usando precio de la lista del cliente
    Si todos los productos no-kg ahora tienen kg, quita el flag
    requires_pesos del state. Genera notas de remisión + relación.
    """
    from .estado_pedido import (cargar_estado, _state_file, _state_lock,
                                  estado_a_dataframe)
    from .nota_remision import generar_notas_remision
    import json as _json

    output_dir = Path(output_dir) if output_dir else Path(config.PROCESSED_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    state = cargar_estado(fecha_iso)
    if not state:
        return {"error": f"No hay estado para {fecha_iso}"}

    cambios = []
    no_encontrados = []
    hospitales_estado = list(state.get("hospitales", {}).keys())

    for peso in pesos or []:
        destino_input = (peso.get("destino") or "").strip()
        alimento_input = (peso.get("alimento") or "").strip()
        try:
            kg = float(peso.get("kg") or 0)
        except (TypeError, ValueError):
            kg = 0
        if kg <= 0:
            no_encontrados.append(f"{destino_input}: {alimento_input} (kg inválido)")
            continue

        destino_resuelto = _resolver_destino_canonico(destino_input, hospitales_estado)
        if not destino_resuelto:
            no_encontrados.append(f"{destino_input} (destino no encontrado)")
            continue

        info = state["hospitales"][destino_resuelto]
        producto = _resolver_alimento_en_productos(alimento_input, info.get("productos", []))
        if not producto:
            no_encontrados.append(f"{destino_resuelto}: {alimento_input}")
            continue

        # Buscar el precio en la lista del cliente
        from .pricing import buscar_precio
        match = buscar_precio(producto["alimento"])
        precio_unit = float(match["precio"]) if match else 0.0

        cant_anterior = producto.get("cantidad")
        pres_anterior = producto.get("presentacion")
        importe_anterior = producto.get("importe", 0)

        producto["cantidad"] = kg
        producto["presentacion"] = "Kilo"
        producto["precio_unitario"] = precio_unit
        producto["importe"] = round(kg * precio_unit, 2)
        producto["tiene_precio"] = match is not None

        cambios.append({
            "destino": destino_resuelto,
            "alimento": producto["alimento"],
            "cantidad_anterior": cant_anterior,
            "presentacion_anterior": pres_anterior,
            "kg_aplicado": kg,
            "precio_unitario": precio_unit,
            "importe_nuevo": producto["importe"],
            "tiene_precio": match is not None,
        })

    # Recalcular subtotal/total por destino y estado
    now = datetime.now().isoformat(timespec="seconds")
    for hospital, info in state["hospitales"].items():
        productos_activos = [p for p in info["productos"] if p["cantidad"] > 0]
        info["subtotal"] = round(sum(p["importe"] for p in productos_activos), 2)
        info["total"] = info["subtotal"]
        if info.get("estado") in (None, "vigente"):
            info["estado"] = "modificado"
            info["estado_actualizado"] = now

    # Verificar si todos los productos quedaron en kg → quitar requires_pesos
    todos_kg = True
    for hospital, info in state["hospitales"].items():
        for p in info["productos"]:
            if p.get("cantidad", 0) > 0 and (p.get("presentacion") or "").lower() not in ("kilo", "kg"):
                todos_kg = False
                break
        if not todos_kg:
            break
    if todos_kg:
        state.pop("requires_pesos", None)

    state["ultima_modificacion"] = now
    state.setdefault("ajustes", []).append({
        "timestamp": now,
        "tipo": "registrar_pesos",
        "cambios": cambios,
        "no_encontrados": no_encontrados,
    })

    with _state_lock:
        _state_file(fecha_iso).write_text(
            _json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    log_event("processor",
              f"⚖️ Pesos aplicados al {fecha_iso}: {len(cambios)} producto(s) actualizado(s)",
              {"fecha": fecha_iso, "cambios": len(cambios),
               "no_encontrados": no_encontrados, "todos_kg": todos_kg})

    # Generar notas de remisión solo si todos los productos están en kg
    notas_path = None
    notas_info = None
    drive_notas = None
    relacion_path = None
    drive_relacion = None
    if todos_kg:
        df = estado_a_dataframe(state)
        df = df[df["CANTIDAD"] > 0]
        fecha_legible = state.get("fecha_legible", fecha_iso)
        notas_path = output_dir / f"Notas Remisión {fecha_legible} Comedores.pdf"
        try:
            folios_existentes = {h: hi.get("folio_remision")
                                  for h, hi in state["hospitales"].items()
                                  if hi.get("folio_remision")}
            notas_info = generar_notas_remision(
                df, fecha_legible, notas_path,
                folios_existentes=folios_existentes,
                cliente=cliente,
            )
            # Persistir folios asignados al state
            for h, folio in (notas_info.get("folios") or {}).items():
                if h in state["hospitales"]:
                    state["hospitales"][h]["folio_remision"] = folio
            with _state_lock:
                _state_file(fecha_iso).write_text(
                    _json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            log.exception(f"Error generando notas tras pesos: {e}")
            log_event("processor", f"⚠️ Notas tras pesos fallaron: {e}", level="warn")

        # Relación de documentos
        try:
            from .relacion_documentos import generar_relacion_dia
            rel = generar_relacion_dia(fecha_iso, fecha_legible=fecha_legible)
            if rel and not rel.get("error"):
                relacion_path = rel["output_path"]
        except Exception as e:
            log.exception(f"Error relación tras pesos: {e}")

        # Drive
        try:
            from .drive_uploader import upload_file as drive_upload
            if notas_path and notas_path.exists():
                drive_notas = drive_upload(notas_path, subfolder=fecha_iso)
            if relacion_path:
                drive_relacion = drive_upload(relacion_path, subfolder=fecha_iso)
        except Exception:
            pass

    return {
        "ok": True,
        "fecha_iso": fecha_iso,
        "cambios": cambios,
        "no_encontrados": no_encontrados,
        "todos_kg": todos_kg,
        "notas_path": notas_path,
        "drive_notas": drive_notas,
        "relacion_path": relacion_path,
        "drive_relacion": drive_relacion,
        "total_dia": round(sum(h.get("total", 0) for h in state["hospitales"].values()), 2),
    }

