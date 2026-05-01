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
# STORAGE_DIR: raíz de TODO el storage runtime (state, conversations, logs,
# folio counters, configs editables). En producción (Render) apuntar a un
# disco persistente, p.ej. STORAGE_DIR=/var/data, para que NO se borre en
# cada redeploy. Localmente queda en `whatsapp_agent/storage/`.
_storage_env = os.getenv("STORAGE_DIR", "")
if _storage_env:
    STORAGE_DIR = Path(_storage_env) if os.path.isabs(_storage_env) else BASE_DIR / _storage_env
else:
    STORAGE_DIR = BASE_DIR / "storage"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

# SEED_STORAGE_DIR: directorio in-repo con defaults para sembrar disco vacío
# en el primer arranque (config files, lista de precios, lo que ya estaba en
# git). Solo se copia lo que NO exista todavía en STORAGE_DIR.
SEED_STORAGE_DIR = BASE_DIR / "storage"

def _resolve_storage_subdir(env_name: str, default_subdir: str) -> Path:
    """Resuelve un subdir de storage. Acepta override via env var.
    - Si env absoluto → usa tal cual.
    - Si env relativo → relativo a BASE_DIR (compat con .env legacy
      'storage/inbox').
    - Si env vacío → STORAGE_DIR/default_subdir.
    """
    val = os.getenv(env_name, "").strip()
    if not val:
        return STORAGE_DIR / default_subdir
    return Path(val) if os.path.isabs(val) else BASE_DIR / val


INBOX_DIR = _resolve_storage_subdir("INBOX_DIR", "inbox")
CONVERSATIONS_DIR = _resolve_storage_subdir("CONVERSATIONS_DIR", "conversations")
PROCESSED_DIR = _resolve_storage_subdir("PROCESSED_DIR", "processed")
INBOX_DIR.mkdir(parents=True, exist_ok=True)
CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# Lista de precios EHMO (para generar notas de remisión)
_precios_env = os.getenv("LISTA_PRECIOS_PATH", "../Lista_Precios_EHMO.xlsx")
LISTA_PRECIOS_PATH = _precios_env if os.path.isabs(_precios_env) else str(BASE_DIR / _precios_env)

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
