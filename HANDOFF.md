# Handoff — Frutas Kelly WhatsApp Agent
**Última actualización:** 2026-04-29
**Propósito:** continuar el trabajo en otra cuenta de Claude. Pega todo este archivo como primer mensaje en la nueva conversación.

---

## 1. Contexto del proyecto

**Quién:** Cristian Zarate (RFC `ZAOC830517RF9`), distribuidor del **Lote 5: Frutas y Verduras** para hospitales del sistema de salud de Chiapas, México.

**Cliente principal:** **EHMO** (`GRUPO OPERADOR DE ALIMENTOS EHMO`, RFC `GOA180712SF5`) — manda diariamente un Excel BD con el pedido del día siguiente.

**Cliente secundario:** **SUREÑA** / Comedores humanitarios (6 destinos: Patria, CCI, 6 de Junio, Shanka, Jobo, Copoya). Pedido en libreta/foto, no Excel.

**Stack del agente:**
- Python 3.11 + Flask (gunicorn en producción)
- Claude API (modelo `claude-sonnet-4-6`) para interpretar mensajes
- Google Drive (subida automática de PDFs/xlsx)
- WhatsApp Business API (Meta)
- Hosted en Render (auto-deploy desde `main`)
- Repo: https://github.com/frutaskelly/Whatsapp_agent

**Working dir local:** `C:\Users\crist\OneDrive\Documentos\Claude\Projects\pedidos chiapas\whatsapp_agent`

---

## 2. Reglas de negocio (críticas)

### Hospitales

**6 EXCLUIDOS (NO se surten, JAMÁS):**
1. Hospital General de Pichucalco
2. Hospital General de Palenque
3. Hospital Básico Comunitario de Tila
4. Hospital General de Reforma
5. Hospital General Yajalón Dr. Jose Manuel Velasco Siles
6. Hospital Básico Comunitario de Amatán

