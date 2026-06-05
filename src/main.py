#!/usr/bin/env python3
"""Einstiegspunkt für das Homelab-Statusdashboard.

Ablauf (Standard, ein Durchlauf):
    1. Konfiguration + letzten Zustand laden
    2. alle Checks ausführen (parallel)
    3. Zustand anwenden (Debounce, "down seit", Verlauf) + Statuswechsel erkennen
    4. Pi-Eigenstatus erfassen
    5. Ergebnis rendern (Text oder E-Paper-Platzhalter)
    6. Push-Benachrichtigungen bei Statuswechsel verschicken
    7. Zustand speichern, Exitcode setzen

Der regelmäßige Lauf ist Aufgabe des systemd-Timers (alle 5 Minuten), NICHT
einer Endlosschleife. Für lokale Tests gibt es optional `--watch`.

Aufruf:
    python src/main.py                       # nutzt ./config.yaml
    python src/main.py --config /pfad/x.yaml
    python src/main.py --watch               # optionaler Dauerbetrieb
    python src/main.py --once -v             # ausführliches Logging
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Tuple

# Eigenes Verzeichnis (src/) auf den Importpfad legen, damit `python src/main.py`
# UND `python -m src.main` (aus dem Projektordner) funktionieren.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from checks import run_check  # noqa: E402
from config import (  # noqa: E402
    AppConfig,
    ConfigError,
    DisplayConfig,
    Target,
    load_config,
)
from models import CheckResult, Dashboard  # noqa: E402
from notify import Notifier  # noqa: E402
from renderers.base import Renderer  # noqa: E402
from renderers.epaper_renderer import EpaperRenderer  # noqa: E402
from renderers.epaper_renderer_placeholder import (  # noqa: E402
    EpaperRendererPlaceholder,
)
from renderers.text_renderer import TextRenderer  # noqa: E402
from state import StateStore  # noqa: E402
from system_info import collect_system  # noqa: E402

logger = logging.getLogger("homelab_dashboard")

DEFAULT_CONFIG = "config.yaml"


def build_renderer(display: DisplayConfig) -> Renderer:
    """Wählt den Renderer anhand der Konfiguration aus."""
    if display.renderer == "epaper":
        return EpaperRenderer(display)
    if display.renderer == "epaper_placeholder":
        return EpaperRendererPlaceholder(display)
    return TextRenderer()


def run_checks(config: AppConfig) -> Dashboard:
    """Führt alle Checks aus (optional parallel) und gruppiert die Ergebnisse."""
    targets: List[Tuple[str, Target]] = []
    if config.nas is not None:
        targets.append(("host", config.nas))
    if config.proxmox is not None:
        targets.append(("host", config.proxmox))
    for svc in config.services:
        targets.append(("service", svc))

    if config.monitoring.parallel and len(targets) > 1:
        # Parallel: bei vielen Diensten mit Timeouts deutlich schneller, da die
        # Wartezeiten nicht mehr aufaddiert werden.
        with ThreadPoolExecutor(max_workers=config.monitoring.max_workers) as pool:
            results = list(
                pool.map(lambda gt: (gt[0], run_check(gt[1])), targets)
            )
    else:
        results = [(group, run_check(t)) for group, t in targets]

    hosts = [r for group, r in results if group == "host"]
    services = [r for group, r in results if group == "service"]
    return Dashboard(title="Homelab Status", hosts=hosts, services=services)


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,  # Logs nach stderr -> stdout bleibt für die Anzeige
    )


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Homelab-Statusdashboard für E-Ink/Terminal."
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("HOMELAB_CONFIG", DEFAULT_CONFIG),
        help=f"Pfad zur config.yaml (Default: {DEFAULT_CONFIG} "
        "bzw. $HOMELAB_CONFIG).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--once",
        action="store_true",
        help="Genau ein Durchlauf (Standardverhalten).",
    )
    group.add_argument(
        "--watch",
        action="store_true",
        help="Dauerbetrieb: wiederholt alle update_interval_seconds "
        "(optional, normalerweise übernimmt das der systemd-Timer).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Ausführliches Debug-Logging.",
    )
    return parser.parse_args(argv)


def _resolve_state_path(config_path: str, state_file: str) -> str:
    """Zustandsdatei relativ zum Verzeichnis der config.yaml ablegen, damit der
    Ort unabhängig vom Arbeitsverzeichnis stabil ist."""
    p = Path(state_file)
    if p.is_absolute():
        return str(p)
    return str(Path(config_path).resolve().parent / p)


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        logger.error("Konfigurationsfehler: %s", exc)
        return 2

    renderer = build_renderer(config.display)
    notifier = Notifier(config.notify)
    state = StateStore(_resolve_state_path(args.config, config.monitoring.state_file))
    state.load()

    if args.watch:
        interval = max(5, config.update_interval_seconds)
        logger.info("Watch-Modus aktiv – Intervall %ds. Stop mit Ctrl+C.", interval)
        try:
            while True:
                _cycle(config, renderer, state, notifier)
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Beende Watch-Modus.")
            return 0

    dashboard = _cycle(config, renderer, state, notifier)
    # Exitcode spiegelt den Gesamtstatus (0=ok/warn, 1=error) – praktisch für
    # systemd / Skripte, ohne die Anzeige zu verändern.
    return 0 if dashboard.overall.value != "error" else 1


def _cycle(
    config: AppConfig,
    renderer: Renderer,
    state: StateStore,
    notifier: Notifier,
) -> Dashboard:
    """Ein vollständiger Zyklus."""
    dashboard = run_checks(config)

    # Zustand anwenden (Debounce, down-seit, Verlauf) + Statuswechsel sammeln.
    transitions = state.apply(dashboard.monitored, config.monitoring)

    # Pi-Eigenstatus (zählt nicht in den Gesamtstatus hinein).
    dashboard.system = collect_system(config.monitoring.show_system)

    # Neu zeichnen, wenn sich der Status geändert hat ODER der Heartbeat fällig
    # ist (damit die Uhrzeit aktuell bleibt und man sieht, dass es läuft).
    now = dashboard.timestamp
    signature = dashboard.state_signature()
    status_changed = signature != state.last_signature
    heartbeat_due = state.heartbeat_due(now, config.display.heartbeat_minutes)
    dashboard.changed = status_changed or heartbeat_due

    if status_changed:
        reason = ""
    elif heartbeat_due:
        reason = " (Heartbeat-Refresh)"
    else:
        reason = " (unverändert)"
    logger.info(
        "Checks fertig: overall=%s | %s%s",
        dashboard.overall.value,
        dashboard.summary_line,
        reason,
    )

    try:
        renderer.render(dashboard)
        if dashboard.changed:
            state.mark_rendered(now)
    except Exception:  # Anzeige darf den Dienst nie crashen lassen
        logger.exception("Renderer ist fehlgeschlagen.")

    # Benachrichtigungen erst nach erfolgreichem Lauf verschicken.
    notifier.notify_transitions(transitions, dashboard)

    state.last_signature = signature
    state.save()
    return dashboard


if __name__ == "__main__":
    raise SystemExit(main())
