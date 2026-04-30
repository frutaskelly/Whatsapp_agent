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
