"""Replay histórico de los cambios del 27-abr al JSON reconstruido.

NO regenera PDFs ni sube a Drive (los originales del 27 ya existen).
"""
from app.ajuste_entrega import aplicar_ajustes
from app.extras_pedido import agregar_extras
import json

FECHA = "2026-04-27"

# ── Faltantes (post-surtido) ────────────────────────────────────────────────
FALTANTES = [
    ("Mujer Comitán",           [{"alimento": "Mamey",     "cantidad_no_entregada": 10}]),
    ("Chiapa de Corzo",         [{"alimento": "Guanábana", "cantidad_no_entregada": 6}]),
    ("Ángel Albino Corzo",      [{"alimento": "Guanábana", "cantidad_no_entregada": 1}]),
    ("Gandulfo",                [{"alimento": "Orégano",   "cantidad_no_entregada": 8},
                                  {"alimento": "Nuez",      "cantidad_no_entregada": 1}]),
    ("Mujer Comitán",           [{"alimento": "Orégano",   "cantidad_no_entregada": 3}]),
    ("Pascasio Gamboa",         [{"alimento": "Nuez",      "cantidad_no_entregada": 1}]),
]

# ── Extras al ALMACÉN EHMO ──────────────────────────────────────────────────
EXTRAS = [
    {"hospital": "ALMACÉN EHMO", "alimento": "Comino entero 60g",                         "cantidad": 2,  "presentacion": "PIEZA"},
    {"hospital": "ALMACÉN EHMO", "alimento": "Comino molido 60g",                         "cantidad": 4,  "presentacion": "PIEZA"},
    {"hospital": "ALMACÉN EHMO", "alimento": "Avena integral",                            "cantidad": 4,  "presentacion": "KILO"},
    {"hospital": "ALMACÉN EHMO", "alimento": "Frijol bayo",                               "cantidad": 39, "presentacion": "KILO"},
    {"hospital": "ALMACÉN EHMO", "alimento": "Fórmula NAN inicio etapa 1 400g",           "cantidad": 10, "presentacion": "PIEZA"},
    {"hospital": "ALMACÉN EHMO", "alimento": "Gelatina sin azúcar",                       "cantidad": 50, "presentacion": "PIEZA"},
    {"hospital": "ALMACÉN EHMO", "alimento": "Pimienta molida negra 60g",                 "cantidad": 10, "presentacion": "PIEZA"},
    {"hospital": "ALMACÉN EHMO", "alimento": "Pimienta entera negra 60g",                 "cantidad": 4,  "presentacion": "PIEZA"},
    {"hospital": "ALMACÉN EHMO", "alimento": "Miel de abeja",                             "cantidad": 1,  "presentacion": "PIEZA"},
    {"hospital": "ALMACÉN EHMO", "alimento": "Mermelada fresa individual (caja 120 pz)",  "cantidad": 7,  "presentacion": "CAJA"},
    {"hospital": "ALMACÉN EHMO", "alimento": "Nieve de sabores",                          "cantidad": 1,  "presentacion": "PIEZA"},
]


def main():
    print("=" * 70)
    print("REPLAY DE CAMBIOS DEL 27-ABR (solo JSON, sin PDFs ni Drive)")
    print("=" * 70)

    print("\n▸ FALTANTES (ajustes post-surtido):")
    for hospital, ajustes in FALTANTES:
        r = aplicar_ajustes(hospital, ajustes, fecha_iso=FECHA, solo_estado=True)
        if not r.get("ok"):
            print(f"  ❌ {hospital}: {r.get('error')}")
            continue
        for c in r["cambios"]:
            print(f"  ✓ {r['hospital_resuelto']:55s} {c['alimento']:30s} "
                  f"{c['cantidad_anterior']:.0f} → {c['cantidad_nueva']:.0f}")
        if r.get("no_encontrados"):
            print(f"    ⚠️ no encontrados: {r['no_encontrados']}")

    print("\n▸ EXTRAS al ALMACÉN EHMO:")
    res = agregar_extras(EXTRAS, fecha_iso=FECHA, fecha_legible="27 de abril")
    for e in res["cambios"]:
        precio_msg = f"${e['precio_unitario']:.2f}" if e["tiene_precio"] else "(sin precio)"
        print(f"  ✓ {e['alimento']:50s} {e['cantidad']:>4} {e['presentacion']:6s} {precio_msg}")
    if res.get("sin_precio"):
        print(f"\n  ⚠️ {len(res['sin_precio'])} extras sin precio en lista — quedaron en $0:")
        for a in res["sin_precio"]:
            print(f"    - {a}")
    print(f"\n  Total extras: ${res['total_extras']:,.2f}")

    # ── Asignar folio 13 al ALMACÉN EHMO en el extras_dia (segun relación) ──
    from app.extras_pedido import cargar_extras, _state_file as extras_state_file
    from app.extras_pedido import _state_lock as extras_lock
    state_extras = cargar_extras(FECHA)
    state_extras["folios_por_destino"] = {"ALMACÉN EHMO": "0000000013"}
    with extras_lock:
        extras_state_file(FECHA).write_text(
            json.dumps(state_extras, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  Folio asignado al ALMACÉN EHMO: 0000000013")

    # ── Resumen final ───────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("RESUMEN FINAL — comparar con tu Google Sheets")
    print("=" * 70)
    state = json.loads(open(f"storage/pedidos_dia/{FECHA}.json", encoding="utf-8").read())
    total_regular = 0.0
    print("\n  Hospitales regulares:")
    for h, info in state["hospitales"].items():
        folio = info.get("folio_remision") or "—"
        total = info.get("total", 0)
        total_regular += total
        nombre_short = h.replace("Hospital ", "")[:55]
        print(f"    [{folio}] {nombre_short:55s} ${total:>12,.2f}")

    extras_state = cargar_extras(FECHA)
    total_extra = sum(e["importe"] for e in (extras_state.get("extras") or []) if e["cantidad"] > 0)
    print(f"\n    [0000000013] ALMACÉN EHMO (EXTRA)                              ${total_extra:>12,.2f}")
    print(f"\n  Total regular:  ${total_regular:>12,.2f}")
    print(f"  Total extras:   ${total_extra:>12,.2f}")
    print(f"  TOTAL DEL DÍA:  ${total_regular + total_extra:>12,.2f}")
    print(f"\n  (Relación que viste anteriormente decía: $182,152.59)")


if __name__ == "__main__":
    main()
