"""Datenmodelle für das Homelab-Statusdashboard.

Dieses Modul ist bewusst frei von externen Abhängigkeiten, damit es überall
(auch in Tests) ohne Installation von requests/Pillow importiert werden kann.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple


class Status(str, Enum):
    """Status eines einzelnen Checks oder des Gesamtsystems.

    Die Reihenfolge entspricht der "Schwere": OK < WARN < ERROR.
    """

    OK = "ok"
    WARN = "warn"
    ERROR = "error"

    @property
    def severity(self) -> int:
        return {Status.OK: 0, Status.WARN: 1, Status.ERROR: 2}[self]

    @property
    def tag(self) -> str:
        """Kurzes, E-Ink-freundliches Label ohne Emojis."""
        return {Status.OK: "[OK]", Status.WARN: "[WARN]", Status.ERROR: "[FAIL]"}[self]


@dataclass
class CheckResult:
    """Ergebnis eines einzelnen Checks."""

    name: str
    status: Status
    message: str = ""
    response_time_ms: Optional[float] = None
    timestamp: datetime = field(default_factory=datetime.now)
    # --- vom Zustandsspeicher (state.py) angereichert ---------------------- #
    # Seit wann ist dieser Check im Fehlerzustand? (für "down seit ...")
    down_since: Optional[datetime] = None
    # Kurzer Verlauf: True = ok-ish (kein Fehler), False = Fehler. Ältester zuerst.
    history: List[bool] = field(default_factory=list)
    # Systemzeilen (Pi-Eigenstatus) werden separat dargestellt und zählen nicht
    # in den Gesamtstatus hinein.
    is_system: bool = False

    @property
    def response_time_str(self) -> str:
        """z.B. '120ms' oder '-' wenn keine Zeit gemessen wurde."""
        if self.response_time_ms is None:
            return "-"
        return f"{int(round(self.response_time_ms))}ms"

    def down_for_str(self, now: Optional[datetime] = None) -> str:
        """Kompakte Ausfalldauer, z.B. 'down 12m' / 'down 3h' / 'down 2d'."""
        if self.down_since is None:
            return ""
        now = now or datetime.now()
        secs = max(0, int((now - self.down_since).total_seconds()))
        if secs < 60:
            return f"down {secs}s"
        mins = secs // 60
        if mins < 60:
            return f"down {mins}m"
        hours = mins // 60
        if hours < 24:
            return f"down {hours}h"
        return f"down {hours // 24}d"


@dataclass
class SidePanel:
    """Inhalt des optionalen rechten Panels: Ausfall-Counter + Spruch/Fakt."""

    days_without_incident: int
    incident_now: bool  # aktuell ein Ausfall? -> Counter rot / 0
    header: str
    body: str


@dataclass
class Dashboard:
    """Gesammeltes Ergebnis aller Checks, gruppiert für die Anzeige.

    `hosts`    -> Infrastruktur (NAS, Proxmox) – wird ohne Überschrift angezeigt.
    `services` -> einzelne Dienste – werden unter "SERVICES" angezeigt.
    `system`   -> Pi-Eigenstatus (Temp/WLAN/Uptime) – Fußzeile, nicht im Overall.
    """

    title: str
    hosts: List[CheckResult] = field(default_factory=list)
    services: List[CheckResult] = field(default_factory=list)
    system: List[CheckResult] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    # Hat sich gegenüber dem letzten Lauf etwas geändert? (für E-Ink-Refresh)
    changed: bool = True
    # Optionales rechtes Panel (Counter + Witz/Tech-History). Wenn gesetzt, zeigt
    # der E-Paper-Renderer es an und blendet die Verlaufs-Sparklines aus.
    side_panel: "Optional[SidePanel]" = None

    @property
    def monitored(self) -> List[CheckResult]:
        """Alle in den Gesamtstatus einfließenden Checks (ohne Systemzeilen)."""
        return [*self.hosts, *self.services]

    @property
    def all_results(self) -> List[CheckResult]:
        return [*self.hosts, *self.services, *self.system]

    @property
    def overall(self) -> Status:
        """Gesamtstatus: error, wenn irgendein Check error ist; sonst warn,
        wenn irgendein Check warn ist; sonst ok. Systemzeilen zählen nicht."""
        results = self.monitored
        if not results:
            return Status.WARN  # nichts konfiguriert -> verdächtig
        worst = max(results, key=lambda r: r.status.severity)
        return worst.status

    @property
    def counts(self) -> Tuple[int, int, int]:
        """(ok, warn, error) über die überwachten Checks."""
        ok = warn = err = 0
        for r in self.monitored:
            if r.status is Status.OK:
                ok += 1
            elif r.status is Status.WARN:
                warn += 1
            else:
                err += 1
        return ok, warn, err

    @property
    def summary_line(self) -> str:
        ok, warn, err = self.counts
        return f"{ok} OK · {warn} WARN · {err} FAIL"

    def sorted_for_display(self, results: List[CheckResult]) -> List[CheckResult]:
        """Fehler zuerst (stabil): error -> warn -> ok, Reihenfolge sonst erhalten."""
        return sorted(results, key=lambda r: -r.status.severity)

    def state_signature(self) -> str:
        """Kompakte Signatur des *anzeigerelevanten* Zustands.

        Dient dem E-Ink-Renderer, um nur bei echter Änderung neu zu zeichnen.
        Antwortzeiten werden bewusst ignoriert (ändern sich ständig)."""
        parts = [self.overall.value]
        for r in self.monitored:
            parts.append(f"{r.name}={r.status.value}")
        return "|".join(parts)
