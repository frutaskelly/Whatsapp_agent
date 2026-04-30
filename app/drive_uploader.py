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
import time
from datetime import datetime
from pathlib import Path
from . import config
from .event_log import log_event

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

_service = None
_init_attempted = False
_folder_cache: dict[tuple[str, str], str] = {}  # (parent_id, name) -> folder_id


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


# Timeout HTTP para llamadas a Drive (segundos). Más alto que el default
# para tolerar redes lentas o el archivo grande sin abortar prematuramente.
_HTTP_TIMEOUT_SECONDS = 180


def _build_authed_http(creds, timeout: int = _HTTP_TIMEOUT_SECONDS):
    """Crea un cliente HTTP autenticado con timeout explícito."""
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    return AuthorizedHttp(creds, http=httplib2.Http(timeout=timeout))


def _get_service(force_new: bool = False):
    """Lazy init del cliente. Devuelve None si no está configurado.

    Si `force_new=True`, recrea el cliente desde cero — útil después de
    timeouts/SSL errors persistentes (la conexión cacheada puede estar muerta).
    """
    global _service, _init_attempted
    if _service is not None and not force_new:
        return _service
    if _init_attempted and not force_new:
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
        try:
            authed_http = _build_authed_http(creds)
            _service = build("drive", "v3", http=authed_http, cache_discovery=False)
        except ImportError:
            # Fallback si google-auth-httplib2 no está disponible:
            # build sin http custom (usa default sin timeout explícito)
            _service = build("drive", "v3", credentials=creds, cache_discovery=False)
        if force_new:
            log.info("Drive: cliente RE-inicializado (reset por errores)")
        else:
            log.info(f"Drive: cliente inicializado (timeout HTTP={_HTTP_TIMEOUT_SECONDS}s)")
        return _service
    except Exception as e:
        log.exception(f"Drive: error inicializando cliente: {e}")
        return None


def _get_or_create_folder(name: str, parent_id: str, service) -> str:
    """Busca una subcarpeta por nombre dentro de parent_id; la crea si no existe.

    Cachea el resultado para no consultar la API en cada upload.
    Devuelve el ID de la carpeta.
    """
    cache_key = (parent_id, name)
    if cache_key in _folder_cache:
        return _folder_cache[cache_key]

    safe_name = name.replace("'", "\\'")
    query = (
        f"name = '{safe_name}' "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and '{parent_id}' in parents and trashed = false"
    )
    try:
        results = service.files().list(q=query, fields="files(id,name)", pageSize=5).execute()
        files = results.get("files", [])
        if files:
            folder_id = files[0]["id"]
            _folder_cache[cache_key] = folder_id
            return folder_id

        # Crear nueva
        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        f = service.files().create(body=metadata, fields="id,name").execute()
        _folder_cache[cache_key] = f["id"]
        log.info(f"Drive: subcarpeta creada '{name}' (id={f['id']})")
        log_event("drive", f"📁 Subcarpeta creada: {name}", {"id": f["id"]})
        return f["id"]
    except Exception as e:
        log.exception(f"Drive: error con subcarpeta '{name}': {e}")
        log_event("drive", f"❌ Error con subcarpeta '{name}'", {"error": str(e)[:200]}, level="error")
        # Fallback al parent original
        return parent_id


def _build_drive_name(original_name: str) -> str:
    """Prepende timestamp al nombre del archivo para evitar duplicados en Drive.

    Ejemplo: 'Pedido.xlsx' -> '2026-04-26_16-30-45_Pedido.xlsx'
    El formato sortea cronológicamente al ordenar por nombre en Drive.
    """
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    p = Path(original_name)
    return f"{ts}_{p.stem}{p.suffix}"


def upload_file(local_path: Path, original_name: str | None = None,
                subfolder: str | None = None) -> dict | None:
    """Sube un archivo a la carpeta de Drive (con subcarpeta opcional por día).

    Si `subfolder` se proporciona, el archivo va a una subcarpeta de ese nombre
    dentro de GOOGLE_DRIVE_FOLDER_ID. La subcarpeta se crea automáticamente si
    no existe; si ya existe se reutiliza.

    Si la carpeta destino fue borrada (404), invalida la caché y reintenta
    una vez (creando la carpeta de nuevo).

    Devuelve {id, name, link} o None.
    """
    service = _get_service()
    if not service:
        log_event("drive", "Upload omitido (Drive no configurado)", level="warn")
        return None

    return _do_upload(local_path, original_name, subfolder, service, retry_on_404=True)


