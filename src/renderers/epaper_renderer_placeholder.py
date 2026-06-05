"""EpaperRendererPlaceholder: Platzhalter für ein echtes E-Paper-Display.

Solange das genaue Displaymodell unbekannt ist, enthält diese Klasse KEINEN
hardwarespezifischen Waveshare-Code. Sie:

  1. nutzt dieselbe Renderer-Schnittstelle wie der TextRenderer,
  2. baut bereits ein Pillow-Bild im konfigurierten Format auf (s/w/rot) mit
     Alarm-Banner, Status-Glyphen, "down seit", Verlaufsstreifen und Pi-Fußzeile,
  3. markiert mit `# TODO(waveshare)` exakt die Stellen, an denen später der
     echte Treiber-Code ergänzt wird.

Sobald das Modell feststeht (z.B. Waveshare 2.13" V4 B/W/R), ersetzt man die
TODO-Stellen durch das passende `waveshare_epd`-Modul – das übrige Projekt
(config, checks, models) bleibt unverändert.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from config import DisplayConfig
from models import CheckResult, Dashboard, Status
from renderers.base import Renderer

logger = logging.getLogger(__name__)

# Pillow ist optional. Wenn es fehlt, beschwert sich der Placeholder nicht beim
# Import, sondern erst beim tatsächlichen Rendern – mit klarer Meldung.
try:
    from PIL import Image, ImageDraw, ImageFont

    _HAS_PIL = True
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageFont = None  # type: ignore[assignment]
    _HAS_PIL = False

# Farben für ein 3-Farben-Display (schwarz/weiß/rot).
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
# Auf echten B/W/R-Displays wird Rot über einen zweiten Frame-Buffer gesetzt.
# Im Placeholder zeichnen wir Rot nur ins Vorschaubild (RGB).
RED = (255, 0, 0)

# Kandidaten für einen Monospace-TTF-Font (erste Treffer gewinnt). Monospace
# sorgt auf E-Ink für ein ruhiges, bündiges Layout. Reihenfolge: Raspberry Pi
# OS zuerst, dann macOS (lokale Entwicklung), dann Pillow-Default als Notnagel.
CANDIDATE_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",  # Raspberry Pi OS
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/System/Library/Fonts/Menlo.ttc",                      # macOS
    "/System/Library/Fonts/Supplemental/Andale Mono.ttf",   # macOS
    "/System/Library/Fonts/Supplemental/Courier New.ttf",   # macOS
]

HISTORY_SHOWN = 12  # Verlaufspunkte im Sparkline-Streifen


class EpaperRendererPlaceholder(Renderer):
    def __init__(self, display: DisplayConfig) -> None:
        self.display = display
        # TODO(waveshare): Hier später den echten Treiber initialisieren, z.B.:
        #   from waveshare_epd import epd2in13b_V4
        #   self.epd = epd2in13b_V4.EPD()
        #   self.epd.init()
        self.epd = None  # Platzhalter für das echte EPD-Objekt.

    def render(self, dashboard: Dashboard) -> None:
        # E-Ink schonen: nur neu zeichnen, wenn sich der Status geändert hat.
        if self.display.redraw_only_on_change and not dashboard.changed:
            logger.info(
                "E-Paper: Status unverändert (%s) – kein Refresh.",
                dashboard.overall.value,
            )
            return

        logger.info(
            "EpaperRendererPlaceholder: rendere Dashboard (overall=%s) "
            "auf virtuelles Display %dx%d (rotation=%d).",
            dashboard.overall.value,
            self.display.width,
            self.display.height,
            self.display.rotation,
        )

        if not _HAS_PIL:
            logger.warning(
                "Pillow ist nicht installiert – es wird kein Bild erzeugt. "
                "Installiere es mit: pip install Pillow"
            )
            self._log_textual(dashboard)
            return

        image = self._build_image(dashboard)

        # Optionale Vorschau als PNG speichern (praktisch zum Entwickeln ohne
        # Hardware). Pfad bewusst relativ zum Arbeitsverzeichnis.
        try:
            image.save("epaper_preview.png")
            logger.info("Vorschau gespeichert: epaper_preview.png")
        except OSError as exc:
            logger.warning("Vorschau konnte nicht gespeichert werden: %s", exc)

        # TODO(waveshare): Hier das Bild an das echte Display senden. Bei einem
        # B/W/R-Display teilt man üblicherweise in zwei 1-Bit-Buffer auf:
        #   black_image = <maske der schwarzen Pixel>
        #   red_image   = <maske der roten Pixel>
        #   self.epd.display(self.epd.getbuffer(black_image),
        #                    self.epd.getbuffer(red_image))
        #   self.epd.sleep()   # Display in den Schlafmodus -> schont das Panel
        logger.debug("Platzhalter: Bild würde jetzt an das E-Paper gesendet.")

    # ------------------------------------------------------------------ #
    # Bildaufbau (hardwareunabhängig)
    # ------------------------------------------------------------------ #
    def _build_image(self, dashboard: Dashboard) -> "Image.Image":
        w, h = self.display.width, self.display.height
        now = dashboard.timestamp
        image = Image.new("RGB", (w, h), WHITE)
        draw = ImageDraw.Draw(image)

        hosts = dashboard.sorted_for_display(dashboard.hosts)
        services = dashboard.sorted_for_display(dashboard.services)

        # Zeilenhöhe an Displayhöhe + Anzahl Zeilen koppeln.
        n_rows = len(hosts) + len(services)
        approx_lines = n_rows + 5  # Banner + SERVICES + Summary + System + OVERALL
        line_h = max(9, min(15, (h - 2) // max(approx_lines, 1)))
        font = self._load_font(max(8, line_h - 2))
        gly = max(6, line_h - 4)  # Kantenlänge der Status-Glyphe

        x_glyph = 2
        x_name = x_glyph + gly + 4
        name_w = max(
            (self._text_w(draw, r.name, font) for r in hosts + services),
            default=self._text_w(draw, "NAME", font),
        )
        x_detail = x_name + name_w + 6
        spark_w = HISTORY_SHOWN * 2

        y = 1
        bottom_reserved = line_h * 2  # Summary/System + OVERALL unten

        # --- Kopf: Alarm-Banner (rot) oder Titel --------------------------- #
        ok, warn, err = dashboard.counts
        if err:
            word = "DIENST" if err == 1 else "DIENSTE"
            draw.rectangle([0, 0, w, line_h + 1], fill=RED)
            draw.text((x_glyph, y), f"{err} {word} DOWN", font=font, fill=WHITE)
        else:
            title = "HOMELAB STATUS" if not warn else f"HOMELAB  {warn} WARN"
            draw.text((x_glyph, y), title, font=font, fill=BLACK)
        y += line_h + 2

        def draw_rows(rows: List[CheckResult]) -> None:
            nonlocal y
            for r in rows:
                if y > h - bottom_reserved - line_h:
                    break
                color = RED if r.status is Status.ERROR else BLACK
                self._draw_glyph(draw, x_glyph, y, gly, r.status)
                draw.text((x_name, y), r.name, font=font, fill=color)
                draw.text((x_detail, y), self._detail(r, now), font=font, fill=color)
                self._draw_sparkline(draw, w - spark_w - 1, y, r, line_h)
                y += line_h

        draw_rows(hosts)
        if services and y <= h - bottom_reserved - line_h:
            draw.text((x_name, y), "SERVICES", font=font, fill=BLACK)
            y += line_h
            draw_rows(services)

        # --- Fuß: Zusammenfassung + Pi-Status, OVERALL ganz unten ---------- #
        sys_line = dashboard.system[0].message if dashboard.system else ""
        foot = dashboard.summary_line
        if sys_line:
            foot = f"{foot}   {sys_line}"
        draw.text((x_glyph, h - line_h * 2), foot, font=font, fill=BLACK)

        overall = dashboard.overall
        overall_color = RED if overall is Status.ERROR else BLACK
        draw.text(
            (x_glyph, h - line_h),
            f"OVERALL: {overall.tag}   {now.strftime('%H:%M')}",
            font=font,
            fill=overall_color,
        )

        if self.display.rotation:
            image = image.rotate(self.display.rotation, expand=True)
        return image

    # ------------------------------------------------------------------ #
    @staticmethod
    def _draw_glyph(draw, x: int, y: int, size: int, status: Status) -> None:
        """Status-Glyphe: gefüllt schwarz=ok, hohl=warn, gefüllt rot=fail."""
        box = [x, y + 1, x + size, y + size + 1]
        if status is Status.OK:
            draw.rectangle(box, fill=BLACK)
        elif status is Status.WARN:
            draw.rectangle(box, outline=BLACK, width=2)
        else:  # ERROR
            draw.rectangle(box, fill=RED)

    def _draw_sparkline(
        self, draw, x: int, y: int, result: CheckResult, line_h: int
    ) -> None:
        """Kleiner Verlaufsstreifen: ok = kurzer schwarzer Strich (oben),
        Fehler = roter Strich (volle Höhe)."""
        if not result.history:
            return
        recent = result.history[-HISTORY_SHOWN:]
        top = y + 1
        bot = y + line_h - 2
        for i, ok in enumerate(recent):
            px = x + i * 2
            if ok:
                draw.line([px, bot - 2, px, bot], fill=BLACK, width=1)
            else:
                draw.line([px, top, px, bot], fill=RED, width=1)

    @staticmethod
    def _text_w(draw, text: str, font) -> int:
        return int(round(draw.textlength(text, font=font)))

    @staticmethod
    def _detail(result: CheckResult, now: datetime) -> str:
        if result.status is Status.ERROR:
            return result.down_for_str(now) or (result.message or "Fehler")
        if result.response_time_ms is not None:
            return result.response_time_str
        return ""

    @staticmethod
    def _load_font(size: int):
        # Monospace-TTF bevorzugen (scharf + bündig). Wenn keiner gefunden wird,
        # auf Pillows Default-Bitmap-Font zurückfallen – immer verfügbar.
        # TODO(waveshare): Bei Bedarf einen eigenen Font-Pfad ergänzen, z.B.
        #   CANDIDATE_FONTS.insert(0, "/pfad/zu/deinem/Font.ttf")
        for path in CANDIDATE_FONTS:
            try:
                return ImageFont.truetype(path, size)
            except (OSError, ValueError):
                continue
        logger.debug("Kein TTF-Font gefunden – nutze Pillow-Default.")
        return ImageFont.load_default()

    @staticmethod
    def _log_textual(dashboard: Dashboard) -> None:
        for r in dashboard.all_results:
            logger.info("%s %s %s", r.name, r.status.tag, r.message)
