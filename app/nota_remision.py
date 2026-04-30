"""Generador de Notas de Remisión con precios — formato oficial.

Replica el formato exacto del PDF de referencia "REMISIONES PEDIDOS SEM 17":
  - Header con lugar de expedición + domicilio fiscal del proveedor
  - Datos del cliente (razón social + RFC + CP)
  - Línea descriptiva SEMANA / DIF ENTREGA
  - Tabla de 6 columnas: Cantidad | Unidad | Descripción | %Desc | P/U | Importe
  - Cantidades con 4 decimales
  - Footer fiscal: Subtotal / Descuento / Desc. Fin. / I.E.P.S. / I.V.A. / Total
  - Total en letras al final
  - Folio secuencial persistente
"""
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
import pandas as pd
from num2words import num2words

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, PageBreak, Spacer,
)
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT

from . import config
from .display_names import corregir_nombre, formatear_presentacion
from .event_log import log_event
from .pricing import buscar_precio

log = logging.getLogger(__name__)

# ─── Estilos ──────────────────────────────────────────────────────────────────
NEGRO = colors.HexColor("#000000")
GRIS = colors.HexColor("#888888")
GRIS_CLARO = colors.HexColor("#F4F4F4")

# ─── Datos fiscales ───────────────────────────────────────────────────────────
# Proveedor (mismo para ambos clientes)
PROVEEDOR_NOMBRE = "CRISTIAN GERARDO ZARATE OROZCO"
PROVEEDOR_RFC = "ZAOC830517RF9"

# Domicilio fiscal del proveedor (mismo)
DOMICILIO_FISCAL = (
    "Calle: SIMBOLOS PATRIOS No. 107, "
    "Col. Paraje el Cerritos, CP: 71260, "
    "San Agustín de las Juntas, Oaxaca"
)

# Lugar de expedición (mismo)
LUGAR_EXPEDICION = (
    "Calle: LEGUMBRES 302 No. A, "
    "Col. ABASTOS, CP: 78390, "
    "SAN LUIS POTOSI, SAN LUIS POTOSI, MEXICO"
)

# ─── Clientes (multi-cliente) ────────────────────────────────────────────────
# Cada cliente tiene su propio:
#   - nombre razón social, RFC, CP
#   - cliente_id (para el campo "(N) NOMBRE")
#   - tipo de línea descriptiva (DIF ENTREGA / COMEDORES HUMANITARIOS)
#   - archivo de folio counter
_SUREÑA_DATA = {
    "nombre": "GRUPO SUREÑA",
    "rfc": "GSU110118GL0",
    "cp": "78390",
    "cliente_id": 2,
    "linea_tipo": "comedores",  # → "SEMANA N: COMEDORES HUMANITARIOS ENTREGA <DIA> ... EN <comedor>."
    "folio_file": "folio_counter_comedores.json",
}
CLIENTES = {
    "EHMO": {
        "nombre": "GRUPO OPERADOR DE ALIMENTOS EHMO",
        "rfc": "GOA180712SF5",
        "cp": "78390",
        "cliente_id": 1,
        "linea_tipo": "ehmo",  # → "SEMANA N: DIF ENTREGA <DIA> ... EN: <hospital>"
        "folio_file": "folio_counter.json",
    },
    # Soportar ambas variantes con/sin eñe (clientes.json y agentes.json
    # usan SURENA sin eñe; código histórico usaba SUREÑA con eñe).
    "SUREÑA": _SUREÑA_DATA,
    "SURENA": _SUREÑA_DATA,
}

# Constantes legacy (mantienen compatibilidad con código viejo). Apuntan al EHMO
# por default. Para SUREÑA usar CLIENTES["SUREÑA"][...].
CLIENTE_NOMBRE = CLIENTES["EHMO"]["nombre"]
CLIENTE_RFC = CLIENTES["EHMO"]["rfc"]
CLIENTE_CP = CLIENTES["EHMO"]["cp"]

