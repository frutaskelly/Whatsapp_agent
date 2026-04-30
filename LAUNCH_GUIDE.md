# Launch Guide — Frutas Kelly WhatsApp Agent

> 📥 **¿Primera vez configurando el sistema?** Empieza por **[INSTALL.md](INSTALL.md)** que tiene
> el quickstart copy-paste y troubleshooting de la instalación. Este doc cubre
> deploy a Render, mantenimiento operativo y rotación de tokens.

---

> 🔐 **CREDENCIALES ENCRIPTADAS**
> Los secrets sensibles (`.env`, `secrets/*.json`) viven en el repo
> **encriptados** con Fernet (AES-128 + HMAC). Lo crudo está gitignored;
> solo viajan los `.enc`. La llave para desencriptarlos vive FUERA del repo:
> `../.frutaskelly_secrets.key` (relativo a `whatsapp_agent/`).
>
> Aún así el repo es **PRIVADO** — no lo conviertas en público bajo ninguna
> circunstancia.

---

## ✅ Qué incluye el repo

| Carpeta / Archivo | Qué es | Status |
|---|---|---|
| `app/` | Código fuente Python | git tracked |
| `.env.enc` | Credenciales (WhatsApp, Anthropic, EHMO_PHONE, Drive) **encriptadas** | **commiteado** |
| `secrets/google-oauth-credentials.json.enc` | OAuth Client ID **encriptado** | **commiteado** |
| `secrets/google-drive-token.json.enc` | Token OAuth **encriptado** | **commiteado** |
| `tools/secrets_crypto.py` | Script para encriptar/desencriptar | **commiteado** |
| `data/Lista_Precios_EHMO.xlsx` | Lista de precios EHMO | **commiteado** |
| `storage/keywords.json` | Override de cambio_kw / ignorar_kw / presentaciones | **commiteado** |
| `storage/folio_counter.json` | Folio secuencial EHMO actual | **commiteado** |
| `storage/folio_counter_comedores.json` | Folio secuencial Comedores | **commiteado** |
| `storage/pedidos_dia/*.json` | Estados operativos por día (27, 28, 29, 30 abr 2026) | **commiteado** |
| `storage/extras_dia/*.json` | Extras al ALMACÉN EHMO por día | **commiteado** |
| `requirements.txt` / `render.yaml` | Dependencias y config Render | git tracked |

**No incluye** (gitignored, regenerable o local):
- `venv/`, `__pycache__/`, IDE configs
- `storage/inbox/*` (Excels que llegan por WhatsApp)
- `storage/conversations/*` (history del agente)
- `storage/processed/*` (PDFs/xlsx generados)
- `storage/event_log.jsonl`, `storage/message_log.jsonl` (logs)
- `backups/`, `dist/`, `build/`

---

## 🚀 Setup desde cero (máquina nueva)

### 0. Prerequisitos
- Python 3.11+
- Git
- Cuenta de GitHub con acceso al repo privado

### 1. Clonar el repo

```bash
git clone https://github.com/frutaskelly/Whatsapp_agent.git
cd Whatsapp_agent
```

### 2. Crear el entorno virtual e instalar dependencias

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

### 2.5. Desencriptar los secrets

Necesitas la **llave** para desencriptar `.env` y `secrets/*.json`. Hay tres formas
de proveer la llave:

**A.** Copia el archivo de llave a la ruta default `../.frutaskelly_secrets.key`
(un nivel arriba del repo):
```bash
# Si lo tienes en OneDrive / USB / password manager, copia a:
cp /ruta/a/.frutaskelly_secrets.key ../.frutaskelly_secrets.key
```

**B.** Exporta la variable de entorno:
```bash
export FRUTASKELLY_KEY_PATH=/ruta/a/.frutaskelly_secrets.key   # Mac/Linux
# Windows PowerShell:
$env:FRUTASKELLY_KEY_PATH = "C:\ruta\a\.frutaskelly_secrets.key"
```

