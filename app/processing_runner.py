"""Disparador del procesamiento de pedidos.

Cuando Claude responde con accion="procesar_archivo" y hay un Excel adjunto,
este módulo ejecuta el procesador, sube el resultado a Drive y devuelve la
información para mandar como mensaje de seguimiento al usuario.
"""
import logging
from pathlib import Path

from . import config
from . import message_log
from .pedido_processor import procesar_pedido
from .drive_uploader import upload_file as drive_upload

log = logging.getLogger(__name__)


def maybe_process(phone: str, attachment_path: Path | None, ai_result: dict,
                  original_filename: str | None = None,
                  agente: dict | None = None) -> dict | None:
    """Disparador post-AI: ejecuta el pipeline correcto según la acción.

    - accion=procesar_archivo: corre el procesador de pedidos completo.
    - accion=aplicar_ajuste:   aplica modificaciones al estado del día y
                                regenera la nota de remisión corregida.
    Devuelve un dict con info del resultado (o None si no aplica).
    Loguea automáticamente un mensaje "out" para que el dashboard lo refleje.
    """
    accion = ai_result.get("accion")
    # fecha_iso es opcional. Si Claude la extrajo del mensaje ("del 27", "ayer"),
    # los handlers de consulta operan sobre ESE día en lugar del más reciente.
    fecha_iso_pedida = (ai_result.get("datos") or {}).get("fecha_iso") or None

    # IMPORTANTE: solo disparamos el handler cuando 'accion' es explícita.
    # Si Claude detectó la intención pero falta info y está pidiendo aclaración,
    # accion="nada" y NO debe ejecutar nada (es solo conversación).

    # Caso 0a: recargar lista de precios (después de editar el Excel)
    if accion == "recargar_precios":
        return _recargar_precios(phone)

    # Caso 0b: consolidar notas vigentes en un solo PDF
    if accion == "consolidar_notas":
        return _consolidar_notas(phone, fecha_iso=fecha_iso_pedida)

    # Caso 0c: generar/regenerar la relación de documentos del día
    if accion == "generar_relacion":
        return _generar_relacion_handler(phone, fecha_iso=fecha_iso_pedida)

    # Caso 0d: procesar pedido extraído de libreta (fotos manuscritas, sin Excel).
    # Usado por el agente SUREÑA Comedores. El AI ya extrajo y confirmó los
    # datos con el operador (contenido + fecha de entrega) antes de disparar esto.
    if accion == "procesar_libreta":
        return _procesar_libreta_desde_ai(phone, ai_result, agente=agente)

    # Caso 0d-bis: registrar pesos reportados por el operador (PASO 2 del flujo
    # SUREÑA). Reemplaza cantidades en manojos/piezas por kg reales y emite
    # las notas de remisión.
    if accion == "registrar_pesos":
        return _registrar_pesos_desde_ai(phone, ai_result, agente=agente)

    # Caso 0e: imprimir una nota específica por folio
    if accion == "imprimir_nota_folio":
        folio = (ai_result.get("datos") or {}).get("folio")
        return _imprimir_nota_folio(phone, folio, fecha_iso=fecha_iso_pedida)

    # Caso 0e–h: control de documentos (estados de folios)
    if accion == "aceptar_folio":
        folios = (ai_result.get("datos") or {}).get("folios") or []
        return _control_estado(phone, folios, "aceptar", fecha_iso=fecha_iso_pedida)
    if accion == "cancelar_folio":
        folios = (ai_result.get("datos") or {}).get("folios") or []
        return _control_estado(phone, folios, "cancelar", fecha_iso=fecha_iso_pedida)
    if accion == "reactivar_folio":
        folios = (ai_result.get("datos") or {}).get("folios") or []
        return _control_estado(phone, folios, "reactivar", fecha_iso=fecha_iso_pedida)
    if accion == "reporte_control":
        return _reporte_control(phone, fecha_iso=fecha_iso_pedida)

    # Caso 1: modificación del pedido (cliente cambia ANTES de surtir, productos FyV)
    if accion == "aplicar_modificacion":
        return _aplicar_modificaciones_desde_ai(phone, ai_result)

    # Caso 2: extras para cubrir desabasto (productos NO-FyV, hoja y nota APARTE)
    if accion == "aplicar_extra":
        return _aplicar_extras_desde_ai(phone, ai_result)

    # Caso 3: ajustes de entrega (personal después de surtir)
    if accion == "aplicar_ajuste":
        return _aplicar_ajustes_desde_ai(phone, ai_result)

    if accion != "procesar_archivo":
        return None
    if not attachment_path:
        return None
    if attachment_path.suffix.lower() not in (".xlsx", ".xls"):
        return None

    # Detectar si el operador YA confirmó reproceso explícito. Claude pone
    # datos.confirmar_reproceso=true cuando detecta la frase exacta:
    # "reprocesa el ... desde cero, sí estoy seguro" (o variantes).
    force_overwrite = bool((ai_result.get("datos") or {}).get("confirmar_reproceso"))

    # Procesar
    try:
        result = procesar_pedido(attachment_path, config.PROCESSED_DIR,
                                  original_filename=original_filename,
                                  force_overwrite=force_overwrite,
                                  agente=agente)
    except Exception as e:
        log.exception(f"Error procesando pedido: {e}")
        msg = f"⚠️ No pude procesar el pedido: {e}"
        message_log.log_message("out", phone, "text", msg, {"processed": False, "error": str(e)})
        return {"error": str(e)}

    if not result:
        msg = ("⚠️ El Excel no tiene la hoja 'BD' que necesito para procesar. "
               "Por favor mándame el archivo original de EHMO con esa hoja.")
        message_log.log_message("out", phone, "text", msg, {"processed": False, "error": "no BD sheet"})
        return {"error": "no BD sheet"}

    # Salvaguarda: día ya existente (NO se procesó). Avisar al operador.
    if result.get("error") == "estado_existente":
        msg = result["mensaje_para_operador"]
        message_log.log_message("out", phone, "text", msg,
                                 {"processed": False, "blocked_overwrite": True,
                                  "fecha_iso": result.get("fecha_iso")})
        return {"blocked_overwrite": True, "fecha_iso": result.get("fecha_iso")}

    output_path = result["output_path"]
    pdf_path = result.get("pdf_path")
    lista_compras_path = result.get("lista_compras_path")
    lista_compras_xlsx_path = result.get("lista_compras_xlsx_path")
    notas_path = result.get("notas_path")
    relacion_path = result.get("relacion_path")
    relacion_pdf_path = result.get("relacion_pdf_path")
    notas_total = result.get("notas_total_general")
    notas_sin_precio = result.get("notas_sin_precio", 0)
    desconocidos = result.get("hospitales_desconocidos", [])
    sin_fyv = result.get("hospitales_si_sin_pedido_fyv", [])
    con_fyv = result.get("hospitales_con_fyv", [])
    excluidos = result.get("hospitales_excluidos_detectados", [])
    productos_cambio = result.get("productos_cambio_lote", [])
    fecha = result.get("fecha", "fecha desconocida")

    # Subir Excel, PDF, Notas y Relación a Drive en subcarpeta por fecha
    from .pedido_processor import fecha_a_iso
    subfolder = fecha_a_iso(fecha)
    drive_info = drive_upload(output_path, subfolder=subfolder)
    drive_pdf = drive_upload(pdf_path, subfolder=subfolder) if pdf_path else None
    drive_lista_compras = drive_upload(lista_compras_path, subfolder=subfolder) if lista_compras_path else None
    drive_lista_compras_xlsx = drive_upload(lista_compras_xlsx_path, subfolder=subfolder) if lista_compras_xlsx_path else None
    drive_notas = drive_upload(notas_path, subfolder=subfolder) if notas_path else None
    drive_relacion = drive_upload(relacion_path, subfolder=subfolder) if relacion_path else None
    drive_relacion_pdf = drive_upload(relacion_pdf_path, subfolder=subfolder) if relacion_pdf_path else None

    # ─── Mensaje completo (lo construye Python, no se corta nunca) ───────────
    lines = [
        f"📊 *Pedido procesado* — {fecha}",
        f"Archivo: *{output_path.name}*",
    ]

    # Hospitales a surtir hoy (enumerados)
    lines.append("")
    lines.append(f"🏥 *Hospitales a surtir hoy ({len(con_fyv)}):*")
    for i, h in enumerate(con_fyv, 1):
        lines.append(f"{i}. {h}")

    # Excluidos detectados
    if excluidos:
        lines.append("")
        lines.append(f"🚫 *Excluidos detectados ({len(excluidos)}) — NO se surten:*")
        for i, h in enumerate(excluidos, 1):
            lines.append(f"{i}. {h}")

    # Productos del cambio de lote (los que el procesador realmente movió)
    if productos_cambio:
        lines.append("")
        lines.append(f"🔄 *Productos movidos Lote 1 → Lote 5 ({len(productos_cambio)}):*")
        for i, p in enumerate(productos_cambio, 1):
            lines.append(f"{i}. {p}")

    # Hospitales conocidos pero sin pedido FyV hoy
    if sin_fyv:
        lines.append("")
        lines.append(f"ℹ️ *{len(sin_fyv)} hospital(es) NO pidieron FyV hoy* (solo otros lotes):")
        for h in sin_fyv:
            lines.append(f"  • {h}")

    # Hospitales no en catálogo (revisión humana)
    if desconocidos:
        lines.append("")
        lines.append(f"⚠️ *{len(desconocidos)} hospital(es) NO en catálogo* — confirma si los surtes:")
        for h in desconocidos:
            lines.append(f"  • {h}")

    # Links de Drive
    lines.append("")
    if drive_info:
        lines.append(f"📂 Excel completo en Drive: {drive_info['link']}")
    else:
        lines.append("⚠️ Excel guardado local (no se pudo subir a Drive)")
    if drive_pdf:
        lines.append(f"🖨️ PDF para imprimir (1 hoja x hospital): {drive_pdf['link']}")
    elif pdf_path:
        lines.append("⚠️ PDF guardado local (no se pudo subir a Drive)")
    if drive_lista_compras:
        lines.append(f"🛒 Lista de compras (PDF para imprimir): {drive_lista_compras['link']}")
    elif lista_compras_path:
        lines.append("⚠️ Lista de compras PDF guardada local (no se pudo subir a Drive)")
    if drive_lista_compras_xlsx:
        lines.append(f"📝 Lista de compras (Excel editable): {drive_lista_compras_xlsx['link']}")
    elif lista_compras_xlsx_path:
        lines.append("⚠️ Lista de compras Excel guardada local (no se pudo subir a Drive)")
    if drive_notas:
        warn_str = f" ⚠️ {notas_sin_precio} sin precio" if notas_sin_precio else ""
        lines.append(f"💵 Notas de remisión con precios{warn_str}: {drive_notas['link']}")
    elif notas_path:
        lines.append("⚠️ Notas de remisión guardadas local (no se pudo subir a Drive)")
    if drive_relacion:
        lines.append(f"📋 Relación de documentos (Excel): {drive_relacion['link']}")
    elif relacion_path:
        lines.append("⚠️ Relación Excel guardada local (no se pudo subir a Drive)")
    if drive_relacion_pdf:
        lines.append(f"📋 Relación de documentos (PDF horizontal para imprimir): {drive_relacion_pdf['link']}")
    elif relacion_pdf_path:
        lines.append("⚠️ Relación PDF guardada local (no se pudo subir a Drive)")

    msg = "\n".join(lines)

    meta = {
        "processed": True,
        "output_name": output_path.name,
        "hospitales_desconocidos": desconocidos,
        "hospitales_si_sin_pedido_fyv": sin_fyv,
    }
    if drive_info:
        meta["drive_link"] = drive_info["link"]
        meta["drive_id"] = drive_info["id"]
    if drive_pdf:
        meta["drive_pdf_link"] = drive_pdf["link"]
        meta["drive_pdf_id"] = drive_pdf["id"]
    if drive_notas:
        meta["drive_notas_link"] = drive_notas["link"]
        meta["drive_notas_id"] = drive_notas["id"]
        meta["notas_total"] = notas_total
    message_log.log_message("out", phone, "text", msg, meta)

    return {
        "output_path": str(output_path),
        "output_name": output_path.name,
        "pdf_path": str(pdf_path) if pdf_path else None,
        "notas_path": str(notas_path) if notas_path else None,
        "notas_total_general": notas_total,
        "drive": drive_info,
        "drive_pdf": drive_pdf,
        "drive_notas": drive_notas,
        "hospitales_si": result.get("hospitales_si", []),
        "hospitales_con_fyv": con_fyv,
        "hospitales_si_sin_pedido_fyv": sin_fyv,
        "hospitales_desconocidos": desconocidos,
        "hospitales_excluidos_detectados": result.get("hospitales_excluidos_detectados", []),
    }


