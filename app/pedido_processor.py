"""Procesador de pedidos Excel.

Toma un Excel del cliente EHMO con hoja "BD" y genera el archivo de salida
con todas las hojas para Frutas y Verduras (Lote 5):
  1. BD                          - copia original sin modificar
  2. cambio lote 1 a lote 5      - lista de productos movidos
  3. BD cambio lote 1 a lote 5   - BD con lotes corregidos
  4. relacion de unidades a surtir - hospitales con si/no
  5. lista de compras            - consolidado total
  6. pedidos para surtir         - agrupado por hospital
  7+. una hoja por hospital

Basado en el script de CONTEXTO_SISTEMA_PEDIDOS.md.
"""
import logging
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment

log = logging.getLogger(__name__)

# ─── Reglas de negocio ────────────────────────────────────────────────────────
EXCLUIDOS_KW = [
    "pichucalco", "palenque", "tila", "reforma",
    "yajalón", "yajalon", "amatán", "amatan",
]

CAMBIO_KW = [
    "ajo en bulbo", "ajonjolí", "ajonjoli",
    "cacahuate tostado sin sal",
    "canela en raja",
    "chile seco ancho", "chile seco guajillo", "chile seco pasilla",
    "epazote", "flor de jamaica",
    "orégano en hoja", "oregano en hoja",
    "perejil",
    "te de limón zacate", "te de limon zacate", "té de limón zacate",
    "te de manzanilla", "té de manzanilla",
    "te de yerbabuena", "té de yerbabuena",
]

IGNORAR_KW = ["almendra tostada", "palanqueta de cacahuate"]

NOMBRES_CORTOS = {
    "Hospital Básico Comunitario Ángel Albino Corzo": "Angel Albino Corzo",
    "Hospital Básico Comunitario Chiapa de Corzo": "Chiapa de Corzo",
    "Hospital Básico Comunitario Las Margaritas": "Las Margaritas",
    "Hospital Básico Comunitario Manuel Velasco Suarez Acala": "Manuel Velasco Suarez Acala",
    "Hospital Chiapas nos une Dr. Jesús Gilberto Gomez Maza": "Jesús Gilberto Gomez Maza",
    "Hospital de la Mujer Comitán": "Mujer Comitán",
    "Hospital de la Mujer San Cristóbal de las Casas": "Mujer San Cristóbal de las Casa",
    "Hospital de las Culturas San Cristóbal de las Casas": "Culturas San Cristóbal de las C",
    "Hospital General María Ignacia Gandulfo Comitán": "María Ignacia Gandulfo ",
    "Hospital General Tapachula": " General Tapachula",
    "Hospital Regional Dr. Rafael Pascasio Gamboa Tuxtla": "Rafael Pascasio Gamboa Tuxtla",
    "Unidad de Atención a la Salud Mental San Agustín": "Salud Mental San Agustín",
}

MESES = {
    "enero": "ene", "febrero": "feb", "marzo": "mar", "abril": "abr",
    "mayo": "may", "junio": "jun", "julio": "jul", "agosto": "ago",
    "septiembre": "sep", "octubre": "oct", "noviembre": "nov", "diciembre": "dic",
}

# ─── Estilos ──────────────────────────────────────────────────────────────────
AZUL_OSC = "1F4E79"
AZUL_CLAR = "D6E4F0"
GRIS = "BBBBBB"


def _is_excluido(nombre):
    n = str(nombre).lower()
    return any(kw in n for kw in EXCLUIDOS_KW)


def _is_cambio(alimento):
    a = str(alimento).lower()
    if any(kw in a for kw in IGNORAR_KW):
        return False
    return any(kw in a for kw in CAMBIO_KW)


def _get_nombre_corto(nombre):
    if nombre in NOMBRES_CORTOS:
        return NOMBRES_CORTOS[nombre]
    words = nombre.split()
    short = ""
    for w in reversed(words):
        candidate = (w + " " + short).strip() if short else w
        if len(candidate) <= 31:
            short = candidate
        else:
            break
    return short[:31]


