"""EpaperRenderer: echter Waveshare-B/W/R-Treiber.

Generisch für Waveshare-`waveshare_epd`-Module mit Schwarz/Weiß/Rot, z.B.:
  - epd7in5b_V2   (7,5"  800×480)   <- dein Display
  - epd2in13b_V4  (2,13" 250×122)
  - epd2in9b_V3   (2,9"  296×128)

Konfiguration (display:):
  renderer: "epaper"
  epd_model: "epd7in5b_V2"
  waveshare_lib_path: "/home/tom/e-Paper/RaspberryPi_JetsonNano/python/lib"

Der Treiber wird zur Laufzeit dynamisch geladen, damit das Projekt auf einem
Rechner ohne Hardware (oder ohne die Waveshare-Lib) trotzdem importierbar bleibt.
"""

from __future__ import annotations

import importlib
import logging
import sys

from config import DisplayConfig
from models import Dashboard
from renderers._layout import render_oriented
from renderers.base import Renderer

logger = logging.getLogger(__name__)


class EpaperRenderer(Renderer):
    def __init__(self, display: DisplayConfig) -> None:
        self.display = display
        self._module = None
        self._load_driver()

    def _load_driver(self) -> None:
        """Lädt das waveshare_epd-Modul (einmalig). Fehler werden nur geloggt –
        der Dienst läuft weiter und kann z.B. weiter Push-Alerts senden."""
        if self.display.waveshare_lib_path:
            # Pfad zur Waveshare-Lib vorne einhängen (wie im Originalprojekt).
            if self.display.waveshare_lib_path not in sys.path:
                sys.path.insert(0, self.display.waveshare_lib_path)
        model = self.display.epd_model
        if not model:
            logger.error("display.epd_model ist nicht gesetzt – kein Treiber.")
            return
        try:
            self._module = importlib.import_module(f"waveshare_epd.{model}")
            logger.info("E-Paper-Treiber geladen: waveshare_epd.%s", model)
        except Exception as exc:
            logger.error(
                "E-Paper-Treiber 'waveshare_epd.%s' nicht ladbar: %s "
                "(waveshare_lib_path korrekt? Lib + spidev/gpio installiert?)",
                model,
                exc,
            )

    def render(self, dashboard: Dashboard) -> None:
        # E-Ink schonen: nur bei Statusänderung / fälligem Heartbeat neu zeichnen.
        if self.display.redraw_only_on_change and not dashboard.changed:
            logger.info(
                "E-Paper: Status unverändert (%s) – kein Refresh.",
                dashboard.overall.value,
            )
            return
        if self._module is None:
            logger.error("Kein E-Paper-Treiber geladen – überspringe Anzeige.")
            return

        try:
            epd = self._module.EPD()
            width, height = epd.width, epd.height
            black, red = render_oriented(
                width,
                height,
                dashboard,
                self.display.rotation,
                self.display.margins(),
                self.display.show_safe_border,
                self.display.show_services_header,
                self.display.show_status_face,
            )

            epd.init()
            # Genau das Erfolgsrezept aus dem funktionierenden Originalcode:
            epd.display(epd.getbuffer(black), epd.getbuffer(red))
            epd.sleep()  # Panel schlafen legen -> schont das Display
            logger.info(
                "E-Paper aktualisiert (%dx%d, overall=%s).",
                width,
                height,
                dashboard.overall.value,
            )
        except Exception:
            # Anzeige darf den Dienst nie crashen lassen.
            logger.exception("E-Paper-Ausgabe fehlgeschlagen.")
