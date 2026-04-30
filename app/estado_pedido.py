"""Estado persistente del pedido del día.

Cuando se procesa un Excel BD, además de generar el Excel y los PDFs,
guardamos un JSON estructurado en storage/pedidos_dia/<fecha-iso>.json con
el detalle por hospital. Este estado es la "fuente de verdad" mutable: si
después llega un ajuste de entrega ("faltó X kg de Y en hospital Z"), se
modifica este estado y se regenera la nota de remisión.

Estructura:
{
  "fecha": "2026-04-27",
  "fecha_legible": "27 de abril",
  "creado": "2026-04-26T20:55:11",
  "ultima_modificacion": "2026-04-26T20:55:11",
  "hospitales": {
    "Hospital de la Mujer Comitán": {
      "productos": [
        {"alimento": "Jitomate guaje", "presentacion": "KILO", "cantidad": 5,
         "precio_unitario": 93.50, "importe": 467.50, "cantidad_original": 5},
        ...
      ],
      "subtotal": 6578.50,
      "total": 6578.50
    },
    ...
  },
  "ajustes": [
    {"timestamp": "2026-04-26T22:10:00", "hospital": "...",
     "alimento": "...", "cantidad_anterior": 5, "cantidad_nueva": 0,
     "razon": "no se surtió", "diferencia_importe": -467.50}
  ]
}
"""
import json
import threading
from datetime import datetime
from pathlib import Path
import pandas as pd

from . import config
from .event_log import log_event
from .pricing import buscar_precio

_state_lock = threading.Lock()


def _state_dir() -> Path:
    d = config.BASE_DIR / "storage" / "pedidos_dia"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_file(fecha_iso: str) -> Path:
    return _state_dir() / f"{fecha_iso}.json"


def guardar_estado(fecha_iso: str, fecha_legible: str, df_fyv: pd.DataFrame,
                    folios: dict[str, str] | None = None) -> Path:
    """Construye y guarda el estado del día desde df_fyv (procesado).

    Para cada hospital, calcula importe usando la lista de precios.
    `folios` opcional: mapping hospital→folio para registrar en el estado.
    """
    folios = folios or {}
    now = datetime.now().isoformat(timespec="seconds")
    hospitales = {}
    for hospital in sorted(df_fyv["UNIDAD"].unique()):
        df_h = (df_fyv[df_fyv["UNIDAD"] == hospital]
                .groupby(["ALIMENTO", "PRESENTACION"])["CANTIDAD"]
                .sum().reset_index().sort_values("ALIMENTO"))
        df_h = df_h[df_h["CANTIDAD"] > 0]
        productos = []
        subtotal = 0.0
        for row in df_h.itertuples(index=False):
            cantidad = float(row.CANTIDAD)
            match = buscar_precio(row.ALIMENTO)
            precio = match["precio"] if match else 0.0
            importe = cantidad * precio
            subtotal += importe
            productos.append({
                "alimento": str(row.ALIMENTO),
                "presentacion": str(row.PRESENTACION),
                "cantidad": cantidad,
                "cantidad_original": cantidad,
                "precio_unitario": precio,
                "importe": round(importe, 2),
                "tiene_precio": match is not None,
            })
        hospitales[hospital] = {
            "folio_remision": folios.get(hospital),
            "estado": "vigente",  # vigente|modificado|aceptado|cancelado
            "estado_actualizado": now,
            "productos": productos,
            "subtotal": round(subtotal, 2),
            "total": round(subtotal, 2),
        }

    state = {
        "fecha": fecha_iso,
        "fecha_legible": fecha_legible,
        "creado": now,
        "ultima_modificacion": now,
        "hospitales": hospitales,
        "ajustes": [],
    }

    path = _state_file(fecha_iso)
    with _state_lock:
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    log_event("storage", f"💾 Estado del pedido guardado: {fecha_iso}",
              {"hospitales": len(hospitales),
               "total": sum(h["total"] for h in hospitales.values())})
    return path


def cargar_estado(fecha_iso: str) -> dict | None:
    """Carga el estado de un día. Devuelve None si no existe."""
    path = _state_file(fecha_iso)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def cargar_estado_mas_reciente() -> tuple[dict | None, str | None]:
    """Si no se especifica fecha, devuelve el estado más reciente (último archivo)."""
    files = sorted(_state_dir().glob("*.json"))
    if not files:
        return None, None
    last = files[-1]
    fecha = last.stem
    try:
        return json.loads(last.read_text(encoding="utf-8")), fecha
    except Exception:
        return None, fecha


def listar_fechas_disponibles() -> list[str]:
    """Devuelve las fechas-ISO que tienen estado guardado, descendente (más reciente primero)."""
    files = sorted(_state_dir().glob("*.json"), reverse=True)
    return [f.stem for f in files]


