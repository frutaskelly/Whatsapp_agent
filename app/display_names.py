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
