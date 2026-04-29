"""Reconstrucción del JSON de estado para un día sin snapshot guardado.

Útil cuando un día se procesó antes de que existiera el sistema de estado
persistente, o cuando el JSON se perdió. Lee el Excel BD original del cliente
y opcionalmente la "Relación Documentos" del día para extraer los folios ya
asignados a cada hospital.

NO regenera PDFs ni sube nada a Drive — solo escribe el JSON de estado en
storage/pedidos_dia/<fecha-iso>.json.

Uso típico (CLI):
    python -m app.rebuild_estado \\
        "01_Pedidos_Cliente/Pedido 27 de abril original.xlsx" \\
        2026-04-27 "27 de abril" \\
        --relacion "whatsapp_agent/storage/processed/Relación Documentos 27 de abril 115942.xlsx"
"""
import argparse
import logging
import re
from pathlib import Path
import pandas as pd

from .pedido_processor import (
    EXCLUIDOS_KW, CAMBIO_KW, IGNORAR_KW,
    _is_excluido, _is_cambio, _is_ignorar, _hospital_canonico,
    _es_lote_5, _es_lote_1,
)
from .estado_pedido import guardar_estado, _state_file
from .event_log import log_event

log = logging.getLogger(__name__)


def extraer_folios_de_relacion(relacion_xlsx: Path) -> dict[str, str]:
    """Lee una Relación Documentos del día y devuelve {hospital: folio_padded}.

    El xlsx tiene encabezados en fila 3, datos a partir de la fila 4.
    Columnas relevantes: HOSPITAL (col 4), # REMISIÓN/PEDIDO (col 5).
    Se ignoran filas tipo 'TOTAL DEL DÍA' y entradas de EXTRA (almacén).
    """
    relacion_xlsx = Path(relacion_xlsx)
    if not relacion_xlsx.exists():
        log.warning(f"Relación no encontrada: {relacion_xlsx}")
        return {}

    xl = pd.ExcelFile(relacion_xlsx)
    df = pd.read_excel(xl, sheet_name=xl.sheet_names[0], header=None)

    folios: dict[str, str] = {}
    for _, row in df.iterrows():
        hospital = row.get(4)
        folio_raw = row.get(5)
        if not isinstance(hospital, str) or not str(hospital).strip():
            continue
        h = str(hospital).strip()
        # Saltar headers, totales, y EXTRA
        if h.upper().startswith(("HOSPITAL", "REMISION", "TOTAL")) and "GENERAL" not in h.upper() \
                and "BÁSICO" not in h.upper() and "BASICO" not in h.upper():
            # Es probable cabecera "HOSPITAL" → saltar
            if h.upper() == "HOSPITAL":
                continue
        if "EXTRA" in h.upper() or "ALMAC" in h.upper():
            continue
        # Folio puede venir como número (14) o string ("14") o ya padded
        try:
            folio_int = int(float(str(folio_raw).strip()))
        except (TypeError, ValueError):
            continue
        folios[h] = f"{folio_int:010d}"
    return folios


def _resolver_hospital_canonico(nombre_relacion: str, nombres_bd: list[str]) -> str | None:
    """Resuelve nombre de la relación a uno de los nombres del BD usando el
    catálogo canónico de pedido_processor (fingerprints únicos por hospital).

    Maneja mojibake (�) reemplazando por '.' en regex implícita: comparamos
    contra el catálogo por substring después de quitar no-alfanuméricos.
    """
    canon_relacion = _hospital_canonico(nombre_relacion) or _hospital_canonico(
        re.sub(r"[^a-zA-Z ]", "a", nombre_relacion)  # mojibake → 'a' (no rompe)
    )
    if not canon_relacion:
        return None
    for h_bd in nombres_bd:
        canon_bd = _hospital_canonico(h_bd) or _hospital_canonico(
            re.sub(r"[^a-zA-Z ]", "a", h_bd)
        )
        if canon_bd == canon_relacion:
            return h_bd
    return None