_MES_NUM = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
    "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def resolver_fecha_iso(texto: str, dias_disponibles: list[str] | None = None,
                        hoy: "datetime | None" = None) -> str | None:
    """Intenta extraer una referencia de fecha del texto del operador.

    Reconoce: 'hoy', 'ayer', 'antier', 'anteayer', 'del 28', 'el 27 de abril',
    '27/04/2026', '2026-04-27'. Si encuentra varios candidatos, devuelve el
    primero que coincida con un día disponible.

    `dias_disponibles` (opcional): si se pasa, solo devuelve fechas que estén
    en esa lista. Útil para no inventar días sin estado.
    `hoy`: para tests; default es datetime.now().
    """
    import re
    if not texto:
        return None
    hoy = hoy or datetime.now()
    t = texto.lower()
    candidatos: list[str] = []

    # Palabras relativas
    if re.search(r"\bhoy\b", t):
        candidatos.append(hoy.strftime("%Y-%m-%d"))
    if re.search(r"\bayer\b", t):
        candidatos.append((hoy - pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    if re.search(r"\bantier\b|\banteayer\b", t):
        candidatos.append((hoy - pd.Timedelta(days=2)).strftime("%Y-%m-%d"))

    # ISO directo: 2026-04-27
    for m in re.finditer(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", t):
        a, mm, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            candidatos.append(datetime(a, mm, d).strftime("%Y-%m-%d"))
        except ValueError:
            pass

    # DD/MM/YYYY o DD/MM
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t):
        d, mm = int(m.group(1)), int(m.group(2))
        a = int(m.group(3)) if m.group(3) else hoy.year
        if a < 100:
            a += 2000
        try:
            candidatos.append(datetime(a, mm, d).strftime("%Y-%m-%d"))
        except ValueError:
            pass

    # "27 de abril" / "del 27 de abril" / "el 27 de abril de 2026"
    for m in re.finditer(
        r"(?:del?\s+|el\s+)?(\d{1,2})\s+de\s+(" + "|".join(_MES_NUM) + r")(?:\s+de\s+(\d{4}))?",
        t,
    ):
        d = int(m.group(1))
        mm = _MES_NUM[m.group(2)]
        a = int(m.group(3)) if m.group(3) else hoy.year
        try:
            candidatos.append(datetime(a, mm, d).strftime("%Y-%m-%d"))
        except ValueError:
            pass

    # "del 28" / "el 27" / "del dia 28" / "el día 27" / "dia 28" — solo número,
    # asumir mes/año actual. Acepta opcionalmente la palabra "día"/"dia".
    if not candidatos:
        for m in re.finditer(
            r"\b(?:(?:del?|el)\s+)?d[ií]a\s+(\d{1,2})\b|\b(?:del?|el)\s+(\d{1,2})\b",
            t,
        ):
            d = int(m.group(1) or m.group(2))
            try:
                candidatos.append(datetime(hoy.year, hoy.month, d).strftime("%Y-%m-%d"))
            except ValueError:
                pass

    if not candidatos:
        return None

    # Filtrar por disponibilidad si se pidió
    if dias_disponibles is not None:
        for c in candidatos:
            if c in dias_disponibles:
                return c
        return None
    return candidatos[0]


def estado_a_contexto_ai(state: dict) -> str:
    """Convierte el estado a un texto compacto que se le inyecta a Claude.

    Sirve para que Claude pueda responder consultas tipo:
      - "qué hospitales pidieron mamey?"
      - "cuánto pidió Comitán de jitomate?"
      - "dame el detalle de Tapachula"
    sin necesidad de releer el archivo original.
    """
    if not state or not state.get("hospitales"):
        return ""
    fecha = state.get("fecha_legible") or state.get("fecha", "?")
    fecha_iso = state.get("fecha", "?")
    lines = [f"[CONTEXTO PEDIDO DEL {fecha} — ya procesado, datos disponibles para consultar]",
             "(NO menciones precios ni montos en tu respuesta — solo cantidades y hospitales)"]
    for hospital, info in state["hospitales"].items():
        productos_activos = [p for p in info.get("productos", []) if p.get("cantidad", 0) > 0]
        if not productos_activos:
            continue
        folio = info.get("folio_remision") or "—"
        lines.append(f"\n▸ {hospital} [folio {folio}]")
        # Productos en una línea compacta
        items = [f"{p['alimento']} ({p['cantidad']} {p['presentacion']})"
                 for p in productos_activos]
        # Wrap en líneas de ~120 chars para legibilidad
        line, max_len = "  ", 120
        for it in items:
            if len(line) + len(it) > max_len:
                lines.append(line.rstrip(", "))
                line = "  "
            line += it + ", "
        if line.strip():
            lines.append(line.rstrip(", "))

    # Agregar contexto de EXTRAS del día (con sus folios) si existen
    try:
        from .extras_pedido import cargar_extras
        extras_state = cargar_extras(fecha_iso)
        if extras_state and extras_state.get("extras"):
            folios_destinos = extras_state.get("folios_por_destino") or {}
            por_destino = {}
            for e in extras_state["extras"]:
                if e.get("cantidad", 0) <= 0:
                    continue
                d = e.get("hospital", "ALMACÉN EHMO")
                por_destino.setdefault(d, []).append(e)
            for destino, items in por_destino.items():
                folio = folios_destinos.get(destino) or "—"
                lines.append(f"\n▸ {destino} (EXTRA — productos no-FyV) [folio {folio}]")
                productos_str = ", ".join(
                    f"{e['alimento']} ({e['cantidad']} {e['presentacion']})"
                    for e in items
                )
                # Wrap a 120 chars
                line2 = "  "
                for it in productos_str.split(", "):
                    if len(line2) + len(it) > 120:
                        lines.append(line2.rstrip(", "))
                        line2 = "  "
                    line2 += it + ", "
                if line2.strip():
                    lines.append(line2.rstrip(", "))
    except Exception:
        pass

    return "\n".join(lines)


def estado_a_dataframe(state: dict) -> pd.DataFrame:
    """Convierte el estado a un DataFrame compatible con generar_notas_remision/pdf.

    Columnas: UNIDAD, ALIMENTO, PRESENTACION, CANTIDAD.
    Solo incluye productos con cantidad > 0.
    """
    rows = []
    for hospital, info in state.get("hospitales", {}).items():
        for p in info.get("productos", []):
            if p.get("cantidad", 0) > 0:
                rows.append({
                    "UNIDAD": hospital,
                    "ALIMENTO": p["alimento"],
                    "PRESENTACION": p["presentacion"],
                    "CANTIDAD": p["cantidad"],
                })
    return pd.DataFrame(rows, columns=["UNIDAD", "ALIMENTO", "PRESENTACION", "CANTIDAD"])
