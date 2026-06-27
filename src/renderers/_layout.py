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

from models import CheckResult, Dashboard, SidePanel, Status

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

    def ellipse(self, box, color: str, fill: bool = True, width: int = 1) -> None:
        d = self._layer(color)
        if fill:
            d.ellipse(box, fill=0)
        else:
            d.ellipse(box, outline=0, width=width)

    def arc(self, box, start: float, end: float, color: str, width: int = 1) -> None:
        self._layer(color).arc(box, start=start, end=end, fill=0, width=width)

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
    show_services_header: bool = True,
    show_status_face: bool = False,
) -> Tuple["Image.Image", "Image.Image"]:
    """Zeichnet das Dashboard und liefert (black, red) in Zielgröße (width×height).

    Bei 90/270° wird intern in der gedrehten Größe gezeichnet und dann rotiert,
    damit die Buffer am Ende exakt width×height groß sind."""
    rot = rotation % 360
    if rot in (90, 270):
        draw_w, draw_h = height, width
    else:
        draw_w, draw_h = width, height

    canvas = _render(
        draw_w,
        draw_h,
        dashboard,
        margins,
        show_border,
        show_services_header,
        show_status_face,
    )
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
    show_services_header: bool = True,
    show_status_face: bool = False,
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
    show_header = has_services and show_services_header
    extra = 1 + (1 if show_header else 0) + (1 if has_sys else 0) + 2 + 1
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

    # Sitzt rechts ein Panel? Dann dürfen die Zeilen nicht hineinragen.
    if dashboard.side_panel is not None:
        panel_x0 = ox + int((right - ox) * 0.52) + pad * 2
        rows_right = panel_x0 - pad * 2
    else:
        panel_x0 = right
        rows_right = right

    y = oy
    # Fußzeilen unten reservieren: ggf. Pi-Status + Summary + OVERALL.
    foot_lines = 3 if has_sys else 2
    bottom_reserved = line_h * foot_lines + pad

    # --- Kopf: roter Alarm-Banner oder Titel -------------------------------- #
    ok, warn, err = dashboard.counts
    loud = dashboard.loud_count
    if err:
        word = "DIENST" if err == 1 else "DIENSTE"
        # Banner innerhalb der Safe-Area (damit der Text garantiert sichtbar ist).
        c.rect([ox, oy, right, oy + line_h + pad], "red", fill=True)
        c.text((x_glyph + pad, y), f"{err} {word} DOWN", font, "white")
    elif loud:
        word = "DIENST" if loud == 1 else "DIENSTE"
        c.rect([ox, oy, right, oy + line_h + pad], "red", fill=True)
        c.text((x_glyph + pad, y), f"{loud} {word} GESTOERT", font, "white")
    else:
        title = "HOMELAB STATUS" if not warn else f"HOMELAB  {warn} WARN"
        c.text((x_glyph, y), title, font, "black")
    y += line_h + pad * 2

    draw_sparks = dashboard.side_panel is None  # Panel ersetzt die Sparklines

    def draw_rows(items: List[CheckResult]) -> None:
        nonlocal y
        for r in items:
            if y > bottom - bottom_reserved - line_h:
                break
            is_red = r.status is Status.ERROR or (
                r.status is Status.WARN and r.loud
            )
            color = "red" if is_red else "black"
            _draw_glyph(c, x_glyph, y, gly, r.status, pad, r.loud)
            c.text((x_name, y), r.name, font, color)
            detail = _clip(c, _detail(r, now), font, rows_right - x_detail)
            c.text((x_detail, y), detail, font, color)
            if draw_sparks:
                _draw_spark(c, right - spark_w, y, r, line_h, spark_pw)
            y += line_h

    draw_rows(hosts)
    if services:
        if show_header and y <= bottom - bottom_reserved - line_h:
            c.text((x_name, y), "SERVICES", font, "black")
            y += line_h
        draw_rows(services)

    # --- Optionales Status-Gesicht in der rechten freien Hälfte ------------- #
    if show_status_face:
        face_x0 = ox + int((right - ox) * 0.55)
        face_x1 = right - spark_w - pad * 2
        face_y0 = oy + line_h * 2
        face_y1 = bottom - bottom_reserved - pad
        if face_x1 - face_x0 > 40 and face_y1 - face_y0 > 40:
            _draw_face(c, dashboard.overall, face_x0, face_y0, face_x1, face_y1)

    # --- Optionales rechtes Info-Panel (Counter + Witz/Tech-History) -------- #
    if dashboard.side_panel is not None:
        p_x0 = panel_x0
        p_x1 = right
        p_y0 = oy + line_h
        p_y1 = bottom - pad  # rechte Spalte hat keine Fußzeile -> volle Höhe
        if p_x1 - p_x0 > 60 and p_y1 - p_y0 > 80:
            _draw_side_panel(c, dashboard.side_panel, p_x0, p_y0, p_x1, p_y1)

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