def _aplicar_ajustes_desde_ai(phone: str, ai_result: dict) -> dict:
    """Aplica los ajustes que Claude extrajo del mensaje texto."""
    from .ajuste_entrega import aplicar_ajustes

    datos = ai_result.get("datos", {}) or {}
    fecha_iso = datos.get("fecha_iso") or None

    # Normalizar a una lista de (hospital, ajustes)
    grupos: list[tuple[str, list]] = []
    if "ajustes_por_hospital" in datos:
        for g in datos["ajustes_por_hospital"]:
            grupos.append((g.get("hospital", ""), g.get("ajustes", []) or []))
    elif "hospital" in datos:
        grupos.append((datos.get("hospital", ""), datos.get("ajustes", []) or []))
    else:
        msg = "⚠️ Detecté un ajuste pero no entendí el hospital o productos. Reformula?"
        message_log.log_message("out", phone, "text", msg, {"ajuste_error": True})
        return {"error": "datos incompletos"}

    resultados = []
    lines = ["📝 *Ajustes aplicados:*"]
    if fecha_iso:
        lines[0] = f"📝 *Ajustes aplicados* (día {fecha_iso}):"
    last_relacion = None
    last_relacion_pdf = None
    for hospital_input, ajustes in grupos:
        if not ajustes:
            continue
        r = aplicar_ajustes(hospital_input, ajustes, fecha_iso=fecha_iso)
        resultados.append(r)
        if not r.get("ok"):
            lines.append(f"❌ {hospital_input}: {r.get('error', 'error')}")
            continue
        lines.append("")
        lines.append(f"🏥 *{r['hospital_resuelto']}*")
        for c in r["cambios"]:
            antes = c["cantidad_anterior"]
            despues = c["cantidad_nueva"]
            if despues == 0:
                lines.append(f"  ❌ {c['alimento']}: ELIMINADO ({antes} → 0)")
            else:
                lines.append(f"  ➖ {c['alimento']}: {antes} → {despues}")
        if r.get("no_encontrados"):
            lines.append(f"  ⚠️ no encontré: {', '.join(r['no_encontrados'])}")
        if r.get("drive_link"):
            lines.append(f"  🖨️ Nota corregida: {r['drive_link']}")
        # Capturar el último link de relación (la última iteración tiene el estado final)
        if r.get("drive_relacion_link"):
            last_relacion = r["drive_relacion_link"]
        if r.get("drive_relacion_pdf_link"):
            last_relacion_pdf = r["drive_relacion_pdf_link"]

    if last_relacion or last_relacion_pdf:
        lines.append("")
        if last_relacion:
            lines.append(f"📋 Relación actualizada (Excel): {last_relacion}")
        if last_relacion_pdf:
            lines.append(f"📋 Relación actualizada (PDF): {last_relacion_pdf}")

    msg = "\n".join(lines)
    meta = {"ajuste": True, "resultados": resultados}
    message_log.log_message("out", phone, "text", msg, meta)

    return {"ajuste": True, "resultados": resultados}


