"""Cliente para interactuar con WhatsApp Business API (Meta Cloud API)."""
import requests
import logging
from pathlib import Path
from . import config

log = logging.getLogger(__name__)


class WhatsAppClient:
    def __init__(self):
        self.token = config.WHATSAPP_ACCESS_TOKEN
        self.phone_id = config.WHATSAPP_PHONE_NUMBER_ID
        self.api_base = config.GRAPH_API_BASE
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    # ─── Enviar mensajes ──────────────────────────────────────────────────────

    def send_text(self, to: str, body: str) -> dict:
        """Envía un mensaje de texto a un número."""
        url = f"{self.api_base}/{self.phone_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body[:4096]},  # límite de WhatsApp
        }
        r = requests.post(url, json=payload, headers=self.headers, timeout=20)
        log.info(f"send_text -> {to}: {r.status_code}")
        return r.json()

    def send_reaction(self, to: str, message_id: str, emoji: str = "👍") -> dict:
        """Reacciona a un mensaje (útil para confirmar 'recibido')."""
        url = f"{self.api_base}/{self.phone_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "reaction",
            "reaction": {"message_id": message_id, "emoji": emoji},
        }
        r = requests.post(url, json=payload, headers=self.headers, timeout=20)
        return r.json()

    def mark_as_read(self, message_id: str) -> dict:
        """Marca un mensaje como leído (azulitos)."""
        url = f"{self.api_base}/{self.phone_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        r = requests.post(url, json=payload, headers=self.headers, timeout=20)
        return r.json()

    # ─── Descargar adjuntos ───────────────────────────────────────────────────

    def get_media_url(self, media_id: str) -> str | None:
        """Obtiene la URL de descarga de un media (foto/audio/documento)."""
        url = f"{self.api_base}/{media_id}"
        r = requests.get(url, headers={"Authorization": f"Bearer {self.token}"}, timeout=20)
        if r.status_code != 200:
            log.error(f"get_media_url failed: {r.status_code} {r.text}")
            return None
        return r.json().get("url")

    def download_media(self, media_id: str, save_path: Path) -> Path | None:
        """Descarga un adjunto a disco. Devuelve el path donde quedó guardado."""
        media_url = self.get_media_url(media_id)
        if not media_url:
            return None

        r = requests.get(
            media_url,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=60,
            stream=True,
        )
        if r.status_code != 200:
            log.error(f"download_media failed: {r.status_code}")
            return None

        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        log.info(f"Media descargada: {save_path} ({save_path.stat().st_size} bytes)")
        return save_path
