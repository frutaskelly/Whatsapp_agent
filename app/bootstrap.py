"""Sembrado del directorio de storage persistente.

Cuando el app corre en Render con un disco persistente montado en
STORAGE_DIR (p.ej. /var/data), el primer arranque encuentra el disco
VACÍO. Esta función copia los archivos por defecto del repo
(SEED_STORAGE_DIR = whatsapp_agent/storage/) hacia STORAGE_DIR para que
el sistema esté operativo sin pasos manuales.

Política:
- Solo copia archivos que NO existan en STORAGE_DIR (no sobrescribe).
- Esto preserva ediciones que el operador haya hecho a través del
  dashboard (agentes.json, clientes.json, keywords.json, lista de
  precios, folio counters, etc.) entre redeploys.
- Si una config nueva se agrega via git push y necesita propagarse al
  disco, hay que borrarla manualmente en el disco y dejar que el seed
  la copie en el siguiente arranque (o editarla por dashboard).
"""
import logging
import shutil
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

# Subdirs/archivos del seed que NO se copian al disco persistente:
# son runtime/transients que se regeneran solos o son de dev local.
_SKIP_TOP = {
    "inbox",            # archivos subidos en webhooks (regenerable)
    "processed",        # PDFs/Excels generados (regenerable)
    "raw_webhooks",     # debug
    "_backups",         # backups locales
    "conversations",    # historial conversacional por usuario (per-deploy)
    "storage",          # dir vacío accidental, no relevante
}
_SKIP_FILES = {
    "event_log.jsonl",   # log local — no contaminar prod
    "message_log.jsonl", # idem
}


def seed_storage_if_empty() -> dict:
    """Copia SEED_STORAGE_DIR → STORAGE_DIR para archivos que falten.

    Returns:
        dict con conteos: {"copied": N, "skipped": M, "errors": [...]}.
    """
    seed_dir: Path = config.SEED_STORAGE_DIR
    target_dir: Path = config.STORAGE_DIR

    # Si son el mismo path (dev local sin override), no hacer nada.
    try:
        if seed_dir.resolve() == target_dir.resolve():
            return {"copied": 0, "skipped": 0, "errors": [],
                    "skipped_reason": "seed_dir == target_dir (dev local)"}
    except FileNotFoundError:
        # En dev local el target puede no existir todavía
        pass

    if not seed_dir.exists():
        return {"copied": 0, "skipped": 0, "errors": [],
                "skipped_reason": f"seed_dir no existe: {seed_dir}"}

    target_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0
    errors: list[str] = []

    # Recorrer el árbol del seed; replicar estructura en target si el
    # archivo no existe ahí. Saltarse subdirs/archivos transients que
    # no tienen sentido en producción (logs locales, processed/, etc.).
    for src in seed_dir.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(seed_dir)
        # Excluir si el primer componente está en SKIP_TOP
        if rel.parts and rel.parts[0] in _SKIP_TOP:
            continue
        # Excluir _backups en cualquier nivel (recovery artifacts)
        if "_backups" in rel.parts:
            continue
        # Excluir archivos en la raíz del seed que sean logs/transients
        if rel.parent == Path(".") and rel.name in _SKIP_FILES:
            continue
        dst = target_dir / rel
        if dst.exists():
            skipped += 1
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
            log.info(f"[bootstrap] seed → {rel}")
        except Exception as e:
            errors.append(f"{rel}: {e}")
            log.warning(f"[bootstrap] error copiando {rel}: {e}")

    log.info(f"[bootstrap] storage seeded: copiados={copied}, "
             f"existentes={skipped}, errores={len(errors)}, "
             f"target={target_dir}")
    return {"copied": copied, "skipped": skipped, "errors": errors,
            "target_dir": str(target_dir), "seed_dir": str(seed_dir)}
