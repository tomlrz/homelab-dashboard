"""Pi-Eigenstatus: CPU-Temperatur, Unterspannung/Throttling, WLAN, Uptime.

Da der Pi selbst der Wächter ist, ist seine eigene Gesundheit wertvoll: ein zu
heißer oder unterversorgter Pi erklärt sprunghafte Fehlmeldungen. Alle Werte
werden "best effort" gelesen – fehlt eine Quelle (z.B. auf einem Mac/Server),
wird sie einfach weggelassen, nie ein Fehler geworfen.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import List, Optional

from models import CheckResult, Status

logger = logging.getLogger(__name__)


def collect_system(show: bool = True) -> List[CheckResult]:
    """Liefert (höchstens) eine kompakte Systemzeile als CheckResult.

    Beispielmeldung: '48C  WLAN -57dBm  up 6d 3h'. Der Status ist das Schlimmste
    der Einzelwerte (heiß/unterspannt/schwaches WLAN -> warn)."""
    if not show:
        return []

    parts: List[str] = []
    status = Status.OK

    temp = _cpu_temp_c()
    if temp is not None:
        parts.append(f"{temp:.0f}C")
        if temp >= 80:
            status = _worse(status, Status.ERROR)
        elif temp >= 70:
            status = _worse(status, Status.WARN)

    throttle_msg, throttle_status = _throttle_status()
    if throttle_msg:
        parts.append(throttle_msg)
        status = _worse(status, throttle_status)

    wifi = _wifi_dbm()
    if wifi is not None:
        parts.append(f"WLAN {wifi}dBm")
        if wifi <= -75:
            status = _worse(status, Status.WARN)

    up = _uptime_str()
    if up:
        parts.append(f"up {up}")

    if not parts:
        return []

    return [
        CheckResult(
            name="PI",
            status=status,
            message="  ".join(parts),
            is_system=True,
        )
    ]


def _worse(a: Status, b: Status) -> Status:
    return a if a.severity >= b.severity else b


def _cpu_temp_c() -> Optional[float]:
    # 1) Linux/Pi: /sys/class/thermal
    p = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        if p.exists():
            return int(p.read_text().strip()) / 1000.0
    except (OSError, ValueError):
        pass
    # 2) Pi-spezifisch: vcgencmd measure_temp -> "temp=48.3'C"
    out = _run(["vcgencmd", "measure_temp"])
    if out:
        m = re.search(r"temp=([\d.]+)", out)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return None


def _throttle_status() -> "tuple[Optional[str], Status]":
    """Wertet `vcgencmd get_throttled` aus (Unterspannung/Drosselung)."""
    out = _run(["vcgencmd", "get_throttled"])
    if not out:
        return None, Status.OK
    m = re.search(r"throttled=0x([0-9a-fA-F]+)", out)
    if not m:
        return None, Status.OK
    bits = int(m.group(1), 16)
    if bits == 0:
        return None, Status.OK  # alles gut -> keine Extra-Zeile
    # Aktuell anliegende Probleme (untere Bits) sind kritischer als "war mal".
    now_undervolt = bool(bits & 0x1)
    now_throttled = bool(bits & 0x4)
    if now_undervolt:
        return "UNTERSPANNUNG", Status.ERROR
    if now_throttled:
        return "throttled", Status.WARN
    # Nur historische Ereignisse (Bits 16-19) -> Hinweis als WARN.
    return "throttle-hist", Status.WARN


def _wifi_dbm() -> Optional[int]:
    """Signalstärke in dBm aus /proc/net/wireless (Linux)."""
    p = Path("/proc/net/wireless")
    try:
        if not p.exists():
            return None
        for line in p.read_text().splitlines():
            if ":" in line and (line.strip().startswith(("wlan", "wlp"))):
                # Format: iface: status link level noise ...
                fields = line.split()
                # 'level' steht an Index 3, oft mit angehängtem Punkt.
                level = fields[3].rstrip(".")
                return int(float(level))
    except (OSError, ValueError, IndexError):
        return None
    return None


def _uptime_str() -> Optional[str]:
    # Linux: /proc/uptime (Sekunden seit Boot)
    p = Path("/proc/uptime")
    secs: Optional[float] = None
    try:
        if p.exists():
            secs = float(p.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        secs = None
    if secs is None:
        # Fallback (z.B. macOS): kein /proc -> uptime-Befehl ist uneinheitlich,
        # daher hier einfach weglassen.
        return None
    days = int(secs // 86400)
    hours = int((secs % 86400) // 3600)
    if days > 0:
        return f"{days}d {hours}h"
    mins = int((secs % 3600) // 60)
    return f"{hours}h {mins}m"


def _run(cmd: List[str], timeout: float = 3.0) -> Optional[str]:
    """Führt ein Kommando aus und gibt stdout zurück – fehlertolerant."""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            text=True,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
        return None
    return None