def _aplicar_modificaciones_desde_ai(phone: str, ai_result: dict) -> dict:
    """Aplica modificaciones pre-surtido y regenera el PDF imprimible + notas."""
    from .modificacion_pedido import aplicar_modificaciones, regenerar_archivos

    datos = ai_result.get("datos", {}) or {}
    fecha_iso_pedida = datos.get("fecha_iso") or None
    grupos: list[tuple[str, list]] = []
    if "modificaciones_por_hospital" in datos:
        for g in datos["modificaciones_por_hospital"]:
            grupos.append((g.get("hospital", ""), g.get("modificaciones", []) or []))
    elif "hospital" in datos:
        grupos.append((datos.get("hospital", ""), datos.get("modificaciones", []) or []))
    else:
        msg = "⚠️ Detecté una modificación pero no entendí el hospital o productos. Reformula?"
        message_log.log_message("out", phone, "text", msg, {"modificacion_error": True})
        return {"error": "datos incompletos"}

    resultados = []
    lines = ["📦 *Modificaciones aplicadas al pedido:*"]
    if fecha_iso_pedida:
        lines[0] = f"📦 *Modificaciones aplicadas al pedido* (día {fecha_iso_pedida}):"
    fecha_iso = None
    for hospital_input, modificaciones in grupos:
        if not modificaciones:
            continue
        r = aplicar_modificaciones(hospital_input, modificaciones, fecha_iso=fecha_iso_pedida)
        resultados.append(r)
        if not r.get("ok"):
            lines.append(f"❌ {hospital_input}: {r.get('error', 'error')}")
            continue
        fecha_iso = r["fecha"]
        lines.append("")
        lines.append(f"🏥 *{r['hospital_resuelto']}*")
        for c in r["cambios"]:
            op_icon = {"agregar": "➕", "restar": "➖", "cancelar": "❌", "fijar": "🔧"}.get(c["operacion"], "•")
            antes = c["cantidad_anterior"]
            despues = c["cantidad_nueva"]
            unidad = c.get("presentacion", "")
            if c["operacion"] == "agregar" and antes == 0:
                lines.append(f"  {op_icon} {c['alimento']}: 0 → {despues} {unidad}  (NUEVO)")
            elif c["operacion"] in ("cancelar", "eliminar"):
                lines.append(f"  {op_icon} {c['alimento']}: {antes} → 0 {unidad}  (ELIMINADO)")
            else:
                lines.append(f"  {op_icon} {c['alimento']}: {antes} → {despues} {unidad}")
        if r.get("no_encontrados"):
            lines.append(f"  ⚠️ no encontré: {', '.join(r['no_encontrados'])}")

    # Regenerar PDF imprimible + lista compras + notas con el estado actualizado
    if fecha_iso:
        regen = regenerar_archivos(fecha_iso)
        lines.append("")
        if regen.get("drive_pdf_imprimible"):
            lines.append(f"🖨️ PDF imprimible actualizado: {regen['drive_pdf_imprimible']['link']}")
        if regen.get("drive_lista_compras"):
            lines.append(f"🛒 Lista de compras actualizada (PDF): {regen['drive_lista_compras']['link']}")
        if regen.get("drive_lista_compras_xlsx"):
            lines.append(f"📝 Lista de compras actualizada (Excel): {regen['drive_lista_compras_xlsx']['link']}")
        if regen.get("drive_notas"):
            lines.append(f"💵 Notas con precios actualizadas: {regen['drive_notas']['link']}")
        if regen.get("drive_relacion"):
            lines.append(f"📋 Relación actualizada (Excel): {regen['drive_relacion']['link']}")
        if regen.get("drive_relacion_pdf"):
            lines.append(f"📋 Relación actualizada (PDF): {regen['drive_relacion_pdf']['link']}")
    else:
        lines.append("")
        lines.append("⚠️ No se regeneraron archivos (ningún hospital se modificó).")

    msg = "\n".join(lines)
    meta = {"modificacion": True, "resultados": resultados}
    message_log.log_message("out", phone, "text", msg, meta)
    return {"modificacion": True, "resultados": resultados}


