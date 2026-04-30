"""Correcciones de nombres de productos al renderizar documentos.

Aplicada al momento de generar PDFs/Excels que se entregan al surtidor o
al cliente. NO modifica el BD ni los datos persistidos — el Excel original
del cliente queda tal cual.

Para agregar una nueva corrección, sumar una entrada a `_CORRECCIONES`
(clave y valor en minúsculas). El match es case-insensitive y preserva
el casing del texto original.
"""
import json
import logging
import re
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_CORRECCIONES = {
    "atufo": "ataulfo",
}


def corregir_nombre(alimento) -> str:
    """Aplica correcciones de nombres preservando el casing original.

    Ejemplos:
        'MANGO ATUFO'  -> 'MANGO ATAULFO'
        'Mango Atufo'  -> 'Mango Ataulfo'
        'mango atufo'  -> 'mango ataulfo'
    """
    if alimento is None:
        return alimento
    s = str(alimento)
    for incorrecto, correcto in _CORRECCIONES.items():
        pattern = re.compile(re.escape(incorrecto), re.IGNORECASE)

        def _repl(m, _correcto=correcto):
            orig = m.group(0)
            if orig.isupper():
                return _correcto.upper()
            if orig[:1].isupper():
                return _correcto.capitalize()
            return _correcto

        s = pattern.sub(_repl, s)
    return s


def formatear_presentacion(presentacion) -> str:
    """Title-case para la columna Unidad de las notas de remisión.

    Ejemplos:
        'KILO'         -> 'Kilo'
        'MEDIA PIEZA'  -> 'Media Pieza'
        'kg'           -> 'Kg'
    """
    if presentacion is None or presentacion == "":
        return ""
    return str(presentacion).title()


# Override de presentación por nombre de producto. Si el alimento contiene el
# substring (lowercase) en la clave, usa el valor como presentación canónica
# en vez de la que venga del BD del cliente. Útil cuando EHMO manda
# presentaciones inconsistentes (ej. "EMMPAQUE DE 454 g" → estandarizar a "PZ").
#
# Hay dos fuentes:
#  1. _PRESENTACIONES_OVERRIDE (este archivo) — defaults hardcoded
#  2. storage/keywords.json → "presentaciones_override" — extensible sin tocar código
# Las entradas del JSON OVERRIDEAN las hardcoded si tienen la misma clave.
_PRESENTACIONES_OVERRIDE = {
    "mermelada": "CAJA",
    "polvo para hornear": "PZ",
    "palanqueta": "PAQUETE",
    "oregano": "PAQUETE",
    "orégano": "PAQUETE",
}


def _keywords_json_path() -> Path:
    """Path a storage/keywords.json. Resuelve relativo al BASE_DIR del proyecto."""
    # Import local para evitar circular import (config → otros)
    from . import config
    return config.BASE_DIR / "storage" / "keywords.json"


@lru_cache(maxsize=1)
def _extra_presentaciones() -> dict:
    """Lee presentaciones_override desde storage/keywords.json (si existe)."""
    p = _keywords_json_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        extra = data.get("presentaciones_override") or {}
        if not isinstance(extra, dict):
            return {}
        return {str(k).lower(): str(v) for k, v in extra.items()}
    except Exception as e:
        log.warning(f"No pude leer presentaciones_override de {p.name}: {e}")
        return {}


def _all_presentaciones_override() -> dict:
    """Merge hardcoded + extras del JSON. Las del JSON ganan si hay colisión."""
    merged = dict(_PRESENTACIONES_OVERRIDE)
    merged.update(_extra_presentaciones())
    return merged


def recargar_presentaciones() -> dict:
    """Limpia el cache para que se relean las presentaciones del JSON."""
    _extra_presentaciones.cache_clear()
    extras = _extra_presentaciones()
    return {"hardcoded": len(_PRESENTACIONES_OVERRIDE), "extras_json": len(extras)}


def presentacion_canonica(alimento, fallback="") -> str:
    """Devuelve la presentación canónica para un alimento.

    Si el alimento coincide con una entrada del override (substring
    case-insensitive), usa esa unidad. Sino, retorna fallback.
    """
    if alimento is None:
        return fallback or ""
    a = str(alimento).lower()
    for kw, pres in _all_presentaciones_override().items():
        if kw in a:
            return pres
    return fallback or ""