Hardcoded en `EXCLUIDOS_KW` ([pedido_processor.py:30](app/pedido_processor.py#L30)).

**20 hospitales conocidos que SÍ surten:** ver `HOSPITALES_CONOCIDOS_SI` en [pedido_processor.py:62](app/pedido_processor.py#L62).

**6 Comedores:** `COMEDORES_SI` en [pedido_processor.py:92](app/pedido_processor.py#L92).

### Lotes

- **Lote 5 = Frutas y Verduras** = lo que surte Frutas Kelly.
- **Lote 1 = Abarrotes** = en general NO, EXCEPTO los productos en `CAMBIO_KW` (productos FyV que EHMO etiqueta mal como abarrote).
- El BD a veces trae lotes **sin prefijo numérico** (`"FRUTAS Y VERDURAS"` en lugar de `"5 FRUTAS Y VERDURAS"`). El sistema acepta ambas variantes (`_es_lote_5`, `_es_lote_1` en pedido_processor).
- Detección de productos del cambio: escanea **cualquier lote que NO sea 5** (incluye lote vacío, "EXTRA ABARROTES", etc.) y rescata los que matcheen `CAMBIO_KW`.

### CAMBIO_KW (productos FyV mal etiquetados)
ajo en bulbo, ajonjolí, cacahuate tostado sin sal, canela en raja, chile seco (ancho/guajillo/pasilla), epazote, flor de jamaica, nuez sin cáscara, orégano en hoja, perejil, té de limón / manzanilla / yerbabuena, palanqueta de cacahuate, mermelada de fresa, polvo para hornear.

### IGNORAR_KW (NUNCA surtir aunque venga como Lote 5)
almendra tostada, salchicha.

### Lista de precios
- Archivo: `Lista_Precios_EHMO 26_04_27.xlsx` — vive en el directorio **padre** (`../Lista_Precios_EHMO 26_04_27.xlsx`), **NO está en el repo**.
- Para recargar sin reiniciar: `GET /api/reload-prices`.
- ⚠️ **En Render no existe esta lista** porque vive fuera del repo. Operación real funciona localmente con simulator. Si se quisiera Render con precios, hay que mover el xlsx al repo y actualizar `LISTA_PRECIOS_PATH`.

### Cadencia
**Pedidos diarios** (no semanales). Cada día llega un Excel BD distinto.

### Semana del cliente
EHMO numera con offset -1 respecto al ISO. Lunes 27/abr/2026 = ISO 18 = **EHMO 17**. Constante `SEMANA_OFFSET_EHMO = -1` en [nota_remision.py:146](app/nota_remision.py#L146).

---

## 3. Estado operativo actual

Estados JSON en `storage/pedidos_dia/<fecha-iso>.json` y extras en `storage/extras_dia/`. Ambos directorios están **gitignored**.

| Día | Regular | Extras | Total | Notas |
|---|---|---|---|---|
| 27 abr | $170,672.39 | $14,434.20 | **$185,106.59** | 12 hospitales (folios 14–25) + ALMACÉN EHMO (folio 13). Reconstruido desde BD original con `rebuild_estado.py`, replayados los faltantes (Mamey, Guanábana, Orégano, Nuez) y agregados 11 extras. Mermelada en este día: 7 cajas a $800. |
| 28 abr | $136,866.29 | $1,819.50 | **$138,685.79** | 12 hospitales (folios 26-37). Mermelada: 6 cajas a $800 (Bicentenario Villaflores). |
| 29 abr | $107,486.12 | — | **$107,486.12** | Mermelada: 10 cajas a $800 (Gandulfo Comitán). |
| 30 abr | $48,379.08 | — | **$48,379.08** | 9 hospitales. Sin mermelada. |

**Mermelada estado final:** todos los días a $800/caja. Lista de precios en $800 (revertido desde $850 a pedido del usuario).

**Folio counter:** [storage/folio_counter.json](storage/folio_counter.json) — se mantiene secuencial automáticamente.

---

## 4. Archivos clave

### Código (en `app/`)
| Archivo | Rol |
|---|---|
| `main.py` | Entry point Flask, endpoints (/webhook, /api/*) |
| `webhook.py` | Endpoints WhatsApp (verify + recepción) |
| `ai_agent.py` | Llama a Claude, inyecta contexto del día (con resolución de fecha del mensaje) |
| `processing_runner.py` | Routing de acciones del agente (modificación, ajuste, extra, consolidar, relación, folio control) |
| `pedido_processor.py` | Procesa Excel BD, aplica reglas de negocio, genera todos los outputs |
| `pedido_pdf.py` | PDF imprimible por hospital (sin precios) |
| `lista_compras_pdf.py` | Lista consolidada PDF + Excel (sin precios) |
| `nota_remision.py` | Notas con precios (formato fiscal, folios) |
| `relacion_documentos.py` | Excel + PDF horizontal con todos los folios del día |
| `pricing.py` | Cargar y matchear precios contra lista xlsx |
| `estado_pedido.py` | JSON state per día, helpers `cargar_estado`, `resolver_fecha_iso`, `listar_fechas_disponibles` |
| `extras_pedido.py` | Estado y operaciones para extras (ALMACÉN EHMO) |
| `modificacion_pedido.py` | Aplicar modificaciones pre-surtido a JSON state |
| `ajuste_entrega.py` | Aplicar ajustes post-surtido (faltantes) |
| `control_documentos.py` | Aceptar/cancelar/reactivar folios |
| `display_names.py` | Correcciones display: typos (mango atufo→ataulfo) y override presentaciones |
| `rebuild_estado.py` | Reconstruir JSON state desde BD original (para días sin snapshot) |

### Config / data
- `storage/keywords.json` — extensiones de CAMBIO_KW, IGNORAR_KW, EXCLUIDOS_KW, presentaciones_override (sin tocar código). Recargable via `/api/reload-keywords`.
- `storage/folio_counter.json` y `folio_counter_comedores.json` — contadores de folios.
- `secrets/google-drive-token.json` y `google-oauth-credentials.json` — auth Drive.
- `.env` — tokens WhatsApp + Anthropic + EHMO_PHONE.

---

## 5. Cambios hechos en esta sesión (cronológico)

### 5.1 Display names — typos y casing
- **Helper nuevo:** [app/display_names.py](app/display_names.py)
- `corregir_nombre("mango atufo") → "mango ataulfo"` (preserva casing)
- `formatear_presentacion("KILO") → "Kilo"` (Title Case para notas remisión)
- Aplicado en `pedido_pdf.py`, `lista_compras_pdf.py`, `nota_remision.py`

### 5.2 Soporte multi-día por WhatsApp
- `resolver_fecha_iso(text)` en estado_pedido.py — detecta "hoy", "ayer", "antier", "del 27", "del día 28", "27 de abril", "28/04", "2026-04-28"
- `listar_fechas_disponibles()` — lista de días con JSON guardado
- `chat()` en `ai_agent.py` ahora pre-resuelve la fecha del mensaje y carga ESE estado en lugar del más reciente
- SYSTEM_PROMPT instruye a Claude a poner `datos.fecha_iso` para acciones específicas
- `_aplicar_modificaciones`, `_aplicar_ajustes`, `_consolidar_notas`, `_generar_relacion_handler`, `_imprimir_nota_folio`, `_reporte_control` ahora aceptan `fecha_iso`
- `_control_estado` (aceptar/cancelar/reactivar): si no hay fecha, escanea TODOS los días buscando el folio (UX: "acepta el folio 14" sin recordar día)

### 5.3 Reconstrucción histórica (Paso 2)
- **Nuevo:** [app/rebuild_estado.py](app/rebuild_estado.py)
- Reconstruye JSON state de un día desde BD original + Relación Documentos (para folios)
- Acepta flag `--excluir-cambio` para días anteriores a la incorporación de un producto a CAMBIO_KW
- Usado para reconstruir el 27-abr (snapshot que no existía)
- Script one-shot: `replay_27.py` (replayó faltantes + extras del 27 con `solo_estado=True`)

### 5.4 Bugs del procesador
- **7 columnas:** EHMO agregó columna `PESO` al BD del 30-abr → procesador crasheaba con `Length mismatch`. Fix: si BD trae >6 columnas, cortar a las primeras 6.
- **Lote sin prefijo:** filas con `"FRUTAS Y VERDURAS"` (sin `5 `) o `"ABARROTES"` (sin `1 `) se perdían. Helpers `_es_lote_5`/`_es_lote_1` aceptan ambas variantes.
- **Cambio en cualquier lote:** detección de productos CAMBIO_KW ahora escanea TODAS las filas que no sean Lote 5 (incluye lote vacío, "EXTRA ABARROTES", etc.).

### 5.5 Keywords extensibles
- [storage/keywords.json](storage/keywords.json) permite agregar entradas extra a:
  - `cambio_kw` — productos FyV en lotes equivocados
  - `ignorar_kw` — productos que nunca van en FyV
  - `excluidos_kw` — hospitales adicionales a excluir
  - `presentaciones_override` — unidades de medida estándar por nombre
- Las listas en código siguen vivas como base; el JSON SUMA, no reemplaza.
- Endpoint `/api/reload-keywords` limpia el cache sin reiniciar Flask.

### 5.6 Override de presentaciones
- Cuando EHMO manda presentaciones inconsistentes (`"EMMPAQUE DE 454 g"`, `"CAJA C/250 PIEZAS DE 20 g"`, `"PIEZA DE 30 g"`), el sistema las normaliza.
- Defaults hardcoded en `_PRESENTACIONES_OVERRIDE`:
  - mermelada → **CAJA**
  - polvo para hornear → **PZ**
  - palanqueta → **PAQUETE**
  - orégano → **PAQUETE**
- Aplicado al ingestar BD (`pedido_processor`).
- Backfilleado en JSONs existentes (27, 28, 29).

### 5.7 Mermelada de fresa — caso particular
- Lista de precios tiene "Mermelada de fresa individual 20 g caja 250 pz" a $800.
- En la nota original del 27 se cobró a $378 (un producto diferente, "120 piezas").
- El usuario corrigió: la presentación correcta es **250 piezas a $800/caja**.
- Estado final unificado: 27 EXTRA, 28 REGULAR (Bicentenario), 29 REGULAR (Gandulfo) — todos a $800/caja.

### 5.8 Fix overflow PDF Relación
- Las celdas eran strings planos → ReportLab no wrappea → texto desbordaba a columnas vecinas.
- Fix: HOSPITAL, SEMANA, FECHA, ESTATUS, OBSERVACIONES ahora son `Paragraph` con word-wrap por palabras (no CJK).
- Anchos de columna ajustados para que "Modificado" entre en una línea.

---

## 6. Endpoints útiles

| Endpoint | Método | Uso |
|---|---|---|
| `/health` | GET | Health check |
| `/webhook` | GET/POST | Meta webhook (verify + mensajes) |
| `/api/status` | GET | Estado del sistema |
| `/api/messages` | GET | Log de mensajes |
| `/api/events` | GET | Log de eventos |
| `/api/simulate` | POST | Simulador local (envía mensaje al agente sin pasar por Meta) |
| `/api/consolidar-notas` | POST/GET | Consolidar notas vigentes |
| `/api/reload-prices` | POST/GET | Recargar lista de precios |
| `/api/reload-keywords` | POST/GET | Recargar storage/keywords.json |
| `/api/relacion` | GET | Relación de documentos (día o semana) |

---

## 7. Comandos por WhatsApp (intenciones que el agente entiende)

- **Pedido nuevo:** mandar Excel adjunto → genera todo (Excel procesado + PDFs + notas + relación)
- **Modificación pre-surtido:** "agrégale 5kg de jitomate al pedido de Comitán"
- **Ajuste post-surtido:** "no se surtió papa en Tapachula 2kg" / "faltó 5kg de mamey en Mujer Comitán"
- **Extra desabasto:** "manda 50kg de chile ancho extra para Comitán" o "extra al almacén de EHMO: 39kg frijol bayo"
- **Consultas:** "qué pidió Comitán hoy", "qué hospitales pidieron mamey ayer", "del día 28 cuáles llevan mermelada"
- **Multi-día:** "del 27", "ayer", "antier", "del día 28 de abril", "28/04", "2026-04-28"
- **Control de folios:** "acepta el folio 14", "cancela el folio 17", "reactiva el 14"
- **Reportes:** "imprime la relación", "consolida las notas", "imprime la nota 14", "control de documentos"
- **Mantenimiento:** "recarga los precios"

---

## 8. Pendientes / consideraciones

### Resueltas en esta sesión pero merecen recordatorio
- ✅ **Lista de precios fuera del repo:** local solo. Si necesitas que Render la use, hay que moverla al repo y actualizar `LISTA_PRECIOS_PATH`.
- ✅ **Estados JSON gitignored:** datos operativos del día a día. No se commitean. Se persisten en disco local.

### Bajos prioridad / nice-to-have
- `_imprimir_nota_folio` no tiene búsqueda multi-día. Si pides "imprime la nota 14" y estás operando con el 30 cargado, fallaría (folio 14 es del 27). Workaround: "imprime la nota 14 del 27".
- `_aplicar_extras_desde_ai` no acepta `fecha_iso` propagado (los extras siempre van al día más reciente o al de hoy).
- En Render free tier el filesystem es efímero: editar `storage/keywords.json` en vivo no persiste. Hay que commitearlo y hacer push.

### Operativo (no código)
- Si EHMO empieza a mandar productos FyV nuevos en lotes incorrectos: agregar al dict `CAMBIO_KW` en código, o más fácil al `cambio_kw` de `storage/keywords.json`.
- Si aparece un nuevo producto con presentación rara: agregar a `_PRESENTACIONES_OVERRIDE` (código) o `presentaciones_override` (json).
- El usuario tiene un v2 que descartó (probó copiar todo a `whatsapp_agent_v2/` pero canceló para seguir con v1 estable).

---

## 9. Memoria persistente del usuario (para contexto)

Stored en `~/.claude/projects/.../memory/MEMORY.md`. Resumen de lo que el sistema "sabe" del usuario:

- Universo de hospitales EHMO: 20 que SÍ + 6 que NO
- 6 Comedores como cliente independiente (Patria, CCI, 6 de Junio, Shanka, Jobo, Copoya)
- Cadencia diaria de pedidos (no semanal)
- Lista de precios fija (`Lista_Precios_EHMO 26_04_27.xlsx`)

Estos hechos NO necesitas duplicarlos en la nueva conversación; ya están detallados arriba en este doc.

---

## 10. Cómo continuar en otra cuenta de Claude

1. Abre Claude Code en la nueva máquina/cuenta dentro de la misma carpeta del proyecto:
   ```
   cd "C:\Users\crist\OneDrive\Documentos\Claude\Projects\pedidos chiapas\whatsapp_agent"
   claude
   ```
2. **Primer mensaje** sugerido:
   > Pega el contenido de `HANDOFF.md`. Dile algo como:
   > "Lee este archivo de handoff de la sesión anterior y dime que estás listo para continuar. La última operación dejó el sistema con 4 días de pedidos (27, 28, 29, 30 de abril 2026) y orégano agregado al override de presentaciones."

3. Confirma que la nueva instancia de Claude pueda:
   - Ver el repo (`git status` debe decir `clean` o lo que sea consistente)
   - Leer `storage/pedidos_dia/*.json` y `storage/extras_dia/*.json`
   - Ver `Lista_Precios_EHMO 26_04_27.xlsx` en `..` (parent dir)

4. Render auto-deploya desde `main`. El último commit es `cc23444` (orégano → PAQUETE).

---

## 11. Últimos commits del repo

```
cc23444 Override de presentación: orégano → PAQUETE
9d6828c Fix overflow de columnas en PDF de Relación de Documentos + presentaciones extensibles
eae0f7a Override de presentaciones por nombre de alimento
ea899da Resolver fecha también acepta 'del día N' / 'el día N' / 'día N'
3855175 Aceptar/cancelar/reactivar folios cross-día (búsqueda multi-fecha)
dfe20d1 Propagar fecha_iso al handler de reporte_control
be16cf0 Propagar fecha_iso a handlers de consulta (relación, notas, folio)
01a2a73 Keywords extensibles desde storage/keywords.json sin tocar código
8165243 Detectar productos del cambio en cualquier lote (vacío o equivocado)
c146d0f Tolerar lote sin prefijo numérico en BD
2d53312 Soporte multi-día, correcciones de display y fix BD con columnas extra
ffe6f34 Operación FyV diaria: pipeline completo + control de documentos (PRE-sesión)
```

---

**FIN DEL HANDOFF.** Si algo falta, búscalo en el log de eventos (`storage/event_log.jsonl`) o en el git log con `git log --all --oneline`.