def _aplicar_extras_desde_ai(phone: str, ai_result: dict) -> dict:
    """Procesa extras para cubrir desabasto. Genera/actualiza hoja + nota APARTE."""
    from .extras_pedido import agregar_extras, regenerar_archivos_extras

    datos = ai_result.get("datos", {}) or {}
    extras_input = datos.get("extras") or []
    if not extras_input:
        msg = "⚠️ Detecté un extra pero no extraje productos. Reformula con cantidad y producto?"
        message_log.log_message("out", phone, "text", msg, {"extra_error": True})
        return {"error": "datos incompletos"}

    res = agregar_extras(extras_input)
    if not res.get("ok"):
        msg = f"❌ No pude agregar los extras: {res.get('error', 'error')}"
        message_log.log_message("out", phone, "text", msg, {"extra_error": True})
        return {"error": res.get("error", "?")}

    fecha_iso = res["fecha"]
    regen = regenerar_archivos_extras(fecha_iso)

    lines = ["📦 *EXTRAS solicitados (cubrir desabasto):*"]
    for c in res["cambios"]:
        precio_marca = "" if c["tiene_precio"] else "  ⚠️ (sin precio en lista)"
        lines.append(f"  ➕ {c['hospital']}: {c['cantidad']} {c['presentacion']} de "
                     f"*{c['alimento']}*{precio_marca}")
    if res.get("sin_precio"):
        lines.append("")
        lines.append(f"⚠️ Sin precio en la lista oficial: {', '.join(res['sin_precio'])}")
        lines.append("    Agrégalos a Lista_Precios_EHMO.xlsx para que cobren correctamente.")

    lines.append("")
    if regen.get("drive_hoja_surtido"):
        lines.append(f"🖨️ Hoja de surtido EXTRAS: {regen['drive_hoja_surtido']['link']}")
    if regen.get("drive_notas"):
        lines.append(f"💵 Nota de remisión EXTRAS: {regen['drive_notas']['link']}")

    msg = "\n".join(lines)
    meta = {"extra": True, "fecha": fecha_iso, "cambios": res["cambios"]}
    if regen.get("drive_hoja_surtido"):
        meta["drive_hoja_extras"] = regen["drive_hoja_surtido"]["link"]
    if regen.get("drive_notas"):
        meta["drive_notas_extras"] = regen["drive_notas"]["link"]
    message_log.log_message("out", phone, "text", msg, meta)

    return {"extra": True, "fecha": fecha_iso, "cambios": res["cambios"],
            "regen": regen}


