"""Log estructurado de eventos del agente para visualización en el dashboard.

A diferencia de message_log (que registra mensajes humanos in/out), este log
captura los pasos internos: cuando se llama a Claude, cuando se sube algo a
Drive, cuando se ejecuta el procesador, etc. Sirve para que el operador vea
en tiempo real qué hace el sistema.
"""
import json
import threading
from datetime import datetime
from pathlib import Path
from . import config

LOG_FILE = config.STORAGE_DIR / "event_log.jsonl"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()


def log_event(category: str, message: str, meta: dict | None = None, level: str = "info"):
    """Registra un paso interno del agente.

    category: "webhook" | "ai" | "drive" | "processor" | "storage" | "system"
    level:    "info" | "warn" | "error"
    """
    entry = {
        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
        "category": category,
        "level": level,
        "message": (message or "")[:500],
        "meta": meta or {},
    }
    with _lock:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_events(limit: int = 200) -> list[dict]:
    """Devuelve los últimos N eventos."""
    if not LOG_FILE.exists():
        return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    out = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