# Días de la semana en español
DIAS_SEMANA = {
    0: "LUNES", 1: "MARTES", 2: "MIERCOLES", 3: "JUEVES",
    4: "VIERNES", 5: "SABADO", 6: "DOMINGO",
}
MESES_NOMBRE = {
    1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL",
    5: "MAYO", 6: "JUNIO", 7: "JULIO", 8: "AGOSTO",
    9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE",
}

# ─── Folio secuencial persistente (por cliente) ──────────────────────────────
_folio_lock = threading.Lock()
FOLIO_INICIAL = 1  # arranca en 1; el operador puede editar el archivo si quiere otro inicio


def _folio_file(cliente: str = "EHMO") -> Path:
    """Path al archivo de folio counter del cliente especificado."""
    fname = CLIENTES.get(cliente, CLIENTES["EHMO"])["folio_file"]
    return config.BASE_DIR / "storage" / fname


def _next_folio(cliente: str = "EHMO") -> str:
    """Devuelve el siguiente folio secuencial del cliente, padded a 10 dígitos.

    Cada cliente tiene su propia secuencia (EHMO y SUREÑA son independientes).
    """
    with _folio_lock:
        f = _folio_file(cliente)
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.exists():
            try:
                state = json.loads(f.read_text())
                current = int(state.get("next", FOLIO_INICIAL))
            except Exception:
                current = FOLIO_INICIAL
        else:
            current = FOLIO_INICIAL
        next_val = current + 1
        f.write_text(json.dumps({"next": next_val}, indent=2))
        return f"{current:010d}"


# Alias por compatibilidad con código viejo que llama _FOLIO_FILE directamente
_FOLIO_FILE = _folio_file("EHMO")


def _fecha_hoy_dmY() -> str:
    """Fecha actual formato DD/MM/YYYY (fecha de elaboración del documento)."""
    return datetime.now().strftime("%d/%m/%Y")


# Offset entre numeración ISO y numeración interna del cliente EHMO.
# Cristian confirmó (2026-04-26) que el cliente numera con un offset de -1
# respecto al estándar ISO (lunes 27/abr/2026 = ISO 18 = EHMO 17).
SEMANA_OFFSET_EHMO = -1


def _semana_iso(fecha_entrega: datetime) -> int:
    """Número de semana en el calendario del cliente EHMO (ISO + offset)."""
    return max(1, fecha_entrega.isocalendar()[1] + SEMANA_OFFSET_EHMO)


def _linea_descriptiva(fecha_entrega: datetime, lugar: str,
                        cliente: str = "EHMO") -> str:
    """Genera la línea descriptiva según el tipo de cliente.

    EHMO:      'SEMANA 17: DIF ENTREGA LUNES 27 DE ABRIL DE 2026 EN: <hospital>'
    Comedores: 'SEMANA 17: COMEDORES HUMANITARIOS ENTREGA LUNES 27 DE ABRIL 2026 EN <comedor>.'
    """
    semana = _semana_iso(fecha_entrega)
    dia_sem = DIAS_SEMANA[fecha_entrega.weekday()]
    mes = MESES_NOMBRE[fecha_entrega.month]
    linea_tipo = CLIENTES.get(cliente, CLIENTES["EHMO"])["linea_tipo"]
    # Nombre corto del lugar (sin prefijos "Comedor" / "Hospital ...") para el header
    if linea_tipo == "comedores":
        # Quitar prefijo "Comedor " si existe
        nombre_corto = lugar.upper()
        for prefix in ("COMEDOR ", "COMEDOR_"):
            if nombre_corto.startswith(prefix):
                nombre_corto = nombre_corto[len(prefix):]
                break
        return (f"SEMANA {semana}: COMEDORES HUMANITARIOS ENTREGA "
                f"{dia_sem} {fecha_entrega.day} DE {mes} {fecha_entrega.year} "
                f"EN {nombre_corto}.")
    # default: EHMO
    return (f"SEMANA {semana}: DIF ENTREGA {dia_sem} {fecha_entrega.day} DE "
            f"{mes} DE {fecha_entrega.year} EN: {lugar.upper()}")


