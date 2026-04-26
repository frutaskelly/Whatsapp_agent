"""Sube archivos a una carpeta de Google Drive usando OAuth user credentials.

Para cuentas personales @gmail.com, los service accounts no pueden escribir
en Mi unidad (no tienen cuota propia y Workspace domain delegation requiere
plan pagado). Por eso usamos OAuth: el usuario autoriza una vez en el
browser y se guarda un token refrescable que el server usa para subir.

Flujo:
1. Usuario corre `python -m app.drive_setup` una sola vez
2. Browser se abre, usuario autoriza
3. Token se guarda en GOOGLE_OAUTH_TOKEN
4. drive_uploader.upload_file() usa ese token automáticamente

Variables de entorno:
- GOOGLE_DRIVE_FOLDER_ID: ID de la carpeta destino
- GOOGLE_OAUTH_CREDENTIALS: ruta al JSON del OAuth Client ID (Desktop app)
- GOOGLE_OAUTH_TOKEN: ruta donde se guarda el token de usuario
"""
import logging
import mimetypes
from pathlib import Path
from . import config

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

_service = None
_init_attempted = False


def _load_credentials():
    """Carga las credenciales del usuario desde el token guardado.
    Refresca el access token si está expirado. NO inicia flujo interactivo.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    token_path = Path(config.GOOGLE_OAUTH_TOKEN) if config.GOOGLE_OAUTH_TOKEN else None
    if not token_path or not token_path.exists():
        log.warning(
            "Drive: token OAuth no existe. Corre 'python -m app.drive_setup' "
            "para autorizar una sola vez."
        )
        return None

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
            log.info("Drive: token refrescado")
        except Exception as e:
            log.exception(f"Drive: error refrescando token: {e}")
            return None
    return creds if creds and creds.valid else None


def _get_service():
    """Lazy init del cliente. Devuelve None si no está configurado."""
    global _service, _init_attempted
    if _service is not None:
        return _service
    if _init_attempted:
        return None
    _init_attempted = True

    if not config.GOOGLE_DRIVE_FOLDER_ID:
        log.info("Drive: GOOGLE_DRIVE_FOLDER_ID no configurado, upload deshabilitado")
        return None

    creds = _load_credentials()
    if not creds:
        return None

    try:
        from googleapiclient.discovery import build
        _service = build("drive", "v3", credentials=creds, cache_discovery=False)
        log.info("Drive: cliente inicializado correctamente (OAuth user)")
        return _service
    except Exception as e:
        log.exception(f"Drive: error inicializando cliente: {e}")
        return None


def upload_file(local_path: Path, original_name: str | None = None) -> dict | None:
    """Sube un archivo a la carpeta configurada. Devuelve {id, name, link} o None."""
    service = _get_service()
    if not service:
        return None
    try:
        from googleapiclient.http import MediaFileUpload
        name = original_name or local_path.name
        mime_type, _ = mimetypes.guess_type(str(local_path))
        media = MediaFileUpload(
            str(local_path),
            mimetype=mime_type or "application/octet-stream",
            resumable=False,
        )
        metadata = {
            "name": name,
            "parents": [config.GOOGLE_DRIVE_FOLDER_ID],
        }
        f = service.files().create(
            body=metadata,
            media_body=media,
            fields="id,name,webViewLink",
        ).execute()
        log.info(f"Drive upload OK: {f['name']} -> {f.get('webViewLink')}")
        return {"id": f["id"], "name": f["name"], "link": f.get("webViewLink")}
    except Exception as e:
        log.exception(f"Drive: error subiendo {local_path}: {e}")
        return None


def reset_service():
    """Resetea el cliente cacheado. Útil después de re-autorizar."""
    global _service, _init_attempted
    _service = None
    _init_attempted = False