def _draw_face(
    c: BWRCanvas, status: Status, x0: int, y0: int, x1: int, y1: int
) -> None:
    """Großes Status-Gesicht: grinst (ok), neutral (warn), rotes X-Augen-
    Gesicht (error). Reagiert auf den Gesamtstatus."""
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    r = min(x1 - x0, y1 - y0) / 2.0 * 0.9
    color = "red" if status is Status.ERROR else "black"
    lw = max(3, int(r * 0.06))

    # Kopf
    c.ellipse([cx - r, cy - r, cx + r, cy + r], color, fill=False, width=lw)

    # Augen
    ex, ey = r * 0.40, r * 0.28
    er = max(2, int(r * 0.10))
    for sx in (-1, 1):
        ecx = cx + sx * ex
        ecy = cy - ey
        if status is Status.ERROR:
            # X-Augen ("oh nein")
            c.line([ecx - er, ecy - er, ecx + er, ecy + er], color, width=lw)
            c.line([ecx - er, ecy + er, ecx + er, ecy - er], color, width=lw)
        else:
            c.ellipse([ecx - er, ecy - er, ecx + er, ecy + er], color, fill=True)

    # Mund
    mw = r * 0.5
    if status is Status.OK:
        # Lächeln (untere Kurve)
        c.arc([cx - mw, cy - r * 0.10, cx + mw, cy + r * 0.70], 20, 160, color, lw)
    elif status is Status.WARN:
        # neutraler Strich
        c.line([cx - mw, cy + r * 0.35, cx + mw, cy + r * 0.35], color, width=lw)
    else:
        # trauriger Mund (obere Kurve)
        c.arc([cx - mw, cy + r * 0.10, cx + mw, cy + r * 0.90], 200, 340, color, lw)


def _clip(c: BWRCanvas, text: str, font, max_w: int) -> str:
    """Kürzt Text mit … auf die Pixelbreite (verhindert Überlappen)."""
    if max_w <= 0 or not text or c.textlength(text, font) <= max_w:
        return text
    while text and c.textlength(text + "…", font) > max_w:
        text = text[:-1]
    return (text + "…") if text else ""


def _wrap(c: BWRCanvas, text: str, font, max_w: int) -> List[str]:
    """Bricht Text auf max. Pixelbreite um (wortweise)."""
    out: List[str] = []
    cur = ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if c.textlength(t, font) <= max_w or not cur:
            cur = t
        else:
            out.append(cur)
            cur = word
    if cur:
        out.append(cur)
    return out


def _draw_side_panel(
    c: BWRCanvas, panel: SidePanel, x0: int, y0: int, x1: int, y1: int
) -> None:
    """Rechtes Panel: großer 'Tage ohne Ausfall'-Counter + Witz/Tech-History."""
    w = x1 - x0
    ph = y1 - y0
    # Trennlinie zum Dashboard links
    div = x0 - max(8, w // 16)
    c.line([div, y0, div, y1], "black", width=2)

    f_lab = _load_font(max(12, int(ph * 0.072)))
    f_big = _load_font(max(40, int(ph * 0.25)))
    f_h = _load_font(max(11, int(ph * 0.052)))

    y = y0
    c.text((x0, y), "TAGE OHNE", f_lab, "black")
    y += int(ph * 0.082)
    c.text((x0, y), "AUSFALL", f_lab, "black")
    y += int(ph * 0.090)

    col = "red" if panel.incident_now else "black"
    c.text((x0, y), str(panel.days_without_incident), f_big, col)
    y += int(ph * 0.28)

    c.line([x0, y, x1, y], "black", width=2)
    y += int(ph * 0.035)

    for ln in _wrap(c, panel.header, f_h, w):
        c.text((x0, y), ln, f_h, "black")
        y += int(ph * 0.055)
    y += int(ph * 0.015)

    # Body-Schrift automatisch so wählen, dass Witz/Fakt komplett in die
    # Resthöhe passt (variable Textlängen -> nie abgeschnitten).
    avail = y1 - y
    max_bs = max(13, int(ph * 0.072))
    font, lines, step = _load_font(13), [], 16
    for bs in range(max_bs, 11, -1):
        f = _load_font(bs)
        wrapped = _wrap(c, panel.body, f, w)
        st = int(bs * 1.18)
        if len(wrapped) * st <= avail:
            font, lines, step = f, wrapped, st
            break
    else:
        font = _load_font(12)
        lines = _wrap(c, panel.body, font, w)
        step = 14
    for ln in lines:
        if y > y1 - step:
            break
        c.text((x0, y), ln, font, "black")
        y += step


def _draw_glyph(
    c: BWRCanvas, x: int, y: int, size: int, status: Status, pad: int,
    loud: bool = False,
) -> None:
    box = [x, y + pad, x + size, y + size + pad]
    if status is Status.OK:
        c.rect(box, "black", fill=True)
    elif status is Status.WARN:
        # auffällige WARN: rotes, kräftiges Kästchen; stille WARN: schwarz dünn.
        if loud:
            c.rect(box, "red", fill=False, width=max(3, size // 4))
        else:
            c.rect(box, "black", fill=False, width=max(2, size // 6))
    elif status is Status.OFF:
        # Strich = deaktiviert/pausiert
        mid = y + pad + size // 2
        c.line([x, mid, x + size, mid], "black", width=max(2, size // 6))
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
    if result.status is Status.OFF:
        return "off"
    if result.status is Status.ERROR:
        return result.down_for_str(now) or (result.message or "Fehler")
    if result.response_time_ms is not None:
        return result.response_time_str
    # WARN ohne Antwortzeit (z.B. "seit 2h weg" / "instabil (12m)")
    return result.message or ""


def _load_font(size: int):
    for path in CANDIDATE_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
    logger.debug("Kein TTF-Font gefunden – nutze Pillow-Default.")
    return ImageFont.load_default()
