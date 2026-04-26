"""Procesador de pedidos Excel — basado en el script existente del proyecto.

Toma un Excel del cliente EHMO con hoja "BD" y genera el archivo de salida
con todas las hojas para Frutas y Verduras (Lote 5).

Esta es una versión simplificada del script en CONTEXTO_SISTEMA_PEDIDOS.md.
"""
import logging
import re
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)


# Hospitales que NO se surten desde el centro de distribución
HOSPITALES_EXCLUIDOS = [
    "pichucalco", "palenque", "tila", "reforma",
    "yajalón", "yajalon", "amatán", "amatan",
]

# Productos que están en Lote 1 pero pertenecen a Lote 5
PRODUCTOS_CAMBIO_LOTE = [
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

# Productos que NO se incluyen aunque estén en la lista de cambio
PRODUCTOS_IGNORAR = ["almendra tostada", "palanqueta de cacahuate"]


def procesar_pedido(input_excel: Path, output_dir: Path) -> Path:
    """Procesa un Excel de pedido del cliente EHMO.

    TODO: integrar el script completo del CONTEXTO_SISTEMA_PEDIDOS.md aquí.
    Por ahora solo registra que recibió el archivo.
    """
    log.info(f"📊 Procesando pedido: {input_excel.name}")

    # TODO: importar pandas y openpyxl, y hacer el procesamiento completo
    # Por ahora devolvemos un placeholder

    output_name = f"Pedido procesado {datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    output_path = output_dir / output_name

    log.info(f"⏳ Procesamiento completo pendiente de implementación")
    return output_path


def extraer_fecha(filename: str) -> str:
    """Extrae la fecha del nombre del archivo."""
    m = re.search(r"Pedido (.+?)(?:\s+original)?\s*\.xlsx", filename, re.I)
    return m.group(1).strip() if m else datetime.now().strftime("%d-%m-%Y")