def _procesar_libreta_desde_ai(phone: str, ai_result: dict,
                                 agente: dict | None = None) -> dict:
    """Procesa un pedido de libreta (foto) tras la doble confirmación del AI.

    Espera que el AI haya devuelto datos con la siguiente estructura:
      datos.fecha_entrega: "YYYY-MM-DD"
      datos.fecha_legible: "30 de abril"  (opcional, se infiere si no viene)
      datos.destinos: [
        {"destino": "Comedor Patria",
         "productos": [{"alimento": "...", "cantidad": N, "presentacion": "..."}]},
        ...
      ]
    """
    from .libreta_processor import procesar_pedido_libreta
    from .pedido_processor import _MESES_NUMERO

    datos = ai_result.get("datos") or {}
    destinos = datos.get("destinos") or []
    fecha_iso = (datos.get("fecha_entrega") or "").strip()
    fecha_legible = (datos.get("fecha_legible") or datos.get("fecha_entrega_legible") or "").strip()

    if not destinos:
        msg = "⚠️ El AI dijo procesar_libreta pero no me pasó destinos. Reintenta o reenvía la foto."
        message_log.log_message("out", phone, "text", msg,
                                 {"libreta_error": "sin destinos",
                                  "agent_id": (agente or {}).get("id")})
        return {"error": "sin destinos en datos"}

    if not fecha_iso:
        msg = "⚠️ Falta la fecha de entrega. Confirma la fecha (YYYY-MM-DD o 'X de mes')."
        message_log.log_message("out", phone, "text", msg,
                                 {"libreta_error": "sin fecha"})
        return {"error": "sin fecha de entrega"}

    # Si vino legible pero no iso, intentar parsear
    # (lo común es que el AI mande fecha_entrega ISO; este fallback es defensivo)
    from datetime import datetime as _dt
    try:
        d = _dt.strptime(fecha_iso, "%Y-%m-%d").date()
    except ValueError:
        # Intentar parsear "30 de abril" → ISO con año actual
        try:
            partes = fecha_iso.lower().split()
            dia = int(partes[0])
            mes = _MESES_NUMERO.get(partes[-1].rstrip("."))
            if not mes:
                raise ValueError("mes desconocido")
            d = _dt(_dt.now().year, mes, dia).date()
            fecha_iso = d.isoformat()
        except Exception:
            msg = f"⚠️ Fecha '{fecha_iso}' no la pude parsear. Mándamela como YYYY-MM-DD o '30 de abril'."
            message_log.log_message("out", phone, "text", msg,
                                     {"libreta_error": "fecha no parseable"})
            return {"error": "fecha no parseable"}

    if not fecha_legible:
        # Construir desde el ISO
        meses_es = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        fecha_legible = f"{d.day} de {meses_es[d.month - 1]}"

    # Disparar el procesador
    try:
        result = procesar_pedido_libreta(
            destinos=destinos,
            fecha_entrega_iso=fecha_iso,
            fecha_entrega_legible=fecha_legible,
            agente=agente,
        )
    except Exception as e:
        log.exception(f"Error en procesar_pedido_libreta: {e}")
        msg = f"⚠️ Error procesando la libreta: {e}"
        message_log.log_message("out", phone, "text", msg,
                                 {"libreta_error": str(e)})
        return {"error": str(e)}

    if result.get("error"):
        msg = f"⚠️ {result['error']}"
        message_log.log_message("out", phone, "text", msg, {"libreta_error": True})
        return result

    # Mensaje de seguimiento al operador con los links
    lines = [
        f"✓ *Documentos de surtido generados* — {fecha_legible}",
        f"📦 {result['n_destinos']} destino(s), {result['n_productos_lineas']} producto-líneas.",
        "",
    ]
    if result.get("drive_pdf"):
        lines.append(f"🖨️ PDF imprimible (uno por comedor): {result['drive_pdf']['link']}")
    if result.get("drive_lc_pdf"):
        lines.append(f"🛒 Lista de compras (consolidada PDF): {result['drive_lc_pdf']['link']}")
    if result.get("drive_lc_xlsx"):
        lines.append(f"📝 Lista de compras (Excel editable): {result['drive_lc_xlsx']['link']}")

    # Identificar productos que NO son kg (vinieron en manojos, piezas, caja...)
    # para pedir explícitamente sus pesos reales.
    no_kg_por_destino: dict[str, list[str]] = {}
    for d in (datos.get("destinos") or []):
        destino_nombre = (d.get("destino") or "").strip()
        if not destino_nombre:
            continue
        # Normaliza el nombre como hace libreta_processor
        if not destino_nombre.lower().startswith("comedor "):
            CONOCIDOS = {"patria", "cci", "6 de junio", "seis de junio",
                          "shanka", "jobo", "copoya"}
            if destino_nombre.lower() in CONOCIDOS:
                destino_nombre = f"Comedor {destino_nombre}"
        no_kg_items = []
        for p in (d.get("productos") or []):
            pres = (p.get("presentacion") or "").lower().strip()
            if pres and not pres.startswith(("kg", "kil")):
                cant = p.get("cantidad")
                alimento = p.get("alimento") or ""
                no_kg_items.append(f"{alimento.strip().lower()} ({cant} {p.get('presentacion')})")
        if no_kg_items:
            no_kg_por_destino[destino_nombre] = no_kg_items

    lines.append("")
    if no_kg_por_destino:
        lines.append("⚖️ *Cuando termines de surtir, mándame los KG reales de los productos en otras unidades:*")
        for destino, items in no_kg_por_destino.items():
            lines.append(f"   • *{destino}*: {', '.join(items)}")
        lines.append("")
        lines.append("Los productos que pediste en kg ya están listos (no hace falta repesar a menos que cambien).")
        lines.append("Con esos pesos genero la nota de remisión con precios.")
    else:
        lines.append("⚖️ Todos los productos vinieron en kg. Confirma cuando termines de surtir si los kg cambiaron, sino emito la nota tal cual.")
    msg = "\n".join(lines)
    out_meta = {"libreta": True, "fecha_iso": fecha_iso,
                "destinos": result["destinos"]}
    if agente:
        out_meta["agent_id"] = agente.get("id")
    message_log.log_message("out", phone, "text", msg, out_meta)

    return {"libreta": True, **{k: v for k, v in result.items() if k != "df_fyv"}}