**C.** Pásala como argumento:
```bash
python tools/secrets_crypto.py decrypt --key /ruta/a/.frutaskelly_secrets.key
```

Luego desencripta:
```bash
python tools/secrets_crypto.py decrypt
# Output:
#   ✓ desencriptado: .env.enc → .env (xxxx bytes)
#   ✓ desencriptado: secrets/google-drive-token.json.enc → secrets/google-drive-token.json (xxxx bytes)
#   ✓ desencriptado: secrets/google-oauth-credentials.json.enc → secrets/google-oauth-credentials.json (xxxx bytes)
```

> 🔑 ¿No tienes la llave? La generaste la primera vez con
> `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
> y la guardaste en OneDrive / gestor de contraseñas / USB. Si la perdiste,
> los `.enc` del repo son irrecuperables — tendrías que regenerar todas las
> credenciales (rotar tokens en Meta, Anthropic, Google).

### 3. Validar que la lista de precios y credenciales se cargan

```bash
python -c "from app.pricing import cargar_lista_precios; print(len(cargar_lista_precios()), 'productos')"
# Debe imprimir: 106 productos
```

### 4. Correr local

```bash
python -m app.main
# Abre http://localhost:5000/health → debe responder {"status":"ok"}
```

### 5. (Opcional) Exponer a internet con ngrok

```bash
ngrok http 5000
# Toma la URL pública y configúrala en Meta:
# Meta Developer Console → tu app (1475318460751526)
#   → WhatsApp → Configuration → Webhook URL: https://<ngrok>.ngrok-free.app/webhook
#   → Verify token: el de WHATSAPP_VERIFY_TOKEN en .env
#   → Suscribir al evento "messages"
```

---

## 🌐 Deploy a Render

Render auto-deploya desde la rama `main` de GitHub. Setup inicial:

### A. Conectar el repo

1. Ir a https://dashboard.render.com → **New** → **Web Service**
2. Connect a GitHub → seleccionar el repo privado `frutaskelly/Whatsapp_agent`
3. Render lee `render.yaml` automáticamente (usa Python 3.11.10, gunicorn)

### B. Configurar variables de entorno

Aunque `.env` está en el repo, **Render NO lo lee** — usa solo las variables del dashboard. Pega estas en Settings → Environment:

```
WHATSAPP_APP_ID            (del .env)
WHATSAPP_BUSINESS_ID       (del .env)
WHATSAPP_PHONE_NUMBER_ID   (del .env)
WHATSAPP_BUSINESS_ACCOUNT_ID  (del .env)
WHATSAPP_ACCESS_TOKEN      (del .env — RENOVAR cada 24h si es temporal)
WHATSAPP_VERIFY_TOKEN      (del .env)
WHATSAPP_APP_SECRET        (del .env)
ANTHROPIC_API_KEY          (del .env)
CLAUDE_MODEL               claude-sonnet-4-6
EHMO_PHONE                 (del .env)
LISTA_PRECIOS_PATH         data/Lista_Precios_EHMO.xlsx
GOOGLE_DRIVE_FOLDER_ID     (del .env)
GOOGLE_OAUTH_CREDENTIALS   secrets/google-oauth-credentials.json
GOOGLE_OAUTH_TOKEN         secrets/google-drive-token.json
```

> 💡 Tip: copiar las líneas del `.env` y pegarlas todas juntas en el bulk editor de Render.

### C. Configurar webhook en Meta

Cuando Render termine el primer deploy:
1. Copiar la URL de Render (ej. `https://frutaskelly-xxx.onrender.com`)
2. Meta Developer Console → tu app → WhatsApp → Configuration:
   - Webhook URL: `https://frutaskelly-xxx.onrender.com/webhook`
   - Verify token: el que tengas en `WHATSAPP_VERIFY_TOKEN`
   - Suscribir al evento `messages`

### D. Verificar

- `GET https://frutaskelly-xxx.onrender.com/health` → `{"status":"ok"}`
- Mandar mensaje de WhatsApp desde el número EHMO_PHONE → debe responder