def _fill(hex_color):
    return PatternFill("solid", fgColor=hex_color)


def _font(bold=False, color="000000", size=10, name="Arial"):
    return Font(name=name, bold=bold, color=color, size=size)


def _border():
    s = Side(style="thin", color=GRIS)
    return Border(left=s, right=s, top=s, bottom=s)


def _set_row(ws, row_idx, values, bold=False, bg=None, font_color="000000"):
    for col_idx, val in enumerate(values, 1):
        c = ws.cell(row=row_idx, column=col_idx, value=val)
        c.font = Font(name="Arial", bold=bold, color=font_color, size=10)
        c.border = _border()
        c.alignment = Alignment(wrap_text=False)
        if bg:
            c.fill = _fill(bg)


def _extraer_fecha(filename: str) -> str:
    """Extrae la fecha del nombre del archivo. Ej: 'Pedido 27 de abril original.xlsx' -> '27 de abril'."""
    m = re.search(r"Pedido (.+?)(?:\s+original)?\s*\.xlsx", filename, re.I)
    if m:
        return m.group(1).strip()
    return datetime.now().strftime("%d de %B")


def procesar_pedido(input_excel: Path, output_dir: Path,
                    original_filename: str | None = None) -> Path | None:
    """Procesa un Excel EHMO con hoja BD. Devuelve el path del archivo generado.

    Si el archivo no tiene hoja BD o el formato no coincide, devuelve None.
    `original_filename` se usa para extraer la fecha del nombre real del archivo
    (no del nombre local que puede tener prefijos de timestamp/phone).
    """
    input_excel = Path(input_excel)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ─── Leer y validar BD ───────────────────────────────────────────────────
    try:
        df_raw = pd.read_excel(input_excel, sheet_name="BD", header=0)
    except ValueError as e:
        log.warning(f"Excel no tiene hoja 'BD': {input_excel.name} ({e})")
        return None
    except Exception as e:
        log.exception(f"Error leyendo Excel {input_excel.name}: {e}")
        return None

    if df_raw.shape[1] < 6:
        log.warning(f"Hoja BD con menos de 6 columnas: {df_raw.shape[1]}")
        return None

    df_raw.columns = ["UNIDAD", "LOTE", "CBA", "ALIMENTO", "PRESENTACION", "CANTIDAD"]
    df_raw = df_raw.dropna(subset=["UNIDAD", "ALIMENTO"])
    df_raw["CANTIDAD"] = pd.to_numeric(df_raw["CANTIDAD"], errors="coerce").fillna(0)
    df_raw["UNIDAD"] = df_raw["UNIDAD"].astype(str).str.strip()
    df_raw["LOTE"] = df_raw["LOTE"].astype(str).str.strip()
    df_raw["ALIMENTO"] = df_raw["ALIMENTO"].astype(str).str.strip()

    # ─── Fecha ───────────────────────────────────────────────────────────────
    fecha_str = _extraer_fecha(original_filename or input_excel.name)
    parts = fecha_str.lower().split()
    dia = parts[0] if parts else "fecha"
    mes_corto = MESES.get(parts[-1], parts[-1][:3]) if parts else "fec"
    fecha_corta = f"{dia}-{mes_corto}"

    # ─── Filtros ─────────────────────────────────────────────────────────────
    todos_hospitales = sorted(df_raw["UNIDAD"].unique())
    mask_excluido = df_raw["UNIDAD"].apply(_is_excluido)
    df_incluido = df_raw[~mask_excluido].copy()
    hospitales_si = sorted(df_incluido["UNIDAD"].unique())

    df_l5 = df_incluido[df_incluido["LOTE"].str.upper().str.contains("5 FRUTAS", na=False)].copy()
    df_lote1 = df_incluido[df_incluido["LOTE"].str.strip().str.upper().str.startswith("1 ABARROTES")].copy()
    df_cambio = df_lote1[df_lote1["ALIMENTO"].apply(_is_cambio)].copy()
    productos_cambio_nombres = sorted(df_cambio["ALIMENTO"].unique())

    df_bd_corr = df_raw.copy()

    def corregir_lote(row):
        if (_is_cambio(row["ALIMENTO"])
                and row["LOTE"].strip().upper().startswith("1 ABARROTES")
                and not _is_excluido(row["UNIDAD"])):
            return "5 FRUTAS Y VERDURAS "
        return row["LOTE"]

    df_bd_corr["LOTE"] = df_bd_corr.apply(corregir_lote, axis=1)

    df_fyv = pd.concat([df_l5, df_cambio], ignore_index=True)
    df_fyv = df_fyv[df_fyv["CANTIDAD"] > 0]

    # ─── Crear Excel de salida ───────────────────────────────────────────────
    output_path = output_dir / f"Pedido {fecha_str} Frutas y verduras.xlsx"
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # Hoja 1: BD original
    ws_bd = wb.create_sheet("BD")
    headers_bd = ["UNIDAD", "LOTE", "C.B.A", "ALIMENTO", "PRESENTACIÓN", fecha_str.upper()]
    _set_row(ws_bd, 1, headers_bd, bold=True, bg=AZUL_OSC, font_color="FFFFFF")
    for i, row in enumerate(df_raw.itertuples(index=False), 2):
        _set_row(ws_bd, i, [row.UNIDAD, row.LOTE, row.CBA, row.ALIMENTO, row.PRESENTACION, row.CANTIDAD])
    ws_bd.column_dimensions["A"].width = 60
    ws_bd.column_dimensions["D"].width = 60
    ws_bd.column_dimensions["F"].width = 18

    # Hoja 2: cambio lote
    ws_cambio = wb.create_sheet("cambio lote 1 a lote 5 ")
    _set_row(ws_cambio, 1, ["Productos cambio de Lote 1 a Lote 5"], bold=True, bg=AZUL_OSC, font_color="FFFFFF")
    for i, p in enumerate(productos_cambio_nombres, 2):
        c = ws_cambio.cell(row=i, column=1, value=p)
        c.font = _font()
        c.border = _border()
    ws_cambio.column_dimensions["A"].width = 60

    # Hoja 3: BD corregida
    ws_bdcorr = wb.create_sheet("BD cambio lote 1 a lote 5")
    _set_row(ws_bdcorr, 1, headers_bd, bold=True, bg=AZUL_OSC, font_color="FFFFFF")
    for i, row in enumerate(df_bd_corr.itertuples(index=False), 2):
        _set_row(ws_bdcorr, i, [row.UNIDAD, row.LOTE, row.CBA, row.ALIMENTO, row.PRESENTACION, row.CANTIDAD])
    ws_bdcorr.column_dimensions["A"].width = 60
    ws_bdcorr.column_dimensions["D"].width = 60
    ws_bdcorr.column_dimensions["F"].width = 18

    # Hoja 4: relación unidades
    ws_rel = wb.create_sheet("relacion de unidades a surtir")
    _set_row(ws_rel, 3, ["Etiquetas de fila", "unidades a surtir"], bold=True, bg=AZUL_CLAR)
    for i, h in enumerate(sorted(todos_hospitales), 4):
        val = "si" if h in hospitales_si else "no"
        _set_row(ws_rel, i, [h, val])
    _set_row(ws_rel, len(todos_hospitales) + 4, ["Total general", len(todos_hospitales)], bold=True)
    ws_rel.column_dimensions["A"].width = 60
    ws_rel.column_dimensions["B"].width = 20

    # Hoja 5: lista de compras
    ws_lista = wb.create_sheet("lista de compras")
    ws_lista.cell(row=1, column=1, value="LOTE").font = _font(bold=True)
    ws_lista.cell(row=1, column=2, value="5 FRUTAS Y VERDURAS ").font = _font()
    ws_lista.cell(row=2, column=1, value="UNIDAD").font = _font(bold=True)
    ws_lista.cell(row=2, column=2, value="(Varios elementos)").font = _font()
    _set_row(ws_lista, 4, ["Etiquetas de fila", f"Suma de {fecha_corta}"], bold=True, bg=AZUL_CLAR)
    compras = df_fyv.groupby("ALIMENTO")["CANTIDAD"].sum().reset_index()
    compras = compras[compras["CANTIDAD"] > 0].sort_values("ALIMENTO")
    for i, row in enumerate(compras.itertuples(index=False), 5):
        _set_row(ws_lista, i, [row.ALIMENTO, row.CANTIDAD])
    _set_row(ws_lista, len(compras) + 5, ["Total general", float(compras["CANTIDAD"].sum())], bold=True)
    ws_lista.column_dimensions["A"].width = 55
    ws_lista.column_dimensions["B"].width = 20

    # Hoja 6: pedidos para surtir
    ws_ped = wb.create_sheet("pedidos para surtir")
    ws_ped.cell(row=2, column=1, value="LOTE").font = _font(bold=True)
    ws_ped.cell(row=2, column=2, value="5 FRUTAS Y VERDURAS ").font = _font()
    _set_row(ws_ped, 4, ["Etiquetas de fila", f"Suma de {fecha_corta}"], bold=True, bg=AZUL_CLAR)
    hospitales_fyv = sorted(df_fyv["UNIDAD"].unique())
    row_idx = 5
    for hospital in hospitales_fyv:
        df_h = df_fyv[df_fyv["UNIDAD"] == hospital].groupby("ALIMENTO")["CANTIDAD"].sum().reset_index()
        df_h = df_h[df_h["CANTIDAD"] > 0].sort_values("ALIMENTO")
        _set_row(ws_ped, row_idx, [hospital, float(df_h["CANTIDAD"].sum())], bold=True, bg=AZUL_CLAR)
        row_idx += 1
        for _, prow in df_h.iterrows():
            _set_row(ws_ped, row_idx, [f"  {prow['ALIMENTO']}", prow["CANTIDAD"]])
            row_idx += 1
    _set_row(ws_ped, row_idx, ["Total general", float(df_fyv["CANTIDAD"].sum())], bold=True)
    ws_ped.column_dimensions["A"].width = 55
    ws_ped.column_dimensions["B"].width = 20

    # Hojas por hospital
    for hospital in hospitales_fyv:
        sname = _get_nombre_corto(hospital)
        ws_h = wb.create_sheet(sname)
        ws_h.cell(row=1, column=1, value="LOTE").font = _font(bold=True)
        ws_h.cell(row=1, column=2, value="5 FRUTAS Y VERDURAS ").font = _font()
        _set_row(ws_h, 3, ["Etiquetas de fila", f"Suma de {fecha_corta}"], bold=True, bg=AZUL_CLAR)
        df_h = df_fyv[df_fyv["UNIDAD"] == hospital].groupby("ALIMENTO")["CANTIDAD"].sum().reset_index()
        df_h = df_h[df_h["CANTIDAD"] > 0].sort_values("ALIMENTO")
        _set_row(ws_h, 4, [hospital, float(df_h["CANTIDAD"].sum())], bold=True, bg=AZUL_CLAR)
        for i, prow in enumerate(df_h.itertuples(index=False), 5):
            _set_row(ws_h, i, [prow.ALIMENTO, prow.CANTIDAD])
        ws_h.column_dimensions["A"].width = 55
        ws_h.column_dimensions["B"].width = 20

    wb.save(output_path)
    log.info(f"Excel procesado guardado: {output_path}")

    return output_path


def extraer_fecha(filename: str) -> str:
    """Alias público para compatibilidad."""
    return _extraer_fecha(filename)