def _registrar_pesos_desde_ai(phone: str, ai_result: dict,
                                agente: dict | None = None) -> dict:
    """Aplica pesos reportados (texto o foto) al state y emite notas.

    Espera datos:
      datos.fecha_iso: 'YYYY-MM-DD'  (opcional; si falta usa más reciente con requires_pesos)
      datos.pesos: [
        {"destino": "Comedor Patria", "alimento": "Espinacas", "kg": 4.5},
        ...
      ]
    """
    from .libreta_processor import aplicar_pesos
    from .estado_pedido import cargar_estado_mas_reciente

    datos = ai_result.get("datos") or {}
    pesos = datos.get("pesos") or []
    fecha_iso = (datos.get("fecha_iso") or datos.get("fecha_entrega") or "").strip()

    if not pesos:
        msg = "⚠️ No identifiqué pesos en tu mensaje. Mándamelos así: 'Patria: espinacas 4kg, sandías 18kg'."
        message_log.log_message("out", phone, "text", msg, {"pesos_error": "sin pesos"})
        return {"error": "sin pesos en datos"}

    # Si no vino fecha, usar el día más reciente con requires_pesos
    if not fecha_iso:
        state, fecha_reciente = cargar_estado_mas_reciente()
        if state and state.get("requires_pesos"):
            fecha_iso = fecha_reciente
        else:
            msg = "⚠️ No me dijiste la fecha y no encontré un día reciente esperando pesos. Indícame la fecha."
            message_log.log_message("out", phone, "text", msg, {"pesos_error": "sin fecha"})
            return {"error": "sin fecha"}

    cliente = (agente or {}).get("cliente_id", "SURENA")

    try:
        result = aplicar_pesos(pesos, fecha_iso, cliente=cliente)
    except Exception as e:
        log.exception(f"Error en aplicar_pesos: {e}")
        msg = f"⚠️ Error procesando pesos: {e}"
        message_log.log_message("out", phone, "text", msg, {"pesos_error": str(e)})
        return {"error": str(e)}

    if result.get("error"):
        msg = f"⚠️ {result['error']}"
        message_log.log_message("out", phone, "text", msg, {"pesos_error": True})
        return result

    # Mensaje al operador
    cambios = result.get("cambios") or []
    no_encontrados = result.get("no_encontrados") or []
    todos_kg = result.get("todos_kg")
    lines = [f"⚖️ *Pesos registrados* — {fecha_iso}"]
    if cambios:
        # Agrupar por destino para legibilidad
        por_destino: dict[str, list[str]] = {}
        for c in cambios:
            por_destino.setdefault(c["destino"], []).append(
                f"{c['alimento']} ({c['cantidad_anterior']} {c['presentacion_anterior']} → {c['kg_aplicado']} kg)"
            )
        for destino, items in por_destino.items():
            lines.append(f"   • *{destino}*: {', '.join(items)}")
    if no_encontrados:
        lines.append("")
        lines.append(f"⚠️ No pude identificar: {', '.join(no_encontrados)}")
    lines.append("")
    if todos_kg:
        lines.append(f"💰 *Total del día (regular): ${result['total_dia']:,.2f}*")
        if result.get("drive_notas"):
            lines.append(f"📑 Notas de remisión: {result['drive_notas']['link']}")
        if result.get("drive_relacion"):
            lines.append(f"📋 Relación de documentos: {result['drive_relacion']['link']}")
    else:
        lines.append("⏸️ Aún hay productos en unidades no-kg. Mándame los pesos faltantes para emitir las notas.")

    msg = "\n".join(lines)
    out_meta = {"pesos": True, "fecha_iso": fecha_iso, "cambios": len(cambios)}
    if agente:
        out_meta["agent_id"] = agente.get("id")
    message_log.log_message("out", phone, "text", msg, out_meta)

    return {"pesos": True, **{k: v for k, v in result.items()
                                if not isinstance(v, type(message_log))}}


def _generar_relacion_handler(phone: str, fecha_iso: str | None = None) -> dict:
    """Genera Excel + PDF de la Relación de Documentos.

    Si fecha_iso se pasa, opera sobre ESE día. Sino, sobre el más reciente.
    """
    from .estado_pedido import cargar_estado, cargar_estado_mas_reciente
    from .relacion_documentos import generar_relacion_dia, generar_relacion_dia_pdf
    from datetime import datetime
    from . import config

    if fecha_iso:
        state = cargar_estado(fecha_iso)
    else:
        state, fecha_iso = cargar_estado_mas_reciente()
    if not state:
        msg = "⚠️ No hay pedido del día procesado. Procesa el Excel primero."
        message_log.log_message("out", phone, "text", msg, {"relacion": False})
        return {"error": "no hay pedido"}

    fecha_legible = state.get("fecha_legible", fecha_iso)
    ts = datetime.now().strftime("%H%M%S")
    drive_xlsx = None
    drive_pdf = None

    try:
        rel = generar_relacion_dia(fecha_iso, fecha_legible=fecha_legible,
                                    output_path=config.PROCESSED_DIR /
                                    f"Relación Documentos {fecha_legible} {ts}.xlsx")
        if rel and not rel.get("error"):
            d = drive_upload(rel["output_path"], subfolder=fecha_iso)
            if d: drive_xlsx = d["link"]
        rel_pdf = generar_relacion_dia_pdf(fecha_iso, fecha_legible=fecha_legible,
                                            output_path=config.PROCESSED_DIR /
                                            f"Relación Documentos {fecha_legible} {ts}.pdf")
        if rel_pdf and not rel_pdf.get("error"):
            d = drive_upload(rel_pdf["output_path"], subfolder=fecha_iso)
            if d: drive_pdf = d["link"]
    except Exception as e:
        log.exception(f"Error generando relación: {e}")
        log_event("processor", f"⚠️ Error generando relación: {e}", level="warn")
        msg = f"⚠️ Error al generar la relación: {e}"
        message_log.log_message("out", phone, "text", msg, {"relacion": False})
        return {"error": str(e)}

    lines = [f"📋 *Relación de documentos* — {fecha_legible}"]
    if drive_xlsx:
        lines.append(f"📊 Excel editable: {drive_xlsx}")
    if drive_pdf:
        lines.append(f"🖨️ PDF horizontal para imprimir: {drive_pdf}")
    if not drive_xlsx and not drive_pdf:
        lines.append("⚠️ Generada local pero no se pudo subir a Drive.")

    msg = "\n".join(lines)
    meta = {"relacion": True, "drive_xlsx": drive_xlsx, "drive_pdf": drive_pdf}
    message_log.log_message("out", phone, "text", msg, meta)
    return {"relacion": True, "drive_xlsx": drive_xlsx, "drive_pdf": drive_pdf}