_TRANSIENT_ERROR_NAMES = {
    "SSLEOFError", "SSLError", "ConnectionError", "ConnectionResetError",
    "ReadTimeoutError", "ConnectTimeoutError", "ServerDisconnectedError",
    "RemoteDisconnected", "ProtocolError", "IncompleteRead", "TimeoutError",
    "ChunkedEncodingError",
}


def _is_transient_error(e: Exception) -> bool:
    if type(e).__name__ in _TRANSIENT_ERROR_NAMES:
        return True
    msg = str(e).lower()
    return any(t in msg for t in [
        "ssl", "connection reset", "connection aborted", "timed out",
        "eof occurred", "remote end closed", "broken pipe",
    ])


def _do_upload(local_path: Path, original_name: str | None, subfolder: str | None,
               service, retry_on_404: bool = True, max_retries: int = 5) -> dict | None:
    base_name = original_name or local_path.name
    drive_name = _build_drive_name(base_name)
    size_bytes = local_path.stat().st_size if local_path.exists() else 0
    size_kb = size_bytes // 1024
    # Archivos >= 5 MB: usar upload resumable (más robusto a cortes intermitentes)
    use_resumable = size_bytes >= 5 * 1024 * 1024

    # Resolver carpeta destino (raíz o subcarpeta del día)
    parent_id = config.GOOGLE_DRIVE_FOLDER_ID
    if subfolder:
        parent_id = _get_or_create_folder(subfolder, parent_id, service)

    log_event("drive", f"⬆️ Subiendo a Drive: {drive_name}",
              {"size_kb": size_kb, "subfolder": subfolder or "(raíz)",
               "resumable": use_resumable})

    last_error = None
    for intento in range(max_retries):
        t0 = time.time()
        try:
            from googleapiclient.http import MediaFileUpload
            mime_type, _ = mimetypes.guess_type(str(local_path))
            media = MediaFileUpload(
                str(local_path),
                mimetype=mime_type or "application/octet-stream",
                resumable=use_resumable,
            )
            metadata = {
                "name": drive_name,
                "parents": [parent_id],
            }
            f = service.files().create(
                body=metadata,
                media_body=media,
                fields="id,name,webViewLink",
            ).execute()
            elapsed = round((time.time() - t0) * 1000)
            log.info(f"Drive upload OK: {f['name']} -> {f.get('webViewLink')}")
            suffix = f" (reintento #{intento})" if intento > 0 else ""
            log_event("drive", f"✓ Subido en {elapsed}ms{suffix}",
                      {"link": f.get("webViewLink"), "name": f["name"]})
            return {"id": f["id"], "name": f["name"], "link": f.get("webViewLink")}
        except Exception as e:
            last_error = e
            msg = str(e)

            # 404 (subcarpeta borrada): invalidar cache y reintentar
            if retry_on_404 and subfolder and ("File not found" in msg or "404" in msg):
                log.warning(f"Drive: subcarpeta '{subfolder}' borrada, invalido cache")
                log_event("drive", "♻️ Cache de subcarpeta invalidada, reintentando", level="warn")
                _folder_cache.pop((config.GOOGLE_DRIVE_FOLDER_ID, subfolder), None)
                parent_id = _get_or_create_folder(subfolder, config.GOOGLE_DRIVE_FOLDER_ID, service)
                retry_on_404 = False  # solo una vez
                continue

            # Errores transitorios (SSL, conexión, timeout): reintento con backoff
            if _is_transient_error(e) and intento < max_retries - 1:
                wait = min(30, 2 ** (intento + 1))  # 2s, 4s, 8s, 16s, 30s
                # Después del 2do fallo, recrear el service (la conexión
                # cacheada puede estar muerta — esto fuerza un socket nuevo).
                refresh_msg = ""
                if intento >= 1:
                    service = _get_service(force_new=True) or service
                    _folder_cache.clear()
                    if subfolder:
                        parent_id = _get_or_create_folder(subfolder, config.GOOGLE_DRIVE_FOLDER_ID, service)
                    refresh_msg = " + cliente Drive recreado"
                log_event("drive",
                          f"♻️ Error transitorio ({type(e).__name__}), reintento en {wait}s{refresh_msg}",
                          {"intento": intento + 1, "error": msg[:120],
                           "espera_s": wait}, level="warn")
                time.sleep(wait)
                continue

            # Error definitivo o se agotaron reintentos
            break

    log.exception(f"Drive: error subiendo {local_path}: {last_error}")
    log_event("drive", f"❌ Error subiendo tras {max_retries} intentos: {type(last_error).__name__}",
              {"error": str(last_error)[:200]}, level="error")
    return None


def reset_service():
    """Resetea el cliente cacheado. Útil después de re-autorizar."""
    global _service, _init_attempted
    _service = None
    _init_attempted = False
