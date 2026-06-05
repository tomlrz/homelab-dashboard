"""Gemeinsame Zeichen-Logik für E-Paper (schwarz/weiß/rot).

Hier wird das Dashboard EINMAL gezeichnet – in zwei 1-Bit-Buffer (schwarz + rot),
genau wie es ein Waveshare-B/W/R-Panel erwartet:
    epd.display(epd.getbuffer(black), epd.getbuffer(red))

Aus denselben Buffern lässt sich auch ein RGB-Vorschaubild zusammensetzen. Damit
ist die PNG-Vorschau (Placeholder) pixelgleich zu dem, was später auf dem Display
landet. Das Layout skaliert mit der Auflösung – funktioniert also für ein kleines
2,13″ (250×122) genauso wie für ein 7,5″ (800×480).

Buffer-Konvention (Waveshare): 0 = Farbe (Tinte), 255 = Papierweiß/leer.
Ein Pixel darf nicht gleichzeitig in Schwarz UND Rot gesetzt sein.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from models import CheckResult, Dashboard, Status

logger = logging.getLogger(__name__)

# Vorschau-Farben (nur fürs RGB-Preview; das Panel nutzt echte Tinte).
PREVIEW_BLACK = (0, 0, 0)
PREVIEW_RED = (200, 0, 0)
PREVIEW_WHITE = (255, 255, 255)

# Monospace-TTF bevorzugt (bündig + scharf auf E-Ink). Raspberry Pi OS zuerst.
CANDIDATE_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",  # Raspberry Pi OS
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Menlo.ttc",                      # macOS (Entwicklung)
    "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
]

HISTORY_SHOWN = 12  # Verlaufspunkte im Sparkline-Streifen


class BWRCanvas:
    """Zeichenfläche mit getrenntem Schwarz- und Rot-Layer (je 1-Bit)."""

    def __init__(self, width: int, height: int) -> None:
        self.w, self.h = width, height
        self.black = Image.new("1", (width, height), 255)
        self.red = Image.new("1", (width, height), 255)
        self._bd = ImageDraw.Draw(self.black)
        self._rd = ImageDraw.Draw(self.red)

    def _layer(self, color: str) -> "ImageDraw.ImageDraw":
        return self._rd if color == "red" else self._bd

    def text(self, xy, s: str, font, color: str) -> None:
        if color == "white":
            # "Weiß" = Tinte wieder rausnehmen (z.B. weiße Schrift auf rotem
            # Banner): in BEIDEN Layern auf 255 setzen.
            self._rd.text(xy, s, font=font, fill=255)
            self._bd.text(xy, s, font=font, fill=255)
        else:
            self._layer(color).text(xy, s, font=font, fill=0)

    def rect(self, box, color: str, fill: bool = True, width: int = 1) -> None:
        d = self._layer(color)
        if fill:
            d.rectangle(box, fill=0)
        else:
            d.rectangle(box, outline=0, width=width)

    def line(self, xy, color: str, width: int = 1) -> None:
        self._layer(color).line(xy, fill=0, width=width)

    def textlength(self, s: str, font) -> int:
        return int(round(self._bd.textlength(s, font=font)))


def compose_rgb(black: "Image.Image", red: "Image.Image") -> "Image.Image":
    """Setzt die beiden 1-Bit-Buffer zu einem RGB-Vorschaubild zusammen."""
    rgb = Image.new("RGB", black.size, PREVIEW_WHITE)
    black_mask = black.point(lambda p: 255 if p == 0 else 0).convert("1")
    rgb.paste(PREVIEW_BLACK, mask=black_mask)
    red_mask = red.point(lambda p: 255 if p == 0 else 0).convert("1")
    rgb.paste(PREVIEW_RED, mask=red_mask)
    return rgb


def render_oriented(
    width: int,
    height: int,
    dashboard: Dashboard,
    rotation: int = 0,
    margins: Tuple[int, int, int, int] = (0, 0, 0, 0),
    show_border: bool = False,
) -> Tuple["Image.Image", "Image.Image"]:
    """Zeichnet das Dashboard und liefert (black, red) in Zielgröße (width×height).

    Bei 90/270° wird intern in der gedrehten Größe gezeichnet und dann rotiert,
    damit die Buffer am Ende exakt width×height groß sind."""
    rot = rotation % 360
    if rot in (90, 270):
        draw_w, draw_h = height, width
    else:
        draw_w, draw_h = width, height

    canvas = _render(draw_w, draw_h, dashboard, margins, show_border)
    black, red = canvas.black, canvas.red
    if rot:
        black = black.rotate(rot, expand=True)
        red = red.rotate(rot, expand=True)
    return black, red


# --------------------------------------------------------------------------- #
# Eigentliches Layout
# --------------------------------------------------------------------------- #
def _render(
    width: int,
    height: int,
    dashboard: Dashboard,
    margins: Tuple[int, int, int, int] = (0, 0, 0, 0),
    show_border: bool = False,
) -> BWRCanvas:
    now = dashboard.timestamp
    c = BWRCanvas(width, height)

    # Safe-Area: Inhalt nur innerhalb der Ränder zeichnen (Bilderrahmen verdeckt
    # ggf. die Kanten). ox/oy = obere linke Ecke, right/bottom = innere Grenzen.
    mt, mr, mb, ml = margins
    ox, oy = ml, mt
    right = max(ox + 20, width - mr)
    bottom = max(oy + 20, height - mb)

    hosts = dashboard.sorted_for_display(dashboard.hosts)
    services = dashboard.sorted_for_display(dashboard.services)
    rows = hosts + services

    # Zeilenhöhe/Schrift an die nutzbare Höhe koppeln (skaliert klein -> groß).
    # Logische Zeilen genau zählen, damit keine Zeile abgeschnitten wird:
    # Banner + Hosts + (SERVICES) + Services + (PI) + Summary + OVERALL + Reserve.
    usable_h = bottom - oy
    n_rows = len(rows)
    has_services = bool(services)
    has_sys = bool(dashboard.system)
    extra = 1 + (1 if has_services else 0) + (1 if has_sys else 0) + 2 + 1
    approx_lines = n_rows + extra
    line_h = max(10, min(70, usable_h // max(approx_lines, 1)))
    font = _load_font(int(line_h * 0.72))
    small = _load_font(int(line_h * 0.60))
    gly = int(line_h * 0.6)
    pad = max(2, line_h // 6)

    if show_border:
        # Kalibrier-Rahmen genau an der Safe-Area-Grenze.
        c.rect([ox, oy, right - 1, bottom - 1], "black", fill=False, width=2)

    x_glyph = ox
    x_name = x_glyph + gly + pad * 2
    name_w = max(
        (c.textlength(r.name, font) for r in rows),
        default=c.textlength("NAME", font),
    )
    x_detail = x_name + name_w + pad * 3
    spark_pw = max(2, line_h // 8)
    spark_w = HISTORY_SHOWN * spark_pw

    y = oy
    # Fußzeilen unten reservieren: ggf. Pi-Status + Summary + OVERALL.
    foot_lines = 3 if has_sys else 2
    bottom_reserved = line_h * foot_lines + pad

    # --- Kopf: roter Alarm-Banner oder Titel -------------------------------- #
    ok, warn, err = dashboard.counts
    if err:
        word = "DIENST" if err == 1 else "DIENSTE"
        # Banner innerhalb der Safe-Area (damit der Text garantiert sichtbar ist).
        c.rect([ox, oy, right, oy + line_h + pad], "red", fill=True)
        c.text((x_glyph + pad, y), f"{err} {word} DOWN", font, "white")
    else:
        title = "HOMELAB STATUS" if not warn else f"HOMELAB  {warn} WARN"
        c.text((x_glyph, y), title, font, "black")
    y += line_h + pad * 2

    def draw_rows(items: List[CheckResult]) -> None:
        nonlocal y
        for r in items:
            if y > bottom - bottom_reserved - line_h:
                break
            color = "red" if r.status is Status.ERROR else "black"
            _draw_glyph(c, x_glyph, y, gly, r.status, pad)
            c.text((x_name, y), r.name, font, color)
            c.text((x_detail, y), _detail(r, now), font, color)
            _draw_spark(c, right - spark_w, y, r, line_h, spark_pw)
            y += line_h

    draw_rows(hosts)
    if services and y <= bottom - bottom_reserved - line_h:
        c.text((x_name, y), "SERVICES", font, "black")
        y += line_h
        draw_rows(services)

    # --- Fuß: (Pi-Status) + Zusammenfassung + OVERALL, jeweils eigene Zeile -- #
    if has_sys:
        c.text(
            (x_glyph, bottom - line_h * 3),
            f"{dashboard.system[0].name} {dashboard.system[0].message}",
            small,
            "black",
        )
    c.text((x_glyph, bottom - line_h * 2), dashboard.summary_line, small, "black")

    overall = dashboard.overall
    oc = "red" if overall is Status.ERROR else "black"
    c.text(
        (x_glyph, bottom - line_h),
        f"OVERALL: {overall.tag}   {now.strftime('%H:%M')}",
        font,
        oc,
    )
    return c


def _draw_glyph(c: BWRCanvas, x: int, y: int, size: int, status: Status, pad: int) -> None:
    box = [x, y + pad, x + size, y + size + pad]
    if status is Status.OK:
        c.rect(box, "black", fill=True)
    elif status is Status.WARN:
        c.rect(box, "black", fill=False, width=max(2, size // 6))
    else:  # ERROR
        c.rect(box, "red", fill=True)


def _draw_spark(
    c: BWRCanvas, x: int, y: int, result: CheckResult, line_h: int, pw: int
) -> None:
    if not result.history:
        return
    recent = result.history[-HISTORY_SHOWN:]
    top = y + 2
    bot = y + line_h - 3
    lw = max(1, pw - 1)
    for i, is_ok in enumerate(recent):
        px = x + i * pw
        if is_ok:
            c.line([px, bot - max(2, line_h // 5), px, bot], "black", width=lw)
        else:
            c.line([px, top, px, bot], "red", width=lw)


def _detail(result: CheckResult, now: datetime) -> str:
    if result.status is Status.ERROR:
        return result.down_for_str(now) or (result.message or "Fehler")
    if result.response_time_ms is not None:
        return result.response_time_str
    return ""


def _load_font(size: int):
    for path in CANDIDATE_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
    logger.debug("Kein TTF-Font gefunden – nutze Pillow-Default.")
    return ImageFont.load_default()
