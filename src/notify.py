"""Push-Benachrichtigungen bei Statuswechsel.

Das E-Ink-Display ist passiv – ein Push aufs Handy macht daraus ein aktives
Frühwarnsystem. Unterstützt werden drei einfache, self-hostbare/kostenlose
Wege: ntfy, Telegram, Gotify. Alles über simple HTTP-POSTs, keine SDKs.

Designprinzip wie überall: ein fehlgeschlagener Versand darf den Dienst nie
crashen lassen – alle Fehler werden nur geloggt.
"""

from __future__ import annotations

import logging
from typing import List

from config import NotifyConfig
from models import Dashboard
from state import Transition

logger = logging.getLogger(__name__)

try:
    import requests

    _HAS_REQUESTS = True
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]
    _HAS_REQUESTS = False


class Notifier:
    def __init__(self, config: NotifyConfig) -> None:
        self.config = config

    def notify_transitions(
        self, transitions: List[Transition], dashboard: Dashboard
    ) -> None:
        """Verschickt für relevante Statuswechsel je eine Nachricht."""
        if not self.config.enabled or not transitions:
            return
        if not _HAS_REQUESTS:
            logger.warning("Push gewünscht, aber 'requests' fehlt – übersprungen.")
            return

        for tr in transitions:
            if tr.kind not in self.config.notify_on:
                continue
            if tr.kind == "error":
                title = f"DOWN: {tr.name}"
                body = f"{tr.name} ist ausgefallen – {tr.message}"
                priority = "high"
            else:  # recovery
                title = f"OK: {tr.name}"
                body = f"{tr.name} ist wieder erreichbar"
                priority = "default"
            body += f"\nGesamt: {dashboard.summary_line}"
            self._send(title, body, priority, is_error=(tr.kind == "error"))

    # ------------------------------------------------------------------ #
    def _send(self, title: str, body: str, priority: str, is_error: bool) -> None:
        try:
            if self.config.provider == "ntfy":
                self._send_ntfy(title, body, priority, is_error)
            elif self.config.provider == "telegram":
                self._send_telegram(title, body)
            elif self.config.provider == "gotify":
                self._send_gotify(title, body, priority)
            logger.info("Push gesendet (%s): %s", self.config.provider, title)
        except Exception as exc:  # absichtlich breit – Push ist unkritisch
            logger.warning("Push fehlgeschlagen (%s): %s", self.config.provider, exc)

    def _send_ntfy(self, title: str, body: str, priority: str, is_error: bool) -> None:
        c = self.config
        url = f"{c.ntfy_url.rstrip('/')}/{c.ntfy_topic}"
        headers = {
            "Title": title,
            "Priority": priority,
            "Tags": "rotating_light" if is_error else "white_check_mark",
        }
        requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=10)

    def _send_telegram(self, title: str, body: str) -> None:
        c = self.config
        url = f"https://api.telegram.org/bot{c.telegram_bot_token}/sendMessage"
        payload = {"chat_id": c.telegram_chat_id, "text": f"{title}\n{body}"}
        requests.post(url, json=payload, timeout=10)

    def _send_gotify(self, title: str, body: str, priority: str) -> None:
        c = self.config
        url = f"{c.gotify_url.rstrip('/')}/message?token={c.gotify_token}"
        prio = 8 if priority == "high" else 4
        requests.post(
            url, json={"title": title, "message": body, "priority": prio}, timeout=10
        )