def reconstruir_estado(excel_bd: Path, fecha_iso: str, fecha_legible: str,
                       relacion_xlsx: Path | None = None,
                       sobrescribir: bool = False,
                       excluir_de_cambio_lote: list[str] | None = None) -> Path:
    """Reconstruye y guarda el JSON de estado de un día desde el Excel BD original.

    No regenera PDFs ni sube nada a Drive. Solo escribe el snapshot JSON.

    Args:
        excel_bd: Excel del cliente con hoja 'BD'.
        fecha_iso: 'YYYY-MM-DD'.
        fecha_legible: '27 de abril'.
        relacion_xlsx: opcional, para asignar folios ya emitidos por hospital.
        sobrescribir: si False (default), aborta si ya existe el JSON.
        excluir_de_cambio_lote: substrings (lowercase) de productos que NO deben
            tratarse como cambio Lote 1→5 en este día. Útil cuando reconstruyes
            un día anterior a la incorporación de ciertos productos al CAMBIO_KW.
    """
    excel_bd = Path(excel_bd)
    out = _state_file(fecha_iso)
    if out.exists() and not sobrescribir:
        raise FileExistsError(
            f"Ya existe estado para {fecha_iso} en {out}. "
            f"Pasa sobrescribir=True para reemplazar."
        )

    # ─── Leer y filtrar BD (misma lógica que procesar_pedido) ────────────────
    df_raw = pd.read_excel(excel_bd, sheet_name="BD", header=0)
    if df_raw.shape[1] < 6:
        raise ValueError(f"Hoja BD con menos de 6 columnas: {df_raw.shape[1]}")
    if df_raw.shape[1] > 6:
        df_raw = df_raw.iloc[:, :6]
    df_raw.columns = ["UNIDAD", "LOTE", "CBA", "ALIMENTO", "PRESENTACION", "CANTIDAD"]
    df_raw = df_raw.dropna(subset=["UNIDAD", "ALIMENTO"])
    df_raw["CANTIDAD"] = pd.to_numeric(df_raw["CANTIDAD"], errors="coerce").fillna(0)
    df_raw["UNIDAD"] = df_raw["UNIDAD"].astype(str).str.strip()
    df_raw["LOTE"] = df_raw["LOTE"].astype(str).str.strip()
    df_raw["ALIMENTO"] = df_raw["ALIMENTO"].astype(str).str.strip()

    # Excluir hospitales no atendidos
    df_incluido = df_raw[~df_raw["UNIDAD"].apply(_is_excluido)].copy()

    # Lote 5 (FyV) — quitar mal clasificados (salchicha, etc.)
    df_l5 = df_incluido[df_incluido["LOTE"].apply(_es_lote_5)].copy()
    df_l5 = df_l5[~df_l5["ALIMENTO"].apply(_is_ignorar)]

    # Cambio Lote 1 → 5 (productos FyV mal etiquetados como abarrote)
    df_lote1 = df_incluido[df_incluido["LOTE"].apply(_es_lote_1)].copy()
    df_cambio = df_lote1[df_lote1["ALIMENTO"].apply(_is_cambio)].copy()
    if excluir_de_cambio_lote:
        excluir_lower = [s.lower() for s in excluir_de_cambio_lote]
        df_cambio = df_cambio[
            ~df_cambio["ALIMENTO"].str.lower().apply(
                lambda a: any(s in a for s in excluir_lower)
            )
        ]

    df_fyv = pd.concat([df_l5, df_cambio], ignore_index=True)
    df_fyv = df_fyv[df_fyv["CANTIDAD"].notna() & (df_fyv["CANTIDAD"] > 0)]

    if df_fyv.empty:
        raise ValueError("No quedaron productos FyV después de filtrar.")

    # ─── Folios desde la relación (opcional) ─────────────────────────────────
    folios: dict[str, str] = {}
    if relacion_xlsx:
        folios_relacion = extraer_folios_de_relacion(Path(relacion_xlsx))
        nombres_bd = sorted(df_fyv["UNIDAD"].unique())
        for nombre_relacion, folio in folios_relacion.items():
            canon = _resolver_hospital_canonico(nombre_relacion, nombres_bd)
            if canon:
                folios[canon] = folio
            else:
                log.warning(f"No pude matchear hospital de relación: {nombre_relacion}")

    # ─── Guardar estado ──────────────────────────────────────────────────────
    if out.exists() and sobrescribir:
        out.unlink()
    path = guardar_estado(fecha_iso, fecha_legible, df_fyv, folios=folios)
    log_event("storage",
              f"♻️ Estado del {fecha_iso} reconstruido desde BD original",
              {"hospitales": df_fyv["UNIDAD"].nunique(),
               "productos_lineas": len(df_fyv),
               "folios_asignados": len(folios),
               "fuente": str(excel_bd)})
    return path


def _cli():
    parser = argparse.ArgumentParser(description="Reconstruye el JSON de estado de un día desde el BD original.")
    parser.add_argument("excel_bd", help="Path al Excel BD del cliente.")
    parser.add_argument("fecha_iso", help="Fecha ISO, ej. 2026-04-27.")
    parser.add_argument("fecha_legible", help="Fecha legible, ej. '27 de abril'.")
    parser.add_argument("--relacion", help="Path al xlsx Relación Documentos para folios.", default=None)
    parser.add_argument("--sobrescribir", action="store_true", help="Sobrescribe si ya existe.")
    parser.add_argument("--excluir-cambio", nargs="*", default=None,
                        help="Substrings (lowercase) de productos a NO tratar como cambio Lote 1→5.")
    args = parser.parse_args()

    path = reconstruir_estado(
        Path(args.excel_bd), args.fecha_iso, args.fecha_legible,
        relacion_xlsx=Path(args.relacion) if args.relacion else None,
        sobrescribir=args.sobrescribir,
        excluir_de_cambio_lote=args.excluir_cambio,
    )
    print(f"OK: estado guardado en {path}")


if __name__ == "__main__":
    _cli()
