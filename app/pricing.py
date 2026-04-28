"""Carga la lista de precios y matchea contra los productos del pedido.

Estrategia de match: normaliza ambos lados (lower + sin acentos), busca el
producto de la lista cuyo nombre tenga el match de substring más largo
contra el nombre del alimento del pedido. Si no hay match, devuelve None.
"""
import logging
import unicodedata
from functools import lru_cache
from pathlib import Path
import pandas as pd

from . import config

log = logging.getLogger(__name__)


def _normalize(s) -> str:
    """Lower + sin acentos + strip."""
    s = str(s or "").lower().strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s


# Sinónimos / overrides manuales — para casos donde el nombre del alimento
# en el BD no contiene el nombre genérico de la lista de precios.
# Mapea: substring del alimento (normalizado) → nombre canónico de la lista.
_SINONIMOS = {
    "jitomate": "jitomate",
    "huevo": "huevo",
    "pollo": "pollo",
}


@lru_cache(maxsize=1)
def cargar_lista_precios() -> list[dict]:
    """Carga la lista de precios desde el Excel configurado.

    Devuelve lista de dicts {producto, unidad, precio, key_normalizada}.
    Cacheada con lru_cache para no releer el Excel en cada match.
    """
    path = Path(config.LISTA_PRECIOS_PATH)
    if not path.exists():
        log.warning(f"Lista de precios no encontrada en {path}")
        return []
    try:
        df = pd.read_excel(path, sheet_name="Lista de Precios")
    except Exception as e:
        log.exception(f"Error leyendo lista de precios: {e}")
        return []

    items = []
    for _, row in df.iterrows():
        producto = str(row.get("Producto", "")).strip()
        if not producto:
            continue
        try:
            precio = float(row.get("Precio Unitario", 0))
        except (TypeError, ValueError):
            precio = 0.0
        unidad = str(row.get("Unidad", "")).strip()
        items.append({
            "producto": producto,
            "unidad": unidad,
            "precio": precio,
            "key": _normalize(producto),
        })
    log.info(f"Lista de precios cargada: {len(items)} productos")
    return items


def buscar_precio(alimento: str) -> dict | None:
    """Encuentra el mejor match de precio para un alimento del pedido.

    Algoritmo en orden de prioridad:
      1. Match exacto (mayor prioridad)
      2. Containment bidireccional: key dentro de alimento O alimento dentro de key
         (ej. "Comino entero" ↔ "comino entero 60 g")
      3. Palabras significativas en común (≥4 chars), penalizando diferencias
         (ej. "Acelga manojo" → "ACELGAS")

    Devuelve dict {producto, unidad, precio, key} o None si no hay match.
    """
    if not alimento:
        return None
    items = cargar_lista_precios()
    if not items:
        return None

    a = _normalize(alimento)
    if not a:
        return None
    a_words = [w for w in a.split() if len(w) >= 3]

    best = None
    best_score = 0

    for item in items:
        key = item["key"]
        if not key:
            continue
        k_words = [w for w in key.split() if len(w) >= 3]

        score = 0

        # Estrategia 1: match exacto
        if a == key:
            score = 10000
        # Estrategia 2: containment bidireccional (mucho más fuerte que palabra suelta)
        elif key in a:
            # ej. lista="Acelgas", pedido="acelgas frescas en manojo" → match fuerte
            score = 500 + len(key) * 3
        elif a in key:
            # ej. pedido="Comino entero", lista="comino entero 60 g" → match fuerte
            score = 500 + len(a) * 3
        else:
            # Estrategia 3: palabras significativas en común
            a_set = set(a_words)
            k_set = set(k_words)
            common = a_set & k_set
            # Variantes singular/plural
            if not common:
                expanded_a = set(a_words) | {w[:-1] for w in a_words if w.endswith("s") and len(w) > 4}
                expanded_a |= {w + "s" for w in a_words if not w.endswith("s")}
                expanded_k = set(k_words) | {w[:-1] for w in k_words if w.endswith("s") and len(w) > 4}
                expanded_k |= {w + "s" for w in k_words if not w.endswith("s")}
                common = expanded_a & expanded_k
            if not common:
                continue
            # Score: longitud total de palabras comunes (más es mejor)
            #        - penalización por palabras distintas (rebaja matches genéricos)
            common_len = sum(len(w) for w in common)
            extra_a = len(a_words) - len(common)
            extra_k = len(k_words) - len(common)
            score = common_len * 3 - (extra_a + extra_k)

        if score > best_score:
            best = item
            best_score = score

    # Threshold mínimo: si el score es bajísimo, no es match real
    if best_score < 5:
        return None
    return best


def precio_de(alimento: str) -> float | None:
    """Atajo que devuelve solo el precio numérico (None si no hay match)."""
    m = buscar_precio(alimento)
    return m["precio"] if m else None
