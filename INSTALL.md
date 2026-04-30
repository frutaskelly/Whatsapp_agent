# Instalación — Frutas Kelly WhatsApp Agent

Setup paso a paso para arrancar el sistema en una máquina nueva.

> Para deploy a Render y mantenimiento operativo, ver [LAUNCH_GUIDE.md](LAUNCH_GUIDE.md).

---

## ⚡ Quick start (copy-paste)

```bash
# 1. Clonar
git clone https://github.com/frutaskelly/Whatsapp_agent.git
cd Whatsapp_agent

# 2. Copiar la llave de cifrado al directorio padre
cp /ruta/a/.frutaskelly_secrets.key ../.frutaskelly_secrets.key

# 3. Entorno virtual + dependencias
python -m venv venv
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate
pip install -r requirements.txt

# 4. Desencriptar credenciales
python tools/secrets_crypto.py decrypt

# 5. Validar
python -c "from app.pricing import cargar_lista_precios; print(len(cargar_lista_precios()), 'productos')"
# Debe imprimir: 106 productos

# 6. Arrancar
python -m app.main
# Visitar http://localhost:5000/health → {"status":"ok"}
```

Si todo arrancó: ✅. Si algún paso falló, ve a la sección detallada abajo.

---

## 📋 Prerequisitos

| Herramienta | Versión | Cómo verificar |
|---|---|---|
| Python | 3.11+ | `python --version` |
| pip | (incluido con Python) | `pip --version` |
| git | cualquiera reciente | `git --version` |

