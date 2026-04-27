"""Log de mensajes entrantes/salientes para el dashboard.

Append-only JSONL en storage/message_log.jsonl. Cada línea es un mensaje
con dirección (in/out), número, tipo, body y meta opcional.
"""
import json
import threading
from datetime import datetime
from pathlib import Path
from . import config

LOG_FILE = config.BASE_DIR / "storage" / "message_log.jsonl"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()


def log_message(direction: str, phone: str, msg_type: str, body: str = "", meta: dict | None = None):
    """Agrega un mensaje al log. direction: 'in' | 'out'."""
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "direction": direction,
        "phone": phone or "",
        "type": msg_type,
        "body": (body or "")[:10000],
        "meta": meta or {},
    }
    with _lock:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_messages(limit: int = 200) -> list[dict]:
    """Devuelve los últimos N mensajes."""
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