def _total_en_letras(monto: float) -> str:
    """Convierte 8423.50 → 'OCHO MIL CUATROCIENTOS VEINTITRES PESOS 50/100 M.N.'."""
    entero = int(monto)
    centavos = round((monto - entero) * 100)
    try:
        palabras = num2words(entero, lang="es").upper()
    except Exception:
        palabras = str(entero)
    # num2words devuelve "uno", el formato fiscal mexicano usa "UN" en contextos como "UN PESO"
    if palabras == "UNO":
        palabras = "UN"
    return f"{palabras} PESOS {centavos:02d}/100 M.N."


def _format_cantidad(value) -> str:
    """Formato '33.0000' (4 decimales fijos)."""
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)


def _format_precio(value) -> str:
    """Formato '16.5000' (4 decimales fijos, sin signo $)."""
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)


def _format_importe(value) -> str:
    """Formato '1,544.50' (2 decimales con separador miles, sin signo $)."""
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


# ─── Generador principal ──────────────────────────────────────────────────────
def generar_notas_remision(df_fyv: pd.DataFrame, fecha_str: str, output_path: Path,
                           fecha_entrega: datetime | None = None,
                           tipo: str = "regular",
                           folios_existentes: dict[str, str] | None = None,
                           cliente: str = "EHMO") -> dict:
    """Genera el PDF de notas con una nota por destino en formato oficial.

    `fecha_entrega` puede pasarse explícita; si no, se infiere de fecha_str.
    `tipo` puede ser "regular" o "extras" (cambia la línea descriptiva y el banner).
    `folios_existentes`: dict {destino: folio_str} para reusar folios ya
       asignados (regeneraciones). Si un destino no está, se asigna folio nuevo.
    `cliente`: "EHMO" (default, hospitales DIF) o "SUREÑA" (comedores humanitarios).
        Cambia datos fiscales del cliente, línea descriptiva, y secuencia de folios.
    Devuelve dict con stats incluyendo `folios` = mapping destino→folio usado.
    """
    folios_existentes = folios_existentes or {}
    # Datos fiscales del cliente activo
    cli = CLIENTES.get(cliente, CLIENTES["EHMO"])
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    df_fyv = df_fyv.copy()
    df_fyv["ALIMENTO"] = df_fyv["ALIMENTO"].apply(corregir_nombre)
    df_fyv["PRESENTACION"] = df_fyv["PRESENTACION"].apply(formatear_presentacion)

    # Resolver fecha de entrega
    if fecha_entrega is None:
        fecha_entrega = _inferir_fecha(fecha_str) or datetime.now()

    fecha_doc = _fecha_hoy_dmY()  # fecha de elaboración (hoy)

    doc = SimpleDocTemplate(
        str(output_path), pagesize=letter,
        topMargin=1 * cm, bottomMargin=1 * cm,
        leftMargin=1.2 * cm, rightMargin=1.2 * cm,
        title=f"Notas Remisión {fecha_str}",
    )

    styles = getSampleStyleSheet()
    style_small = ParagraphStyle("sm", parent=styles["Normal"], fontSize=7, leading=8)
    style_small_b = ParagraphStyle("smb", parent=styles["Normal"], fontSize=7, leading=8,
                                    fontName="Helvetica-Bold")
    style_titulo = ParagraphStyle("ti", parent=styles["Normal"], fontSize=10, leading=12,
                                   fontName="Helvetica-Bold", alignment=TA_CENTER)
    style_provnombre = ParagraphStyle("pn", parent=styles["Normal"], fontSize=9, leading=11,
                                       fontName="Helvetica-Bold", alignment=TA_RIGHT)
    style_label_b = ParagraphStyle("lb", parent=styles["Normal"], fontSize=8, leading=10,
                                    fontName="Helvetica-Bold")
    style_normal = ParagraphStyle("n", parent=styles["Normal"], fontSize=8, leading=10)
    style_descrip = ParagraphStyle("desc", parent=styles["Normal"], fontSize=9, leading=11,
                                    fontName="Helvetica-Bold")
    style_total_letras = ParagraphStyle("tl", parent=styles["Normal"], fontSize=8, leading=10,
                                         fontName="Helvetica-Bold")

    elements = []
    hospitales = sorted(df_fyv["UNIDAD"].unique())
    total_general = 0.0
    sin_precio_count = 0
    folios_usados: dict[str, str] = {}

    for idx, hospital in enumerate(hospitales, 1):
        df_h = (df_fyv[df_fyv["UNIDAD"] == hospital]
                .groupby(["ALIMENTO", "PRESENTACION"])["CANTIDAD"]
                .sum().reset_index().sort_values("ALIMENTO"))
        # Filtro defensivo: nunca cobrar productos con cantidad 0 o negativa
        df_h = df_h[df_h["CANTIDAD"] > 0]
        if df_h.empty:
            log_event("processor",
                      f"⚠️ {hospital}: sin productos con cantidad > 0 (saltado)",
                      level="warn")
            continue

        # Reutilizar folio si ya existe para este destino, sino tomar nuevo
        # del counter del cliente activo
        if hospital in folios_existentes:
            folio = folios_existentes[hospital]
        else:
            folio = _next_folio(cliente)
        folios_usados[hospital] = folio

        # ─── HEADER: lugar exped (izq) | título + folio + fecha (der) ──────
        header_data = [[
            Paragraph(
                f"<b>Lugar de expedición</b><br/>{LUGAR_EXPEDICION}<br/><br/>"
                f"<b>Domicilio fiscal</b><br/>{DOMICILIO_FISCAL}<br/>"
                f"<b>RFC:</b> {PROVEEDOR_RFC}",
                style_small,
            ),
            Paragraph(
                f"<b>NOTA DE REMISIÓN</b><br/><br/>"
                f"<b>{PROVEEDOR_NOMBRE}</b><br/><br/>"
                f"<b>PEDIDO No.:</b> {folio}<br/>"
                f"{fecha_doc}<br/>"
                f"<b>R.F.C.:</b> {PROVEEDOR_RFC}",
                style_provnombre,
            ),
        ]]
        header_tbl = Table(header_data, colWidths=[11 * cm, 7 * cm])
        header_tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOX", (0, 0), (-1, -1), 0.5, NEGRO),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(header_tbl)

        # ─── CLIENTE ─────────────────────────────────────────────────────────
        elements.append(Spacer(1, 0.2 * cm))
        cliente_data = [[
            Paragraph(
                f"<b>Cliente:</b> ( {cli['cliente_id']} ) {cli['nombre']} &nbsp;&nbsp;&nbsp; "
                f"<b>RFC:</b> {cli['rfc']} &nbsp;&nbsp;&nbsp; <b>CP:</b> {cli['cp']}",
                style_label_b,
            ),
        ]]
        cliente_tbl = Table(cliente_data, colWidths=[18 * cm])
        cliente_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), GRIS_CLARO),
            ("BOX", (0, 0), (-1, -1), 0.5, NEGRO),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(cliente_tbl)

        # ─── LÍNEA DESCRIPTIVA SEMANA ────────────────────────────────────────
        elements.append(Spacer(1, 0.2 * cm))
        if tipo == "extras":
            semana = _semana_iso(fecha_entrega)
            dia_sem = DIAS_SEMANA[fecha_entrega.weekday()]
            mes_n = MESES_NOMBRE[fecha_entrega.month]
            linea_desc = (
                f"SEMANA {semana}: EXTRA SOLICITADO PARA CUBRIR NECESIDADES ESPECIALES "
                f"— {dia_sem} {fecha_entrega.day} DE {mes_n} DE {fecha_entrega.year} "
                f"EN: {hospital.upper()}"
            )
        else:
            linea_desc = _linea_descriptiva(fecha_entrega, hospital, cliente=cliente)
        elements.append(Paragraph(linea_desc, style_descrip))
        elements.append(Spacer(1, 0.2 * cm))

        # ─── TABLA DE PRODUCTOS ──────────────────────────────────────────────
        data = [["Cantidad", "Unidad", "Descripción", "% Desc", "P/U", "Importe"]]
        subtotal = 0.0
        sin_precio_h = 0
        for row in df_h.itertuples(index=False):
            cantidad = float(row.CANTIDAD)
            match = buscar_precio(row.ALIMENTO)
            if match:
                precio = match["precio"]
                importe = cantidad * precio
                subtotal += importe
                pu_str = _format_precio(precio)
                imp_str = _format_importe(importe)
            else:
                pu_str = "0.0000"
                imp_str = "0.00"
                sin_precio_h += 1

            data.append([
                _format_cantidad(cantidad),
                str(row.PRESENTACION),
                str(row.ALIMENTO),
                "0.00",
                pu_str,
                imp_str,
            ])

        table = Table(
            data,
            colWidths=[2 * cm, 2 * cm, 8 * cm, 1.5 * cm, 2 * cm, 2.5 * cm],
            repeatRows=1,
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), GRIS_CLARO),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (0, -1), "RIGHT"),
            ("ALIGN", (1, 0), (1, -1), "CENTER"),
            ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOX", (0, 0), (-1, -1), 0.5, NEGRO),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, NEGRO),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        elements.append(table)

        # ─── FOOTER FISCAL: Subtotal/Desc/IEPS/IVA/Total + Total en letras ───
        elements.append(Spacer(1, 0.15 * cm))

        total = subtotal  # Frutas y verduras: tasa 0%, IVA = 0
        totales_data = [
            [Paragraph(_total_en_letras(total), style_total_letras),
             "Subtotal", _format_importe(subtotal)],
            ["", "Descuento", "0.00"],
            ["", "Desc. Fin.", "0.00"],
            ["", "I.E.P.S.", "0.00"],
            ["", "I.V.A.", "0.00"],
            ["", "Total", _format_importe(total)],
        ]
        totales_tbl = Table(totales_data, colWidths=[11.5 * cm, 3 * cm, 3.5 * cm])
        totales_tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTNAME", (1, 0), (-1, -1), "Helvetica-Bold"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("ALIGN", (2, 0), (2, -1), "RIGHT"),
            ("BOX", (1, 0), (-1, -1), 0.5, NEGRO),
            ("LINEABOVE", (1, -1), (-1, -1), 1, NEGRO),
            ("BACKGROUND", (1, -1), (-1, -1), GRIS_CLARO),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            # Total en letras: ocupar el alto completo
            ("SPAN", (0, 0), (0, -1)),
        ]))
        elements.append(totales_tbl)

        # ─── Aviso si hay productos sin precio ───────────────────────────────
        if sin_precio_h:
            elements.append(Spacer(1, 0.2 * cm))
            elements.append(Paragraph(
                f"<font color='#a04040' size='8'>⚠️ {sin_precio_h} producto(s) sin precio en lista — "
                f"P/U e Importe en 0.00. El subtotal NO los incluye.</font>",
                style_normal,
            ))

        total_general += subtotal
        sin_precio_count += sin_precio_h

        if idx < len(hospitales):
            elements.append(PageBreak())

    doc.build(elements)
    log.info(f"Notas de remisión generadas: {output_path} ({len(hospitales)} notas)")
    log_event("processor", f"📑 Notas remisión generadas ({len(hospitales)} notas, total ${total_general:,.2f})",
              {"output": output_path.name, "hospitales": len(hospitales),
               "total_general": total_general, "sin_precio_count": sin_precio_count})

    return {
        "output_path": output_path,
        "total_general": total_general,
        "sin_precio_count": sin_precio_count,
        "hospitales": len(hospitales),
        "folios": folios_usados,
    }


# ─── Inferencia de fecha de entrega ───────────────────────────────────────────
_MES_A_NUM = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def _inferir_fecha(fecha_str: str) -> datetime | None:
    """Convierte '27 de abril' → datetime(2026, 4, 27) usando año actual."""
    if not fecha_str:
        return None
    parts = fecha_str.lower().split()
    dia = mes = None
    año = datetime.now().year
    for p in parts:
        if dia is None:
            try:
                dia = int(p)
                continue
            except ValueError:
                pass
        if p in _MES_A_NUM:
            mes = _MES_A_NUM[p]
            continue
        if p.isdigit() and len(p) == 4:
            año = int(p)
    if dia is None or mes is None:
        return None
    try:
        return datetime(año, mes, dia)
    except ValueError:
        return None