def _control_estado(phone: str, folios: list, accion: str,
                     fecha_iso: str | None = None) -> dict:
    """Aplica una acción (aceptar/cancelar/reactivar) a una lista de folios.

    Si fecha_iso se pasa, opera sobre ESE día. Sino, primero intenta el más
    reciente y, si el folio no aparece ahí, busca en los otros días disponibles
    (UX: el operador puede decir "acepta el folio 14" sin recordar el día).
    """
    from .control_documentos import (
        aceptar_folio, cancelar_folio, reactivar_folio, ESTADO_ICON,
    )
    from .estado_pedido import listar_fechas_disponibles

    if not folios:
        msg = f"⚠️ ¿Qué folio(s) quieres {accion}? Dime el número."
        message_log.log_message("out", phone, "text", msg, {"control_error": True})
        return {"error": "sin folios"}

    op = {"aceptar": aceptar_folio,
          "cancelar": cancelar_folio,
          "reactivar": reactivar_folio}[accion]
    titulo = {"aceptar": "✅ Folios aceptados para facturar",
              "cancelar": "❌ Folios cancelados",
              "reactivar": "🔄 Folios reactivados"}[accion]

    # Si no nos dieron fecha explícita, búsqueda multi-día: probar más reciente
    # y si el folio no existe, escanear los demás días.
    fechas_a_probar: list[str | None]
    if fecha_iso:
        fechas_a_probar = [fecha_iso]
    else:
        fechas_a_probar = [None] + [d for d in listar_fechas_disponibles()]

    resultados = []
    lines = [f"*{titulo}:*"]
    for f in folios:
        r = None
        for fi in fechas_a_probar:
            r_try = op(f, fecha_iso=fi)
            if r_try.get("ok"):
                r = r_try
                break
            r = r_try  # guardar último error como fallback
        resultados.append(r)
        if r.get("ok"):
            ic = ESTADO_ICON.get(r["nuevo"], "?")
            destino = r.get("hospital") or r.get("destino") or "?"
            try:
                folio_int = int(r.get("folio") or 0)
            except (TypeError, ValueError):
                folio_int = "?"
            lines.append(f"  {ic} folio {folio_int} — {destino}: "
                         f"{r['anterior']} → {r['nuevo']}")
        else:
            lines.append(f"  ⚠️ {f}: {r.get('error', '?')}")

    msg = "\n".join(lines)
    meta = {"control": True, "accion": accion, "resultados": resultados}
    message_log.log_message("out", phone, "text", msg, meta)
    return {"control": True, "accion": accion, "resultados": resultados}


def _reporte_control(phone: str, fecha_iso: str | None = None) -> dict:
    """Devuelve el reporte de control de documentos por estado.

    Si fecha_iso se pasa, opera sobre ESE día. Sino, sobre el más reciente.
    """
    from .control_documentos import reporte_estados, ESTADO_ICON

    rep = reporte_estados(fecha_iso=fecha_iso)
    if rep.get("error"):
        msg = f"⚠️ {rep['error']}"
        message_log.log_message("out", phone, "text", msg, {"control_error": True})
        return {"error": rep["error"]}

    fecha = rep.get("fecha_legible", rep.get("fecha"))
    grupos = rep["grupos"]
    lines = [f"📋 *Control de documentos — {fecha}*"]
    orden = ["aceptado", "modificado", "vigente", "cancelado"]
    for est in orden:
        items = grupos.get(est) or []
        if not items:
            continue
        lines.append("")
        lines.append(f"{ESTADO_ICON.get(est, '?')} *{est.upper()}* ({len(items)})")
        for it in sorted(items, key=lambda x: x.get("folio") or ""):
            try:
                fnum = int(it.get("folio") or 0)
            except (TypeError, ValueError):
                fnum = "?"
            lines.append(f"  • folio {fnum} — {it['destino']}")
    # Resumen
    lines.append("")
    listos = len(grupos.get("aceptado") or [])
    pendientes = len(grupos.get("vigente") or []) + len(grupos.get("modificado") or [])
    cancelados = len(grupos.get("cancelado") or [])
    lines.append(f"📊 Listos para facturar: {listos} · Pendientes: {pendientes} · Cancelados: {cancelados}")

    msg = "\n".join(lines)
    meta = {"reporte_control": True, "grupos": {k: len(v) for k, v in grupos.items()}}
    message_log.log_message("out", phone, "text", msg, meta)
    return {"reporte_control": True, "grupos": grupos}


