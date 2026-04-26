"""Configuración central del agente WhatsApp."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Cargar .env si existe (en producción Render inyecta las vars directo).
# override=True para que .env gane sobre vars vacías heredadas del entorno (Windows).
load_dotenv(override=True)

BASE_DIR = Path(__file__).parent.parent

# === WhatsApp Business API ===
WHATSAPP_APP_ID = os.getenv("WHATSAPP_APP_ID", "")
WHATSAPP_BUSINESS_ID = os.getenv("WHATSAPP_BUSINESS_ID", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_BUSINESS_ACCOUNT_ID = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "frutaskelly_change_me")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")

# Versión de Graph API
GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# === Claude AI ===
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# === General ===
PORT = int(os.getenv("PORT", "5000"))
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
EHMO_PHONE = os.getenv("EHMO_PHONE", "")  # Whitelist - solo procesa de este número

# === Storage ===
INBOX_DIR = BASE_DIR / os.getenv("INBOX_DIR", "storage/inbox")
CONVERSATIONS_DIR = BASE_DIR / os.getenv("CONVERSATIONS_DIR", "storage/conversations")
INBOX_DIR.mkdir(parents=True, exist_ok=True)
CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

# === Google Drive (OAuth user credentials) ===
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")


def _resolve_path(env_value: str) -> str:
    """Resuelve rutas relativas contra BASE_DIR; las absolutas se devuelven tal cual."""
    if not env_value:
        return ""
    return env_value if os.path.isabs(env_value) else str(BASE_DIR / env_value)


GOOGLE_OAUTH_CREDENTIALS = _resolve_path(os.getenv("GOOGLE_OAUTH_CREDENTIALS", ""))
GOOGLE_OAUTH_TOKEN = _resolve_path(os.getenv("GOOGLE_OAUTH_TOKEN", "secrets/google-drive-token.json"))


def validate_config():
    """Valida que las variables críticas estén configuradas."""
    missing = []
    if not WHATSAPP_ACCESS_TOKEN:
        missing.append("WHATSAPP_ACCESS_TOKEN")
    if not WHATSAPP_PHONE_NUMBER_ID:
        missing.append("WHATSAPP_PHONE_NUMBER_ID")
    if not WHATSAPP_VERIFY_TOKEN or WHATSAPP_VERIFY_TOKEN == "frutaskelly_change_me":
        missing.append("WHATSAPP_VERIFY_TOKEN (debe ser un valor único)")
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")

    if missing:
        print(f"[!] Variables faltantes: {', '.join(missing)}")
        return False
    return True
