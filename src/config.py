"""Laden und Validieren der YAML-Konfiguration.

Die Konfiguration wird in typisierte Dataclasses überführt, damit der restliche
Code nicht mit rohen Dicts arbeiten muss und Tippfehler früh auffallen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml

logger = logging.getLogger(__name__)

# Erlaubte Check-Typen
VALID_TYPES = {"http", "https", "tcp", "ping"}
VALID_RENDERERS = {"text", "epaper_placeholder", "epaper"}
VALID_PROVIDERS = {"none", "ntfy", "telegram", "gotify"}


@dataclass
class Target:
    """Ein einzelnes Prüfziel (Host oder Dienst).

    Je nach `type` werden unterschiedliche Felder benötigt:
      - http/https : `url`
      - tcp        : `host` + `port`
      - ping       : `host` (optional `port` als TCP-Fallback)
    """

    name: str
    type: str = "tcp"
    url: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    timeout: float = 5.0
    # Erwarteter HTTP-Statusbereich (inklusive). Default: 200-399 = "alles ok".
    expected_status_min: int = 200
    expected_status_max: int = 399
    # SSL-Zertifikat prüfen? Bei self-signed Zertifikaten auf false setzen.
    verify_ssl: bool = True
    # Antwortzeit-Warnschwelle in ms: darüber -> WARN (None = aus).
    warn_response_ms: Optional[int] = None
    # Optionaler Host-Header (vhost): nötig, wenn ein Reverse Proxy / Nextcloud
    # trusted_domains den Zugriff per nackter IP mit 400 ablehnt. Dann hier den
    # echten Hostnamen eintragen, unter dem der Dienst erreichbar ist.
    host_header: Optional[str] = None


@dataclass
class DisplayConfig:
    renderer: str = "text"
    rotation: int = 0
    width: int = 250
    height: int = 122
    # E-Ink nur neu zeichnen, wenn sich der Status geändert hat (schont Panel).
    redraw_only_on_change: bool = True
    # Heartbeat: spätestens nach so vielen Minuten einmal neu zeichnen, auch ohne
    # Statuswechsel. So bleibt die angezeigte Uhrzeit aktuell -> man erkennt, dass
    # das System noch läuft (eine alte Uhrzeit = es hängt). 0 = aus.
    heartbeat_minutes: int = 60
    # Nur für renderer: "epaper" – echtes Waveshare-Display:
    #   epd_model: z.B. "epd7in5b_V2" (muss zu deinem Panel passen)
    #   waveshare_lib_path: Ordner, der das Paket "waveshare_epd" enthält
    epd_model: Optional[str] = None
    waveshare_lib_path: Optional[str] = None
    # Sicherheitsrand in Pixeln, falls ein Bilderrahmen/Passepartout die Ränder
    # verdeckt. `margin` gilt für alle Seiten; einzelne Seiten überschreibbar.
    margin: int = 0
    margin_top: Optional[int] = None
    margin_right: Optional[int] = None
    margin_bottom: Optional[int] = None
    margin_left: Optional[int] = None
    # Zum Einstellen: dünnen Rahmen an der Safe-Area-Grenze zeichnen. Margin so
    # hochdrehen, bis der Rahmen rundum sichtbar im Bilderrahmen sitzt.
    show_safe_border: bool = False
    # "SERVICES"-Zwischenüberschrift anzeigen (false = eine durchgehende Liste).
    show_services_header: bool = True
    # Großes Status-Gesicht rechts: grinst bei ok, neutral bei warn, rotes
    # X-Augen-Gesicht bei Fehler. Füllt die rechte Hälfte mit Leben. :)
    show_status_face: bool = False

    def margins(self) -> Tuple[int, int, int, int]:
        """(top, right, bottom, left) – Einzelseiten überschreiben `margin`."""
        m = self.margin
        return (
            self.margin_top if self.margin_top is not None else m,
            self.margin_right if self.margin_right is not None else m,
            self.margin_bottom if self.margin_bottom is not None else m,
            self.margin_left if self.margin_left is not None else m,
        )


@dataclass
class NotifyConfig:
    """Push-Benachrichtigung bei Statuswechsel."""

    provider: str = "none"  # none | ntfy | telegram | gotify
    # Wann benachrichtigen? "error" = wenn etwas ausfällt, "recovery" = wieder ok.
    notify_on: List[str] = field(default_factory=lambda: ["error", "recovery"])
    # ntfy
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: Optional[str] = None
    # telegram
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    # gotify
    gotify_url: Optional[str] = None
    gotify_token: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return self.provider != "none"


@dataclass
class MonitoringConfig:
    """Verhalten der Auswertung (Robustheit/Zustand)."""

    # Erst nach so vielen aufeinanderfolgenden Fehlschlägen wirklich FAIL melden
    # (Flapping-Schutz). 1 = sofort.
    failure_threshold: int = 2
    # Länge des angezeigten Verlaufsstreifens.
    history_length: int = 20
    # Zustandsdatei (down-seit, Verlauf, letzte Signatur).
    state_file: str = "state.json"
    # Checks parallel ausführen (schneller bei vielen Diensten/Timeouts).
    parallel: bool = True
    max_workers: int = 8
    # Pi-Eigenstatus (CPU-Temp, Unterspannung, WLAN, Uptime) anzeigen.
    show_system: bool = True


@dataclass
class AppConfig:
    update_interval_seconds: int = 300
    nas: Optional[Target] = None
    proxmox: Optional[Target] = None
    services: List[Target] = field(default_factory=list)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)


class ConfigError(Exception):
    """Wird geworfen, wenn die Konfiguration ungültig ist."""


def _parse_expected_status(
    value: Union[int, List[int], Tuple[int, int], None],
) -> Tuple[int, int]:
    """Akzeptiert `200`, `[200, 399]` oder `null` und liefert (min, max)."""
    if value is None:
        return (200, 399)
    if isinstance(value, int):
        return (value, value)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        lo, hi = int(value[0]), int(value[1])
        return (min(lo, hi), max(lo, hi))
    raise ConfigError(
        f"expected_status muss int oder [min, max] sein, war: {value!r}"
    )


def _parse_target(raw: Dict[str, Any], default_name: str = "") -> Target:
    if not isinstance(raw, dict):
        raise ConfigError(f"Target muss ein Mapping sein, war: {raw!r}")

    name = str(raw.get("name", default_name) or default_name)
    if not name:
        raise ConfigError(f"Target ohne 'name': {raw!r}")

    type_ = str(raw.get("type", "tcp")).lower()
    if type_ not in VALID_TYPES:
        raise ConfigError(
            f"Unbekannter type '{type_}' für '{name}'. "
            f"Erlaubt: {', '.join(sorted(VALID_TYPES))}"
        )

    lo, hi = _parse_expected_status(raw.get("expected_status"))
    warn_ms = raw.get("warn_response_ms")

    target = Target(
        name=name,
        type=type_,
        url=raw.get("url"),
        host=raw.get("host"),
        port=int(raw["port"]) if raw.get("port") is not None else None,
        timeout=float(raw.get("timeout", 5.0)),
        expected_status_min=lo,
        expected_status_max=hi,
        verify_ssl=bool(raw.get("verify_ssl", True)),
        warn_response_ms=int(warn_ms) if warn_ms is not None else None,
        host_header=raw.get("host_header"),
    )
    _validate_target(target)
    return target


def _validate_target(t: Target) -> None:
    if t.type in ("http", "https"):
        if not t.url:
            raise ConfigError(f"'{t.name}': type={t.type} benötigt eine 'url'.")
    elif t.type == "tcp":
        if not t.host or t.port is None:
            raise ConfigError(f"'{t.name}': type=tcp benötigt 'host' und 'port'.")
    elif t.type == "ping":
        if not t.host:
            raise ConfigError(f"'{t.name}': type=ping benötigt 'host'.")


def _opt_int(value: Any) -> Optional[int]:
    return int(value) if value is not None else None


def _parse_display(raw: Optional[Dict[str, Any]]) -> DisplayConfig:
    raw = raw or {}
    renderer = str(raw.get("renderer", "text")).lower()
    if renderer not in VALID_RENDERERS:
        raise ConfigError(
            f"Unbekannter renderer '{renderer}'. "
            f"Erlaubt: {', '.join(sorted(VALID_RENDERERS))}"
        )
    cfg = DisplayConfig(
        renderer=renderer,
        rotation=int(raw.get("rotation", 0)),
        width=int(raw.get("width", 250)),
        height=int(raw.get("height", 122)),
        redraw_only_on_change=bool(raw.get("redraw_only_on_change", True)),
        heartbeat_minutes=max(0, int(raw.get("heartbeat_minutes", 60))),
        epd_model=raw.get("epd_model"),
        waveshare_lib_path=raw.get("waveshare_lib_path"),
        margin=int(raw.get("margin", 0)),
        margin_top=_opt_int(raw.get("margin_top")),
        margin_right=_opt_int(raw.get("margin_right")),
        margin_bottom=_opt_int(raw.get("margin_bottom")),
        margin_left=_opt_int(raw.get("margin_left")),
        show_safe_border=bool(raw.get("show_safe_border", False)),
        show_services_header=bool(raw.get("show_services_header", True)),
        show_status_face=bool(raw.get("show_status_face", False)),
    )
    if renderer == "epaper" and not cfg.epd_model:
        raise ConfigError(
            "display.renderer='epaper' benötigt 'epd_model' (z.B. epd7in5b_V2)."
        )
    return cfg


def _parse_monitoring(raw: Optional[Dict[str, Any]]) -> MonitoringConfig:
    raw = raw or {}
    return MonitoringConfig(
        failure_threshold=max(1, int(raw.get("failure_threshold", 2))),
        history_length=max(1, int(raw.get("history_length", 20))),
        state_file=str(raw.get("state_file", "state.json")),
        parallel=bool(raw.get("parallel", True)),
        max_workers=max(1, int(raw.get("max_workers", 8))),
        show_system=bool(raw.get("show_system", True)),
    )


def _parse_notify(raw: Optional[Dict[str, Any]]) -> NotifyConfig:
    raw = raw or {}
    provider = str(raw.get("provider", "none")).lower()
    if provider not in VALID_PROVIDERS:
        raise ConfigError(
            f"Unbekannter notify.provider '{provider}'. "
            f"Erlaubt: {', '.join(sorted(VALID_PROVIDERS))}"
        )
    notify_on = raw.get("notify_on") or ["error", "recovery"]
    if not isinstance(notify_on, list):
        raise ConfigError("notify.notify_on muss eine Liste sein.")

    cfg = NotifyConfig(
        provider=provider,
        notify_on=[str(x).lower() for x in notify_on],
        ntfy_url=str(raw.get("ntfy_url", "https://ntfy.sh")),
        ntfy_topic=raw.get("ntfy_topic"),
        telegram_bot_token=raw.get("telegram_bot_token"),
        telegram_chat_id=(
            str(raw["telegram_chat_id"])
            if raw.get("telegram_chat_id") is not None
            else None
        ),
        gotify_url=raw.get("gotify_url"),
        gotify_token=raw.get("gotify_token"),
    )
    _validate_notify(cfg)
    return cfg


def _validate_notify(c: NotifyConfig) -> None:
    if c.provider == "ntfy" and not c.ntfy_topic:
        raise ConfigError("notify.provider=ntfy benötigt 'ntfy_topic'.")
    if c.provider == "telegram" and not (c.telegram_bot_token and c.telegram_chat_id):
        raise ConfigError(
            "notify.provider=telegram benötigt 'telegram_bot_token' und "
            "'telegram_chat_id'."
        )
    if c.provider == "gotify" and not (c.gotify_url and c.gotify_token):
        raise ConfigError(
            "notify.provider=gotify benötigt 'gotify_url' und 'gotify_token'."
        )


def load_config(path: Union[str, Path]) -> AppConfig:
    """Lädt die YAML-Konfiguration und gibt eine validierte AppConfig zurück.

    Raises:
        ConfigError: bei fehlender Datei oder ungültigem Inhalt.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"Konfigurationsdatei nicht gefunden: {path}. "
            f"Kopiere config.example.yaml nach config.yaml und passe sie an."
        )

    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML-Fehler in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Konfiguration muss ein Mapping sein, war: {type(raw)}")

    nas = _parse_target(raw["nas"], "NAS") if raw.get("nas") else None
    proxmox = _parse_target(raw["proxmox"], "PROXMOX") if raw.get("proxmox") else None

    services_raw = raw.get("services") or []
    if not isinstance(services_raw, list):
        raise ConfigError("'services' muss eine Liste sein.")
    services = [_parse_target(s) for s in services_raw]

    config = AppConfig(
        update_interval_seconds=int(raw.get("update_interval_seconds", 300)),
        nas=nas,
        proxmox=proxmox,
        services=services,
        display=_parse_display(raw.get("display")),
        monitoring=_parse_monitoring(raw.get("monitoring")),
        notify=_parse_notify(raw.get("notify")),
    )
    logger.debug(
        "Konfiguration geladen: nas=%s proxmox=%s services=%d renderer=%s notify=%s",
        bool(nas),
        bool(proxmox),
        len(services),
        config.display.renderer,
        config.notify.provider,
    )
    return config
