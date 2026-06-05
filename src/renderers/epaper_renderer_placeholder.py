"""EpaperRendererPlaceholder: Vorschau ohne Hardware.

Zeichnet das Dashboard mit derselben Logik wie der echte E-Paper-Renderer
(schwarz/weiß/rot, zwei 1-Bit-Buffer) und speichert das Ergebnis als
`epaper_preview.png`. Praktisch zum Entwickeln/Layout-Prüfen ohne Display.

Den echten Treiber-Code findest du in `epaper_renderer.py`.
"""

from __future__ import annotations

import logging

from config import DisplayConfig
from models import Dashboard
from renderers._layout import compose_rgb, render_oriented
from renderers.base import Renderer

logger = logging.getLogger(__name__)

try:
    from PIL import Image  # noqa: F401  (nur zum Prüfen, ob Pillow da ist)

    _HAS_PIL = True
except Exception:  # pragma: no cover
    _HAS_PIL = False


class EpaperRendererPlaceholder(Renderer):
    def __init__(self, display: DisplayConfig) -> None:
        self.display = display

    def render(self, dashboard: Dashboard) -> None:
        if self.display.redraw_only_on_change and not dashboard.changed:
            logger.info(
                "E-Paper (Vorschau): Status unverändert (%s) – kein Refresh.",
                dashboard.overall.value,
            )
            return

        if not _HAS_PIL:
            logger.warning("Pillow fehlt – keine Vorschau. pip install Pillow")
            return

        black, red = render_oriented(
            self.display.width,
            self.display.height,
            dashboard,
            self.display.rotation,
            self.display.margins(),
            self.display.show_safe_border,
            self.display.show_services_header,
            self.display.show_status_face,
        )
        try:
            compose_rgb(black, red).save("epaper_preview.png")
            logger.info(
                "Vorschau gespeichert: epaper_preview.png (%dx%d, overall=%s).",
                self.display.width,
                self.display.height,
                dashboard.overall.value,
            )
        except OSError as exc:
            logger.warning("Vorschau konnte nicht gespeichert werden: %s", exc)
