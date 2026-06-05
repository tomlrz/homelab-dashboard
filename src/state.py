"""Zustandsspeicher zwischen den Läufen.

Da der Dienst pro Lauf neu startet (systemd-Timer), brauchen wir eine kleine
persistente Datei, um über Läufe hinweg Dinge zu wissen wie:
  - seit wann ist ein Dienst down ("down seit ..."),
  - wie viele Fehlschläge in Folge (Flapping-Schutz / Debounce),
  - ein kurzer Verlauf (ok/fail) für den Sparkline-Streifen,
  - die letzte Anzeige-Signatur (E-Ink nur bei Änderung neu zeichnen),
  - der letzte gemeldete Status (für Push bei Statuswechsel).

Bewusst nur die Standardbibliothek + JSON – keine Datenbank.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config import MonitoringConfig
from models import CheckResult, Status

logger = logging.getLogger(__name__)


@dataclass
class Transition:
    """Ein Statuswechsel eines Checks zwischen zwei Läufen (für Push-Alerts)."""

    name: str
    kind: str  # "error" (neu ausgefallen) | "recovery" (wieder ok)
    message: str


class StateStore:
    """Lädt/speichert den Zustand als JSON. Fehlertolerant: eine kaputte oder
    fehlende Datei führt nur zu einem leeren Startzustand, nie zum Absturz."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._data: Dict = {"checks": {}, "signature": None}

    def load(self) -> None:
        if not self.path.exists():
            logger.debug("Keine Zustandsdatei (%s) – starte frisch.", self.path)
            return
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self._data = loaded
                self._data.setdefault("checks", {})
                self._data.setdefault("signature", None)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Zustandsdatei unlesbar (%s) – starte frisch.", exc)

    def save(self) -> None:
        try:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            tmp.replace(self.path)  # atomar -> nie halb geschriebene Datei
        except OSError as exc:
            logger.warning("Zustand konnte nicht gespeichert werden: %s", exc)

    @property
    def last_signature(self) -> Optional[str]:
        return self._data.get("signature")

    @last_signature.setter
    def last_signature(self, value: str) -> None:
        self._data["signature"] = value

    def heartbeat_due(self, now: datetime, heartbeat_minutes: int) -> bool:
        """True, wenn seit dem letzten Zeichnen >= heartbeat_minutes vergangen
        sind (oder noch nie gezeichnet wurde). 0 schaltet den Heartbeat aus."""
        if heartbeat_minutes <= 0:
            return False
        last = self._data.get("last_render")
        last_dt = _parse_iso(last) if last else None
        if last_dt is None:
            return True
        return (now - last_dt).total_seconds() >= heartbeat_minutes * 60

    def mark_rendered(self, now: datetime) -> None:
        """Merkt sich den Zeitpunkt des letzten tatsächlichen Neuzeichnens."""
        self._data["last_render"] = now.isoformat()

    def days_without_incident(self, now: datetime, is_error: bool) -> int:
        """'Tage ohne Ausfall'. Bei einem Ausfall (is_error) wird auf 0 gesetzt
        und der Startpunkt auf heute zurückgesetzt; sonst Tage seit dem letzten
        Ausfall (bzw. seit dem ersten Lauf)."""
        today = now.date()
        clean_since = self._data.get("clean_since")
        if is_error or not clean_since:
            self._data["clean_since"] = today.isoformat()
            if is_error:
                return 0
            clean_since = today.isoformat()
        try:
            start = datetime.fromisoformat(clean_since).date()
        except (ValueError, TypeError):
            start = today
            self._data["clean_since"] = today.isoformat()
        return max(0, (today - start).days)

    # ------------------------------------------------------------------ #
    # Kernlogik: rohe Check-Ergebnisse mit Zustand anreichern
    # ------------------------------------------------------------------ #
    def apply(
        self,
        results: List[CheckResult],
        config: MonitoringConfig,
        now: Optional[datetime] = None,
    ) -> List[Transition]:
        """Wendet Debounce/Down-seit/Verlauf auf die Ergebnisse an (in-place)
        und liefert die Statuswechsel für die Benachrichtigung.

        Wichtig: `results` werden direkt verändert (status/down_since/history).
        """
        now = now or datetime.now()
        checks: Dict[str, Dict] = self._data.setdefault("checks", {})
        transitions: List[Transition] = []

        for result in results:
            prev = checks.get(result.name, {})
            prev_reported: Optional[str] = prev.get("reported_status")
            raw_is_error = result.status is Status.ERROR

            # --- Flapping-Schutz / Debounce ------------------------------ #
            consecutive = int(prev.get("consecutive_failures", 0))
            consecutive = consecutive + 1 if raw_is_error else 0

            if raw_is_error and consecutive < config.failure_threshold:
                # Noch nicht oft genug fehlgeschlagen -> als WARN "abfedern",
                # damit ein einzelner Aussetzer nicht sofort rot wird.
                result.status = Status.WARN
                result.message = (
                    f"instabil ({consecutive}/{config.failure_threshold}): "
                    f"{result.message}"
                )

            effective_is_error = result.status is Status.ERROR

            # --- "down seit ..." ----------------------------------------- #
            down_since_iso: Optional[str] = prev.get("down_since")
            if effective_is_error:
                if not down_since_iso:
                    down_since_iso = now.isoformat()
                result.down_since = _parse_iso(down_since_iso)
            else:
                down_since_iso = None

            # --- Verlaufsstreifen ---------------------------------------- #
            history: List[int] = list(prev.get("history", []))
            history.append(0 if effective_is_error else 1)
            history = history[-config.history_length :]
            result.history = [bool(x) for x in history]

            # --- Statuswechsel für Push erkennen ------------------------- #
            reported = result.status.value
            if prev_reported is not None and prev_reported != reported:
                if reported == "error":
                    transitions.append(
                        Transition(result.name, "error", result.message or "down")
                    )
                elif prev_reported == "error" and reported in ("ok", "warn"):
                    transitions.append(
                        Transition(result.name, "recovery", result.message or "ok")
                    )

            checks[result.name] = {
                "reported_status": reported,
                "consecutive_failures": consecutive,
                "down_since": down_since_iso,
                "history": history,
            }

        # Verwaiste Einträge (Dienst aus Config entfernt) aufräumen.
        active = {r.name for r in results}
        for stale in [k for k in checks if k not in active]:
            del checks[stale]

        return transitions


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
