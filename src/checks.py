"""Check-Implementierungen.

Designprinzip: Robustheit vor Eleganz. Jeder Check fängt *alle* Fehler ab und
liefert immer ein CheckResult zurück – nie eine Exception nach außen. Selbst
wenn NAS oder Proxmox komplett weg sind, läuft das Dashboard weiter und kann
den Fehler anzeigen.
"""

from __future__ import annotations

import logging
import socket
import subprocess
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from config import Target
from models import CheckResult, Status

logger = logging.getLogger(__name__)

# requests wird nur für HTTP-Checks gebraucht. Import wird abgesichert, damit
# das Programm auch ohne installiertes requests startet (z.B. nur TCP/Ping).
try:
    import requests
    from requests.exceptions import RequestException

    # Wir nutzen bewusst verify_ssl: false für self-signed Zertifikate (z.B.
    # Proxmox). Die laute InsecureRequestWarning pro Request daher unterdrücken.
    try:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:  # pragma: no cover
        pass

    _HAS_REQUESTS = True
except Exception:  # pragma: no cover - nur im Fehlerfall relevant
    requests = None  # type: ignore[assignment]
    RequestException = Exception  # type: ignore[assignment,misc]
    _HAS_REQUESTS = False


def run_check(target: Target) -> CheckResult:
    """Führt den passenden Check für ein Target aus.

    Diese Funktion ist der einzige Einstiegspunkt von außen und garantiert,
    dass niemals eine Exception nach oben durchschlägt.
    """
    # Bewusst deaktivierte Dienste werden gar nicht erst geprüft.
    if not target.enabled:
        return CheckResult(
            name=target.name, status=Status.OFF, message="deaktiviert (Wartung)"
        )
    try:
        if target.type in ("http", "https"):
            return _check_http(target)
        if target.type == "tcp":
            return _check_tcp(target)
        if target.type == "ping":
            return _check_ping(target)
        # Sollte durch die Config-Validierung nie passieren:
        return CheckResult(
            name=target.name,
            status=Status.ERROR,
            message=f"Unbekannter Check-Typ: {target.type}",
        )
    except Exception as exc:  # absolute Sicherheitsleine
        logger.exception("Unerwarteter Fehler im Check '%s'", target.name)
        return CheckResult(
            name=target.name,
            status=Status.ERROR,
            message=f"Interner Fehler: {exc}",
        )


# --------------------------------------------------------------------------- #
# HTTP / HTTPS
# --------------------------------------------------------------------------- #
def _check_http(target: Target) -> CheckResult:
    timestamp = datetime.now()

    if not _HAS_REQUESTS:
        return CheckResult(
            name=target.name,
            status=Status.ERROR,
            message="'requests' ist nicht installiert (pip install requests).",
            timestamp=timestamp,
        )

    # Optionaler Host-Header für vhost-/trusted_domains-Fälle.
    headers = {"Host": target.host_header} if target.host_header else None

    start = time.perf_counter()
    try:
        resp = requests.get(
            target.url,
            timeout=target.timeout,
            verify=target.verify_ssl,
            allow_redirects=True,
            headers=headers,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        code = resp.status_code
        if target.expected_status_min <= code <= target.expected_status_max:
            status = Status.OK
            message = f"HTTP {code}"
        else:
            # Erreichbar, aber unerwarteter Code -> warn (kein harter Ausfall).
            status = Status.WARN
            message = (
                f"HTTP {code} (erwartet "
                f"{target.expected_status_min}-{target.expected_status_max})"
            )
        status, message = _apply_slow_warn(target, status, message, elapsed_ms)
        return CheckResult(
            name=target.name,
            status=status,
            message=message,
            response_time_ms=elapsed_ms,
            timestamp=timestamp,
        )

    except requests.exceptions.SSLError as exc:
        return CheckResult(
            name=target.name,
            status=Status.WARN,
            message=f"SSL-Fehler (ggf. verify_ssl: false setzen): {_short(exc)}",
            timestamp=timestamp,
        )
    except requests.exceptions.Timeout:
        return CheckResult(
            name=target.name,
            status=Status.ERROR,
            message="Timeout",
            timestamp=timestamp,
        )
    except requests.exceptions.ConnectionError:
        return CheckResult(
            name=target.name,
            status=Status.ERROR,
            message="Connection refused / nicht erreichbar",
            timestamp=timestamp,
        )
    except RequestException as exc:
        return CheckResult(
            name=target.name,
            status=Status.ERROR,
            message=_short(exc),
            timestamp=timestamp,
        )


# --------------------------------------------------------------------------- #
# TCP
# --------------------------------------------------------------------------- #
def _check_tcp(target: Target) -> CheckResult:
    timestamp = datetime.now()
    host = target.host or ""
    port = int(target.port) if target.port is not None else 0

    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=target.timeout):
            elapsed_ms = (time.perf_counter() - start) * 1000.0
        status, message = _apply_slow_warn(
            target, Status.OK, f"TCP {host}:{port} offen", elapsed_ms
        )
        return CheckResult(
            name=target.name,
            status=status,
            message=message,
            response_time_ms=elapsed_ms,
            timestamp=timestamp,
        )
    except socket.timeout:
        return CheckResult(
            name=target.name,
            status=Status.ERROR,
            message="Timeout",
            timestamp=timestamp,
        )
    except OSError as exc:
        return CheckResult(
            name=target.name,
            status=Status.ERROR,
            message=f"TCP {host}:{port} nicht erreichbar ({_short(exc)})",
            timestamp=timestamp,
        )


