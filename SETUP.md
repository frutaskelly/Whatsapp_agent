# Setup paso a paso

## 1. Abrir el proyecto en VS Code

```
Abre VS Code → File → Open Folder → selecciona "whatsapp_agent"
```

O desde terminal:
```bash
cd "C:\Users\crist\OneDrive\Documentos\Claude\Projects\pedidos chiapas\whatsapp_agent"
code .
```

## 2. Instalar Python 3.11+

Si no lo tienes: https://www.python.org/downloads/

Verifica:
```bash
python --version
```

## 3. Crear entorno virtual

```bash
python -m venv venv
venv\Scripts\activate    # Windows
# source venv/bin/activate  # Mac/Linux
```

## 4. Instalar dependencias

```bash
pip install -r requirements.txt
```

## 5. Configurar variables de entorno

```bash
copy .env.example .env    # Windows
# cp .env.example .env    # Mac/Linux
```

Edita `.env` con tus tokens reales (los del archivo `.env.whatsapp` que ya tienes).

## 6. Probar local

```bash
python -m app.main
```

Debe arrancar en `http://localhost:5000`. Visita `http://localhost:5000/health` para verificar.

## 7. Exponer a internet con ngrok (para pruebas locales)

Descarga ngrok: https://ngrok.com/download

```bash
ngrok http 5000
```

Te da una URL pública tipo `https://abc123.ngrok-free.app`. Esa la metes en Meta como webhook.

## 8. Configurar webhook en Meta

1. Ve a tu app: https://developers.facebook.com/apps/1475318460751526/
2. WhatsApp → Configuration
3. Webhook URL: `https://tu-ngrok-url.ngrok-free.app/webhook`
4. Verify token: el que pusiste en `.env` como `WHATSAPP_VERIFY_TOKEN`
5. Suscribir al evento `messages`

## 9. Hacer prueba end-to-end

Manda un WhatsApp desde tu número personal al +1 555 642 8375.
Debe llegarte respuesta automática.

## 10. Cuando funcione local → deploy a Render

Ver siguiente sección en README.md
