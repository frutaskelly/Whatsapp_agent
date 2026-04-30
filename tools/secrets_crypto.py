"""Encriptar/desencriptar archivos sensibles del proyecto con Fernet (AES-128 + HMAC).

Uso:
    # Encriptar (después de cambios locales en .env o secrets/*.json):
    python tools/secrets_crypto.py encrypt

    # Desencriptar (al clonar el repo en una máquina nueva):
    python tools/secrets_crypto.py decrypt

Cómo funciona:
  - Lee la llave desde la ruta apuntada por la env var FRUTASKELLY_KEY_PATH,
    o por default desde "../.frutaskelly_secrets.key" (fuera del repo).
  - Si pasas --key /ruta/a/llave override la ruta.
  - Encripta los archivos listados en TARGETS y los guarda con sufijo .enc.
  - Los .enc SÍ se commitean al repo. Los originales (.env, secrets/*.json) NO.

Para generar una llave nueva (una sola vez):
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > ../.frutaskelly_secrets.key

Importante: si pierdes la llave, los .enc del repo son ilegibles. Guárdala en
un lugar seguro (gestor de contraseñas, OneDrive, copia a USB, etc.).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

# Archivos sensibles a encriptar/desencriptar (relativos a la raíz del repo)
TARGETS = [
    ".env",
    "secrets/google-drive-token.json",
    "secrets/google-oauth-credentials.json",
]

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KEY_PATH = REPO_ROOT.parent / ".frutaskelly_secrets.key"


def _resolver_key_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("FRUTASKELLY_KEY_PATH")
    if env:
        return Path(env).resolve()
    return DEFAULT_KEY_PATH


def _cargar_fernet(key_path: Path) -> Fernet:
    if not key_path.exists():
        sys.exit(
            f"❌ Llave no encontrada en {key_path}\n"
            f"   Crea una con:\n"
            f'   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > "{key_path}"\n'
            f"   O exporta FRUTASKELLY_KEY_PATH=/ruta/llave"
        )
    raw = key_path.read_bytes().strip()
    try:
        return Fernet(raw)
    except Exception as e:
        sys.exit(f"❌ La llave en {key_path} no es válida ({e})")


def cmd_encrypt(key_path: Path) -> int:
    f = _cargar_fernet(key_path)
    cambios = 0
    for rel in TARGETS:
        src = REPO_ROOT / rel
        dst = REPO_ROOT / f"{rel}.enc"
        if not src.exists():
            print(f"  ⚠️  saltado (no existe): {rel}")
            continue
        ciphertext = f.encrypt(src.read_bytes())
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(ciphertext)
        print(f"  ✓ encriptado: {rel} → {rel}.enc ({len(ciphertext)} bytes)")
        cambios += 1
    print(f"\n{cambios} archivo(s) encriptado(s).")
    return 0


def cmd_decrypt(key_path: Path) -> int:
    f = _cargar_fernet(key_path)
    cambios = 0
    for rel in TARGETS:
        src = REPO_ROOT / f"{rel}.enc"
        dst = REPO_ROOT / rel
        if not src.exists():
            print(f"  ⚠️  saltado (no existe .enc): {rel}.enc")
            continue
        try:
            plaintext = f.decrypt(src.read_bytes())
        except InvalidToken:
            sys.exit(f"❌ Llave incorrecta para {rel}.enc — no se pudo desencriptar.")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(plaintext)
        print(f"  ✓ desencriptado: {rel}.enc → {rel} ({len(plaintext)} bytes)")
        cambios += 1
    print(f"\n{cambios} archivo(s) desencriptado(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("action", choices=["encrypt", "decrypt"])
    parser.add_argument("--key", help="Path al archivo de llave (overridea env y default)")
    args = parser.parse_args()
    key_path = _resolver_key_path(args.key)
    if args.action == "encrypt":
        return cmd_encrypt(key_path)
    return cmd_decrypt(key_path)


if __name__ == "__main__":
    sys.exit(main())
