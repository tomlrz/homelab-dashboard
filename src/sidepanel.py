"""Inhalt für das rechte Panel: täglich wechselnd Witz / Tech-History.

Liest eine kleine JSON-Datei (offline, im Repo mitgeliefert) und wählt anhand des
Tages deterministisch einen Eintrag – gerade Tage Witz, ungerade Tech-History.
Deterministisch heißt: an einem Tag immer derselbe Text (kein Flackern bei
mehreren Refreshes). Fehlt/defekt die Datei, greift eine eingebaute Mini-Liste.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

_FALLBACK = {
    "jokes": [
        "Es gibt 10 Arten von Menschen: die, die Binär verstehen, und die, "
        "die es nicht tun.",
    ],
    "history": [
        "1969: Die erste ARPANET-Verbindung wird aufgebaut – der Urknall des "
        "Internets.",
    ],
}


def pick_text(content_path: str, now: datetime) -> Tuple[str, str]:
    """Liefert (header, body) für das Panel, abhängig vom Datum."""
    data = _load(content_path)
    jokes: List[str] = data.get("jokes") or _FALLBACK["jokes"]
    history: List[str] = data.get("history") or _FALLBACK["history"]

    doy = now.timetuple().tm_yday
    if doy % 2 == 0:
        lst, header = jokes, "WITZ DES TAGES"
    else:
        lst, header = history, "TECH-GESCHICHTE"

    if not lst:
        return header, ""
    body = lst[(doy // 2) % len(lst)]
    return header, body


def _load(content_path: str) -> dict:
    try:
        with Path(content_path).open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Panel-Inhalt nicht ladbar (%s) – nutze Fallback.", exc)
    return _FALLBACK
