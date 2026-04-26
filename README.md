# Frutas Kelly - WhatsApp Agent

Agente conversacional con WhatsApp Business API + Claude AI para recibir y procesar pedidos de hospitales (Lote 5: Frutas y Verduras).

## ¿Qué hace?

EHMO (cliente) manda mensajes por WhatsApp en cualquier formato:
- Excel adjunto del pedido
- Foto del pedido escrito a mano
- PDF
- Mensaje de texto: "agrégale 20kg de jitomate al pedido del jueves"
- Audio (transcrito automáticamente)

El agente:
1. Recibe el mensaje vía webhook
2. Detecta el tipo (texto/imagen/pdf/excel/audio)
3. Manda todo a Claude AI para interpretar
4. Ejecuta la acción correcta (guardar pedido, modificar Excel, preguntar de vuelta)
5. Responde por WhatsApp con confirmación

## Arquitectura

```
EHMO -> WhatsApp -> Webhook (Render) -> Claude AI -> Acciones
                                            |
                                            v
                                    Tu carpeta de pedidos
```

## Setup local

```bash
# 1. Clonar
git clone <tu-repo>
cd whatsapp_agent

# 2. Crear entorno virtual
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# Edita .env con tus tokens reales

# 5. Correr local con ngrok para pruebas
python app/main.py
# En otra terminal:
ngrok http 5000
```

## Deploy a Render

1. Push a GitHub
2. En Render: New > Web Service > Conectar repo
3. Render lee `render.yaml` automáticamente
4. Agregar variables de entorno desde el dashboard de Render
5. Copiar la URL de Render y configurarla en Meta Developer Console

## Variables de entorno

Ver `.env.example`. Las críticas:

- `WHATSAPP_ACCESS_TOKEN`: token de Meta (24h temporal o permanente con System User)
- `WHATSAPP_PHONE_NUMBER_ID`: ID del número de WhatsApp Business
- `WHATSAPP_VERIFY_TOKEN`: cualquier string que tú inventas (se usa para verificar webhook)
- `ANTHROPIC_API_KEY`: para Claude AI

## Estructura del proyecto

```
whatsapp_agent/
├── app/
│   ├── main.py              # Entry point Flask
│   ├── webhook.py           # Endpoints de WhatsApp
│   ├── whatsapp_client.py   # Cliente para enviar mensajes
│   ├── ai_agent.py          # Lógica con Claude
│   ├── pedido_processor.py  # Procesa Excel de pedidos (basado en script existente)
│   └── config.py            # Configuración
├── storage/
│   ├── inbox/               # Archivos recibidos
│   └── conversations/       # Historial de conversación por contacto
├── requirements.txt
├── render.yaml              # Config para Render
├── .env.example
├── .gitignore
└── README.md
```

## Estado del proyecto

- [x] Setup inicial Meta App
- [x] Token y credenciales guardadas
- [ ] Webhook básico
- [ ] Deploy a Render
- [ ] Integración Claude AI
- [ ] Procesamiento Excel/PDF/Imagen
- [ ] Memoria conversacional
- [ ] Producción con número real
