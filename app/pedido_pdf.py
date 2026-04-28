"""Generador de PDF para imprimir pedidos.

Produce un PDF con una página por hospital. Cada página incluye:
  - Header con nombre del hospital + fecha
  - Tabla con #, ALIMENTO, PRESENTACIÓN, CANTIDAD
  - Subtotal de productos al final de la tabla

Diseñado para imprimir todo de una vez y entregar cada hoja por separado
a su hospital correspondiente.
"""
import logging
from pathlib import Path
import pandas as pd

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, PageBreak, Spacer,
)

from .event_log import log_event

log = logging.getLogger(__name__)

AZUL_OSC = colors.HexColor("#1F4E79")
AZUL_CLAR = colors.HexColor("#D6E4F0")
GRIS = colors.HexColor("#BBBBBB")


def generar_pdf_pedido(df_fyv: pd.DataFrame, fecha_str: str, output_path: Path,
                       subtitulo: str = "Lote 5: Frutas y Verduras",
                       titulo_principal: str | None = None) -> Path:
    """Genera un PDF con una página por hospital.

    df_fyv debe tener columnas UNIDAD, ALIMENTO, PRESENTACION, CANTIDAD.
    `subtitulo` se imprime debajo del nombre del hospital (default: "Lote 5...").
    `titulo_principal` opcional: si se da, se imprime arriba como banner
       (ej. "EXTRAS — Cubrir Necesidades Especiales"). Útil para PDFs especiales.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path), pagesize=letter,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        title=f"Pedido {fecha_str} Frutas y Verduras",
    )

    styles = getSampleStyleSheet()
    style_h1 = ParagraphStyle(
        "h1", parent=styles["Heading1"],
        fontSize=14, leading=18, textColor=AZUL_OSC,
        spaceAfter=4,
    )
    style_sub = ParagraphStyle(
        "sub", parent=styles["Normal"],
        fontSize=10, leading=12, textColor=colors.HexColor("#555555"),
        spaceAfter=12,
    )

    elements = []
    hospitales = sorted(df_fyv["UNIDAD"].unique())

    for i, hospital in enumerate(hospitales):
        df_h = (df_fyv[df_fyv["UNIDAD"] == hospital]
                .groupby(["ALIMENTO", "PRESENTACION"])["CANTIDAD"]
                .sum().reset_index().sort_values("ALIMENTO"))
        # Filtro defensivo: no imprimir productos con cantidad 0 o negativa
        df_h = df_h[df_h["CANTIDAD"] > 0]
        if df_h.empty:
            continue

        if titulo_principal:
            style_banner = ParagraphStyle(
                "banner", parent=styles["Heading2"], fontSize=11, leading=13,
                textColor=colors.HexColor("#7C3AED"), spaceAfter=4,
                fontName="Helvetica-Bold",
            )
            elements.append(Paragraph(titulo_principal, style_banner))
        elements.append(Paragraph(hospital, style_h1))
        elements.append(Paragraph(
            f"Pedido del <b>{fecha_str}</b> &nbsp;·&nbsp; {subtitulo}",
            style_sub,
        ))

        data = [["#", "Alimento", "Presentación", "Cantidad"]]
        for idx, row in enumerate(df_h.itertuples(index=False), 1):
            data.append([
                str(idx),
                str(row.ALIMENTO),
                str(row.PRESENTACION),
                _format_qty(row.CANTIDAD),
            ])
        # Total
        total = df_h["CANTIDAD"].sum()
        data.append(["", "TOTAL DE PRODUCTOS", str(len(df_h)), _format_qty(total)])

        table = Table(
            data,
            colWidths=[0.9 * cm, 10.5 * cm, 4 * cm, 2.5 * cm],
            repeatRows=1,
        )
        table.setStyle(TableStyle([
            # Header
            ("BACKGROUND", (0, 0), (-1, 0), AZUL_OSC),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("ALIGN", (3, 0), (3, -1), "RIGHT"),
            # Body
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("FONTNAME", (0, 1), (-1, -2), "Helvetica"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.4, GRIS),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#F8F9FB")]),
            # Total row
            ("BACKGROUND", (0, -1), (-1, -1), AZUL_CLAR),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ]))
        elements.append(table)

        if i < len(hospitales) - 1:
            elements.append(PageBreak())

    doc.build(elements)
    log.info(f"PDF generado: {output_path} ({len(hospitales)} hospitales)")
    log_event("processor", f"📄 PDF generado ({len(hospitales)} páginas)",
              {"output": output_path.name, "hospitales": len(hospitales)})
    return output_path


def _format_qty(value) -> str:
    """Formatea una cantidad: entero si es entero, sino con 2 decimales."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return str(int(f))
    return f"{f:.2f}"