---

## 🔄 Cuando cambias un secret

### Token de WhatsApp expira (cada 24h si es temporal)
1. Meta Developer Console → tu app → WhatsApp → API Setup → Generate token
2. Actualizar en `.env` local
3. **Re-encriptar** `.env`:
   ```bash
   python tools/secrets_crypto.py encrypt
   git add .env.enc && git commit -m "Renovar token WhatsApp" && git push
   ```
4. Actualizar también el env var `WHATSAPP_ACCESS_TOKEN` en Render dashboard
5. (Local) Reiniciar `python -m app.main`

### Renovar token de Google Drive
```bash
python -m app.drive_setup
# Sigue el flujo OAuth en el navegador
# Genera nuevo secrets/google-drive-token.json
python tools/secrets_crypto.py encrypt
git add secrets/google-drive-token.json.enc
git commit -m "Renovar token Drive"
git push
```

### Actualizar lista de precios
1. Editar `data/Lista_Precios_EHMO.xlsx`
2. (Local) `GET http://localhost:5000/api/reload-prices` o por WhatsApp: "recarga los precios"
3. (Producción) `git push` → Render redeploya con la nueva lista

### Agregar productos al cambio de lote o presentaciones
1. Editar `storage/keywords.json`
2. (Local) `GET http://localhost:5000/api/reload-keywords`
3. (Producción) `git push` → redeploy

---

## 📦 Archivos críticos a respaldar (fuera del repo)

Si por algún motivo pierdes acceso a GitHub, estos son los archivos que necesitas para arrancar:

1. **Tokens de WhatsApp Business API** — solo viven en `.env` y en Meta Developer Console
2. **Anthropic API key** — solo en `.env` y en https://console.anthropic.com
3. **Google OAuth client ID** — solo en `secrets/google-oauth-credentials.json` y en Google Cloud Console
4. **Token OAuth de Drive** — solo en `secrets/google-drive-token.json` (regenerable con `app.drive_setup`)
5. **Lista de precios** — solo en `data/Lista_Precios_EHMO.xlsx`
6. **Estados operativos** — `storage/pedidos_dia/`, `storage/extras_dia/`, `folio_counter*.json`

Recomendación: el repo + OneDrive sync + GitHub privado ya da triple respaldo. Sólo asegúrate de que:
- El repo no se haga público
- El token de Render (si lo tienes) no se filtre

---

## 🧰 Comandos útiles para el día a día

```bash
# Ver estado actual de los días
ls storage/pedidos_dia/

# Recalcular totales de un día
python -c "
import json
st = json.loads(open('storage/pedidos_dia/2026-04-30.json', encoding='utf-8').read())
print(sum(h['total'] for h in st['hospitales'].values()))
"

# Reconstruir el JSON de un día desde un BD original
python -m app.rebuild_estado "ruta/al/excel.xlsx" "2026-XX-XX" "X de mes" --relacion "ruta/relacion.xlsx"

# Recargar precios sin reiniciar (Flask corriendo)
curl -X POST http://localhost:5000/api/reload-prices

# Recargar keywords sin reiniciar
curl -X POST http://localhost:5000/api/reload-keywords

# Health check
curl http://localhost:5000/health
```

---

## 📚 Referencias

- [HANDOFF.md](HANDOFF.md) — contexto histórico de la sesión 2026-04-29 (qué cambió, por qué, decisiones)
- [README.md](README.md) — descripción del proyecto
- [SETUP.md](SETUP.md) — setup paso a paso (más antiguo)

---

**Si algo no funciona después del setup**, revisa en este orden:
1. `python -c "from app import config; print(config.WHATSAPP_ACCESS_TOKEN[:10])"` — credenciales cargadas
2. `python -c "from app.pricing import cargar_lista_precios; print(len(cargar_lista_precios()))"` — lista de precios
3. `curl http://localhost:5000/health` — Flask responde
4. Logs en `storage/event_log.jsonl` (cuando exista)
