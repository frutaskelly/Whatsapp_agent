"""Correcciones de nombres de productos al renderizar documentos.

Aplicada al momento de generar PDFs/Excels que se entregan al surtidor o
al cliente. NO modifica el BD ni los datos persistidos — el Excel original
del cliente queda tal cual.

Para agregar una nueva corrección, sumar una entrada a `_CORRECCIONES`
(clave y valor en minúsculas). El match es case-insensitive y preserva
el casing del texto original.
"""
import re

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
_PRESENTACIONES_OVERRIDE = {
    "mermelada": "CAJA",
    "polvo para hornear": "PZ",
    "palanqueta": "PAQUETE",
}


def presentacion_canonica(alimento, fallback="") -> str:
    """Devuelve la presentación canónica para un alimento.

    Si el alimento coincide con una entrada de _PRESENTACIONES_OVERRIDE
    (substring case-insensitive), usa esa unidad. Sino, retorna fallback.
    """
    if alimento is None:
        return fallback or ""
    a = str(alimento).lower()
    for kw, pres in _PRESENTACIONES_OVERRIDE.items():
        if kw in a:
            return pres
    return fallback or ""