**Si no tienes Python 3.11:**
- Windows: https://www.python.org/downloads/ — durante el instalador marcar "Add Python to PATH"
- Mac: `brew install python@3.11` (necesita [Homebrew](https://brew.sh))
- Linux: `sudo apt install python3.11 python3.11-venv` (Ubuntu/Debian)

**Acceso al repo:** la cuenta de GitHub que uses debe tener acceso al repo privado `frutaskelly/Whatsapp_agent`. Si es máquina nueva, configura git con tu identidad y autentícate (gh CLI o token personal).

---

## 1. Clonar el repo

```bash
git clone https://github.com/frutaskelly/Whatsapp_agent.git
cd Whatsapp_agent
```

Si te pide login: usa tu cuenta GitHub o un token de acceso personal (https://github.com/settings/tokens).

---

## 2. Conseguir la llave de cifrado

Sin la llave, los archivos `.enc` del repo son ilegibles.

**Donde está la llave:**
- Tu gestor de contraseñas (1Password / Bitwarden / etc.) — busca "frutaskelly_secrets" o similar
- Un USB físico de respaldo
- Tu OneDrive en otra carpeta separada del proyecto
- Impresa en papel (en lugar seguro)

Es un archivo de texto con ~44 caracteres en una sola línea, que empieza con `gAAAAA...` (no, espera — **la llave** empieza con caracteres random base64. Es el contenido **encriptado** el que empieza con `gAAAAA`).

**Copia la llave** al directorio padre del repo:
```bash
# Windows (PowerShell):
copy "C:\ruta\a\backup\.frutaskelly_secrets.key" "..\.frutaskelly_secrets.key"

# Mac/Linux:
cp /ruta/a/backup/.frutaskelly_secrets.key ../.frutaskelly_secrets.key
```

**Verificar que existe:**
```bash
# Debe imprimir 1 línea con ~44 chars:
type "..\.frutaskelly_secrets.key"   # Windows
cat ../.frutaskelly_secrets.key      # Mac/Linux
```

> 🔑 **Si perdiste la llave**: los `.enc` del repo son irrecuperables. Tendrás que rotar todas las credenciales (Meta WhatsApp, Anthropic API key, Google OAuth) y crear una llave nueva. Ver sección "Si perdiste la llave" abajo.

---

## 3. Entorno virtual + dependencias

```bash
python -m venv venv
```

**Activar el entorno virtual:**
```bash
# Windows (PowerShell):
venv\Scripts\activate

# Windows (Git Bash o WSL):
source venv/Scripts/activate

# Mac/Linux:
source venv/bin/activate
```

Cuando esté activo, tu prompt mostrará `(venv)` al inicio.

**Instalar dependencias:**
```bash
pip install -r requirements.txt
```

Esto puede tardar 1-3 minutos (instala Flask, openpyxl, reportlab, anthropic SDK, cryptography, google-api-python-client, etc.).

**Si falla la instalación de `cryptography`:**
- Windows: instalar [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) y reintentar
- Mac: `brew install openssl rust` y reintentar
- Linux: `sudo apt install build-essential libssl-dev libffi-dev python3-dev`

---

## 4. Desencriptar credenciales

```bash
python tools/secrets_crypto.py decrypt
```

**Output esperado:**
```
  ✓ desencriptado: .env.enc → .env (xxxx bytes)
  ✓ desencriptado: secrets/google-drive-token.json.enc → secrets/google-drive-token.json (xxxx bytes)
  ✓ desencriptado: secrets/google-oauth-credentials.json.enc → secrets/google-oauth-credentials.json (xxxx bytes)

3 archivo(s) desencriptado(s).
```

**Si dice "Llave no encontrada":** la llave debe estar en `../.frutaskelly_secrets.key` (un nivel arriba del repo). Otras opciones:
```bash
# Pasarla como argumento
python tools/secrets_crypto.py decrypt --key /ruta/exacta/llave.key

# O exportar variable de entorno
export FRUTASKELLY_KEY_PATH=/ruta/exacta/llave.key   # Mac/Linux
$env:FRUTASKELLY_KEY_PATH = "C:\ruta\exacta\llave.key"   # Windows PowerShell
python tools/secrets_crypto.py decrypt
```

**Si dice "Llave incorrecta":** la llave que copiaste no corresponde a estos `.enc`. Verifica que sea la versión correcta de tu backup.

---

## 5. Validar el setup

**Lista de precios cargada:**
```bash
python -c "from app.pricing import cargar_lista_precios; print(len(cargar_lista_precios()), 'productos')"
```
Debe imprimir: `106 productos`

**Estados operativos cargados:**
```bash
python -c "from app.estado_pedido import listar_fechas_disponibles; print(listar_fechas_disponibles())"
```
Debe imprimir algo como: `['2026-04-30', '2026-04-29', '2026-04-28', '2026-04-27']`

**Credenciales cargadas:**
```bash
python -c "from app import config; print('WhatsApp token:', config.WHATSAPP_ACCESS_TOKEN[:10] + '...'); print('Anthropic:', config.ANTHROPIC_API_KEY[:10] + '...')"
```
Debe imprimir tokens (truncados). Si dice "None" o falla, el `.env` no se cargó.

---

## 6. Arrancar el agente

```bash
python -m app.main
```

**Output esperado:**
```
🚀 Frutas Kelly WhatsApp Agent — entorno: production
   Webhook URL local: http://localhost:5000/webhook
   Verify token: frutaskelly_webhook_2026_xyz
 * Running on http://0.0.0.0:5000
```

**Verificar que responde:**

En otra terminal (o navegador):
```bash
curl http://localhost:5000/health
# {"status":"ok"}
```

Para que reciba mensajes de WhatsApp reales, necesitas:
- Exponer a internet (ngrok local, o usar Render para producción)
- Configurar el webhook en Meta Developer Console

Detalles completos de eso en [LAUNCH_GUIDE.md](LAUNCH_GUIDE.md).

---

## 7. (Opcional) Probar localmente con simulator

Sin pasar por Meta/WhatsApp, puedes simular un mensaje:
```bash
curl -X POST http://localhost:5000/api/simulate \
  -H "Content-Type: application/json" \
  -d '{"text":"hola","phone":"simulator"}'
```

O usar el dashboard web: http://localhost:5000/

---

## 🔑 Si perdiste la llave

Los `.enc` ya no se pueden desencriptar. Necesitas:

### A. Generar una llave nueva

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > ../.frutaskelly_secrets.key
```

### B. Rotar todas las credenciales

Necesitas obtener nuevos valores para llenar `.env` y `secrets/`:

1. **Meta WhatsApp Business**: https://developers.facebook.com/apps/1475318460751526/
   - WhatsApp → API Setup → Generate token
   - Copiar `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, etc.

2. **Anthropic API**: https://console.anthropic.com/settings/keys
   - Crear una key nueva → copiar a `ANTHROPIC_API_KEY`

3. **Google Drive OAuth**: https://console.cloud.google.com
   - Si tienes acceso al proyecto: descargar de nuevo el OAuth Client JSON → `secrets/google-oauth-credentials.json`
   - Si no: crear nuevo OAuth Client ID (tipo "Desktop app")
   - Después: `python -m app.drive_setup` (genera `secrets/google-drive-token.json`)

4. **Lista de precios**: ya está en `data/Lista_Precios_EHMO.xlsx` (no requiere rotación, sigue siendo válida)

### C. Encriptar con la nueva llave y commitear

```bash
python tools/secrets_crypto.py encrypt
git add .env.enc secrets/*.json.enc
git commit -m "Rotar credenciales tras pérdida de llave"
git push
```

### D. Respaldar la nueva llave

**No la pierdas otra vez.** Guarda en al menos 2 lugares fuera del proyecto.

---

## 🧰 Troubleshooting común

| Síntoma | Causa probable | Fix |
|---|---|---|
| `pip install` falla en cryptography | Falta compilador C | Ver sección 3 |
| `Llave no encontrada` | La llave no está en `../.frutaskelly_secrets.key` | Copiarla o usar `--key` |
| `Llave incorrecta` | Llave de un setup distinto | Buscar la llave correcta |
| `WHATSAPP_ACCESS_TOKEN` es `None` | `.env` no se desencriptó o está vacío | `python tools/secrets_crypto.py decrypt` |
| `404` en webhook | URL mal configurada en Meta | Verificar webhook URL + verify token |
| Mensaje no llega al bot | EHMO_PHONE no incluye al remitente | Revisar `.env`, agregar el número |
| `cryptography` import error | Instalación corrupta | `pip uninstall cryptography && pip install cryptography` |

---

## 📚 Próximos pasos

- [LAUNCH_GUIDE.md](LAUNCH_GUIDE.md) — deploy a Render, mantenimiento, rotación de tokens, comandos del día a día
- [HANDOFF.md](HANDOFF.md) — contexto histórico del proyecto y reglas de negocio
- [README.md](README.md) — descripción general
