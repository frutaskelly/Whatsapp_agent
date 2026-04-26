"""Setup interactivo de OAuth para Google Drive.

Corre una sola vez:
    python -m app.drive_setup

Abre el browser, te pide autorizar el acceso a Drive y guarda un token
en GOOGLE_OAUTH_TOKEN. Después de eso, drive_uploader.upload_file()
funciona sin intervención del usuario.

Si el token expira o se revoca, vuelve a correr este script.
"""
import sys
from pathlib import Path
from . import config

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def main():
    creds_path = Path(config.GOOGLE_OAUTH_CREDENTIALS) if config.GOOGLE_OAUTH_CREDENTIALS else None
    token_path = Path(config.GOOGLE_OAUTH_TOKEN) if config.GOOGLE_OAUTH_TOKEN else None

    if not creds_path or not creds_path.exists():
        print(f"[ERROR] No encuentro el archivo OAuth credentials.")
        print(f"        Esperado en: {creds_path}")
        print(f"        Crea un OAuth Client ID (Desktop) en Google Cloud Console")
        print(f"        y descarga el JSON a esa ruta.")
        sys.exit(1)

    if not token_path:
        print("[ERROR] GOOGLE_OAUTH_TOKEN no configurado en .env")
        sys.exit(1)

    from google_auth_oauthlib.flow import InstalledAppFlow

    print(f"[*] Usando credentials: {creds_path}")
    print(f"[*] Token se guardará en: {token_path}")
    print(f"[*] Scopes: {SCOPES}")
    print(f"[*] Abriendo browser para autorización...")

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"[OK] Token guardado en {token_path}")
    print(f"[OK] Drive uploads listos. Reinicia el server Flask.")


if __name__ == "__main__":
    main()
