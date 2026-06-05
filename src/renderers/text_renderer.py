"""TextRenderer: kompakte Dashboard-Ausgabe für Terminal/Log.

Das Layout ist bewusst E-Ink-tauglich:
  - kurze Zeilen, feste Spalten
  - klare Statuslabels [OK] / [WARN] / [FAIL] statt Emojis
  - Alarm-Banner + Zusammenfassung, Fehler zuerst
  - Mini-Verlaufsstreifen und Pi-Fußzeile
  - keine Farben, keine Animationen

Hinweis zu print(): Hier ist print() bewusst erlaubt, weil die formatierte
Ausgabe das eigentliche Produkt des TextRenderers ist (vergleichbar mit einer
Display-Ausgabe). Überall sonst im Projekt wird logging verwendet.
"""

from __future__ import annotations

from datetime import datetime
from typing import List

from models import CheckResult, Dashboard, Status
from renderers.base import Renderer

# Verlaufs-Symbole: hoch=ok, niedrig=Fehler. Block-Zeichen sind in DejaVu/Mono
# vorhanden und auf E-Ink gut lesbar.
SPARK_OK = "▇"
SPARK_FAIL = "▁"
HISTORY_SHOWN = 10  # nur die letzten N Punkte zeigen (kurze Zeilen)


class TextRenderer(Renderer):
    def __init__(self, show_services_header: bool = True) -> None:
        self.show_services_header = show_services_header

    def render(self, dashboard: Dashboard) -> None:
        print(self.format(dashboard))

    def format(self, dashboard: Dashboard) -> str:
        now = dashboard.timestamp
        hosts = dashboard.sorted_for_display(dashboard.hosts)
        services = dashboard.sorted_for_display(dashboard.services)

        # Spaltenbreiten über alle (sichtbaren) Zeilen bestimmen.
        rows = hosts + services
        name_width = max((len(r.name) for r in rows), default=4)
        details = {id(r): self._detail(r, now) for r in rows}
        detail_width = max((len(d) for d in details.values()), default=0)
        tag_width = 6  # längste Variante: [WARN]/[FAIL]

        lines: List[str] = []
        lines.append(f"# {dashboard.title.upper()}")

        banner = self._banner(dashboard)
        if banner:
            lines.append(banner)
        lines.append("")

        for r in hosts:
            lines.append(
                self._format_line(r, name_width, tag_width, detail_width, details)
            )

        if hosts and services:
            lines.append("")
        if services:
            if self.show_services_header:
                lines.append("SERVICES")
            for r in services:
                lines.append(
                    self._format_line(r, name_width, tag_width, detail_width, details)
                )

        lines.append("")
        lines.append(dashboard.summary_line)
        for sysline in dashboard.system:
            lines.append(f"{sysline.name} {sysline.message}".rstrip())
        lines.append(f"OVERALL: {self._overall_tag(dashboard.overall)}")
        lines.append(f"LAST: {now.strftime('%Y-%m-%d %H:%M')}")

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _banner(dashboard: Dashboard) -> str:
        ok, warn, err = dashboard.counts
        if err:
            word = "DIENST" if err == 1 else "DIENSTE"
            return f"*** {err} {word} DOWN ***"
        if warn:
            return f"!!! {warn} WARN !!!"
        return ""

    def _format_line(
        self,
        result: CheckResult,
        name_width: int,
        tag_width: int,
        detail_width: int,
        details: dict,
    ) -> str:
        name = result.name.ljust(name_width)
        tag = result.status.tag.ljust(tag_width)
        detail = details[id(result)].ljust(detail_width)
        spark = self._sparkline(result)
        return f"{name} {tag} {detail} {spark}".rstrip()

    @staticmethod
    def _detail(result: CheckResult, now: datetime) -> str:
        if result.status is Status.ERROR:
            # Bei Ausfall lieber die Dauer als die technische Meldung zeigen.
            down = result.down_for_str(now)
            return down or (result.message or "Fehler")
        if result.response_time_ms is not None:
            return result.response_time_str
        return result.message or "-"

    @staticmethod
    def _sparkline(result: CheckResult) -> str:
        if not result.history:
            return ""
        recent = result.history[-HISTORY_SHOWN:]
        return "".join(SPARK_OK if ok else SPARK_FAIL for ok in recent)

    @staticmethod
    def _overall_tag(status: Status) -> str:
        return {Status.OK: "OK", Status.WARN: "WARN", Status.ERROR: "FAIL"}[status]