def _imprimir_nota_folio(phone: str, folio_input: str | None,
                          fecha_iso: str | None = None) -> dict:
    """Genera el PDF de UNA nota específica por su folio.

    Si el folio es de un hospital → regenera nota corregida individual.
    Si el folio es del ALMACÉN EHMO (extras) → regenera la nota EXTRAS.
    """
    from .estado_pedido import cargar_estado, cargar_estado_mas_reciente, estado_a_dataframe
    from .extras_pedido import cargar_extras, regenerar_archivos_extras
    from .nota_remision import generar_notas_remision
    from datetime import datetime
    from . import config

    if not folio_input:
        msg = "⚠️ No me dijiste qué folio. Dime el número (ej. 'imprime la nota 13')."
        message_log.log_message("out", phone, "text", msg, {"imprimir_folio": False})
        return {"error": "sin folio"}

    # Normalizar folio: '13' → '0000000013'
    try:
        folio_int = int(str(folio_input).lstrip("0") or "0")
        folio_str = f"{folio_int:010d}"
    except (TypeError, ValueError):
        msg = f"⚠️ Folio '{folio_input}' no es un número válido."
        message_log.log_message("out", phone, "text", msg, {"imprimir_folio": False})
        return {"error": "folio inválido"}

    if fecha_iso:
        state = cargar_estado(fecha_iso)
    else:
        state, fecha_iso = cargar_estado_mas_reciente()
    if not state:
        msg = "⚠️ No hay pedido del día procesado."
        message_log.log_message("out", phone, "text", msg, {"imprimir_folio": False})
        return {"error": "sin pedido"}

    fecha_legible = state.get("fecha_legible", fecha_iso)
    ts = datetime.now().strftime("%H%M%S")

    # 1) Buscar entre hospitales del pedido normal
    hospital_match = None
    for h, info in state["hospitales"].items():
        if info.get("folio_remision") == folio_str:
            hospital_match = (h, info)
            break

    if hospital_match:
        hospital_resuelto, info = hospital_match
        if info.get("total", 0) <= 0:
            msg = f"⚠️ El folio {folio_int} ({hospital_resuelto}) no tiene productos vigentes."
            message_log.log_message("out", phone, "text", msg, {"imprimir_folio": False})
            return {"error": "sin productos"}

        df = estado_a_dataframe(state)
        df_solo = df[df["UNIDAD"] == hospital_resuelto]
        h_short = hospital_resuelto.replace("Hospital ", "").replace(
            "Básico Comunitario ", "HBC ")[:40].strip()
        output_path = (config.PROCESSED_DIR /
                       f"Nota Folio {folio_int} {h_short} {fecha_legible} {ts}.pdf")
        folios_existentes = {hospital_resuelto: folio_str}
        try:
            info_gen = generar_notas_remision(df_solo, fecha_legible, output_path,
                                               folios_existentes=folios_existentes)
            d = drive_upload(output_path, subfolder=fecha_iso)
            link = d["link"] if d else None
            msg = (f"🖨️ *Nota folio {folio_int}* — {hospital_resuelto}\n"
                   f"{('PDF: ' + link) if link else 'Generada local (no se pudo subir a Drive)'}")
            message_log.log_message("out", phone, "text", msg,
                                     {"imprimir_folio": True, "folio": folio_str,
                                      "hospital": hospital_resuelto, "drive_link": link})
            return {"folio": folio_str, "hospital": hospital_resuelto, "drive_link": link}
        except Exception as e:
            log.exception(f"Error imprimiendo folio {folio_str}: {e}")
            msg = f"⚠️ Error generando la nota: {e}"
            message_log.log_message("out", phone, "text", msg, {"imprimir_folio": False})
            return {"error": str(e)}

    # 2) Buscar entre folios de extras
    extras_state = cargar_extras(fecha_iso)
    if extras_state:
        folios_destinos = extras_state.get("folios_por_destino") or {}
        for destino, f in folios_destinos.items():
            if f == folio_str:
                # Regenerar la nota EXTRAS (es una sola con todos los items)
                regen = regenerar_archivos_extras(fecha_iso)
                link = (regen.get("drive_notas") or {}).get("link") if not regen.get("error") else None
                msg = (f"🖨️ *Nota folio {folio_int}* — {destino} (EXTRAS)\n"
                       f"{('PDF: ' + link) if link else 'Generada local (no se pudo subir a Drive)'}")
                message_log.log_message("out", phone, "text", msg,
                                         {"imprimir_folio": True, "folio": folio_str,
                                          "destino": destino, "drive_link": link})
                return {"folio": folio_str, "destino": destino, "drive_link": link}

    # No se encontró
    folios_validos = []
    for h, info in state["hospitales"].items():
        f = info.get("folio_remision")
        if f and info.get("total", 0) > 0:
            folios_validos.append(f"{int(f)} → {h}")
    if extras_state:
        for d, f in (extras_state.get("folios_por_destino") or {}).items():
            folios_validos.append(f"{int(f)} → {d} (EXTRA)")
    msg = (f"⚠️ No encontré el folio {folio_int}. Folios vigentes hoy:\n" +
           "\n".join(f"  • {fv}" for fv in sorted(folios_validos)))
    message_log.log_message("out", phone, "text", msg, {"imprimir_folio": False})
    return {"error": f"folio {folio_int} no existe"}


def _recargar_precios(phone: str) -> dict:
    """Refresca la lista de precios desde el Excel sin reiniciar Flask."""
    from .pricing import cargar_lista_precios
    cargar_lista_precios.cache_clear()
    items = cargar_lista_precios()
    msg = f"🔄 Lista de precios recargada — {len(items)} productos activos."
    message_log.log_message("out", phone, "text", msg, {"reload_prices": True, "count": len(items)})
    return {"reload_prices": True, "count": len(items)}


def _consolidar_notas(phone: str, fecha_iso: str | None = None) -> dict:
    """Genera un PDF único con TODAS las notas vigentes (estado actual).

    Si fecha_iso se pasa, opera sobre ESE día. Sino, sobre el más reciente.
    Útil para imprimir todas las notas finales después de modificaciones/ajustes.
    """
    from .estado_pedido import cargar_estado, cargar_estado_mas_reciente, estado_a_dataframe
    from .nota_remision import generar_notas_remision
    from datetime import datetime
    from . import config

    if fecha_iso:
        state = cargar_estado(fecha_iso)
    else:
        state, fecha_iso = cargar_estado_mas_reciente()
    if not state:
        msg = "⚠️ No hay pedido del día procesado, no puedo consolidar notas."
        message_log.log_message("out", phone, "text", msg, {"consolidar": False})
        return {"error": "no hay pedido"}

    df = estado_a_dataframe(state)
    if df.empty:
        msg = "⚠️ El estado del día no tiene productos vigentes."
        message_log.log_message("out", phone, "text", msg, {"consolidar": False})
        return {"error": "estado vacío"}

    fecha_legible = state.get("fecha_legible", fecha_iso)
    ts = datetime.now().strftime("%H%M%S")
    output_path = config.PROCESSED_DIR / f"Notas Remisión CONSOLIDADAS {fecha_legible} {ts}.pdf"

    folios_existentes = {h: hi.get("folio_remision")
                          for h, hi in state["hospitales"].items()
                          if hi.get("folio_remision")}

    drive_link = None
    notas_count = 0
    try:
        info = generar_notas_remision(df, fecha_legible, output_path,
                                       folios_existentes=folios_existentes)
        notas_count = info.get("hospitales", 0)
        drive_info = drive_upload(output_path, subfolder=fecha_iso)
        if drive_info:
            drive_link = drive_info["link"]
    except Exception as e:
        log.exception(f"Error consolidando notas: {e}")
        log_event("processor", f"⚠️ Error consolidando notas: {e}", level="warn")
        msg = f"⚠️ Error al consolidar: {e}"
        message_log.log_message("out", phone, "text", msg, {"consolidar": False})
        return {"error": str(e)}

    lines = [
        f"📑 *Notas de remisión consolidadas* — {fecha_legible}",
        f"Total de notas: {notas_count} hospitales",
    ]
    if drive_link:
        lines.append(f"🖨️ PDF para imprimir todas juntas: {drive_link}")
    else:
        lines.append("⚠️ Guardado local (no se pudo subir a Drive)")

    msg = "\n".join(lines)
    meta = {"consolidar": True, "notas_count": notas_count}
    if drive_link:
        meta["drive_link"] = drive_link
    message_log.log_message("out", phone, "text", msg, meta)
    return {"consolidar": True, "drive_link": drive_link, "notas_count": notas_count}