# --------------------------------------------------------------------------- #
# Ping (mit TCP-Fallback)
# --------------------------------------------------------------------------- #
def _check_ping(target: Target) -> CheckResult:
    """ICMP-Ping über das System-`ping`.

    Wichtig: ICMP braucht auf manchen Systemen Root-Rechte oder ist in
    Containern/Firewalls blockiert. Deshalb fällt dieser Check bei Problemen
    automatisch auf einen TCP-Check zurück, *wenn* ein `port` konfiguriert ist.
    """
    timestamp = datetime.now()
    host = target.host or ""

    start = time.perf_counter()
    try:
        # -c 1 : ein Paket, -W <sek> : Timeout (Linux). macOS nutzt ms bei -W,
        # daher als String und auf mind. 1 begrenzt -> robust über Plattformen.
        timeout_s = max(1, int(round(target.timeout)))
        proc = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout_s), host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=target.timeout + 2,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if proc.returncode == 0:
            return CheckResult(
                name=target.name,
                status=Status.OK,
                message="Ping OK",
                response_time_ms=elapsed_ms,
                timestamp=timestamp,
            )
        # Ping fehlgeschlagen -> Fallback versuchen.
        return _ping_fallback(target, timestamp, reason="Ping fehlgeschlagen")

    except (FileNotFoundError, PermissionError) as exc:
        # `ping` nicht vorhanden oder keine Rechte -> Fallback.
        logger.debug("Ping nicht nutzbar für '%s': %s", target.name, exc)
        return _ping_fallback(target, timestamp, reason="Ping nicht verfügbar")
    except subprocess.TimeoutExpired:
        return _ping_fallback(target, timestamp, reason="Ping Timeout")
    except Exception as exc:  # pragma: no cover
        return _ping_fallback(target, timestamp, reason=f"Ping-Fehler: {_short(exc)}")


def _ping_fallback(
    target: Target, timestamp: datetime, reason: str
) -> CheckResult:
    """Versucht einen TCP-Check, wenn ein Port konfiguriert ist."""
    if target.port is not None:
        result = _check_tcp(target)
        # Hinweis anhängen, dass wir auf TCP ausgewichen sind.
        result.message = f"{result.message} (Fallback nach: {reason})"
        result.timestamp = timestamp
        return result
    return CheckResult(
        name=target.name,
        status=Status.ERROR,
        message=f"{reason} (kein TCP-Fallback-Port konfiguriert)",
        timestamp=timestamp,
    )


# --------------------------------------------------------------------------- #
# Hilfsfunktionen
# --------------------------------------------------------------------------- #
def _apply_slow_warn(
    target: Target, status: Status, message: str, elapsed_ms: float
) -> "tuple[Status, str]":
    """Stuft ein OK auf WARN herab, wenn die Antwortzeit zu hoch ist.

    Erlaubt ein Frühwarnsignal ("erreichbar, aber lahm"), bevor ein Dienst ganz
    ausfällt. Greift nur, wenn warn_response_ms gesetzt ist und der Check sonst OK
    wäre."""
    if (
        status is Status.OK
        and target.warn_response_ms is not None
        and elapsed_ms > target.warn_response_ms
    ):
        return Status.WARN, f"langsam ({int(round(elapsed_ms))}ms)"
    return status, message


def _short(exc: object, limit: int = 80) -> str:
    """Kürzt lange Exception-Texte für die kompakte Anzeige."""
    text = str(exc).strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def derive_port_from_url(url: Optional[str]) -> Optional[int]:
    """Hilfsfunktion: Standardport aus einer URL ableiten (für Fallbacks)."""
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.port:
        return parsed.port
    if parsed.scheme == "https":
        return 443
    if parsed.scheme == "http":
        return 80
    return None
