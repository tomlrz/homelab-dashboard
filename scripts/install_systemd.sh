#!/usr/bin/env bash
#
# install_systemd.sh
# ------------------
# Richtet das Homelab-Statusdashboard auf einem Raspberry Pi (oder beliebigem
# Linux mit systemd) ein:
#   1. prüft Pfade & Voraussetzungen
#   2. erstellt ein Python-venv und installiert requirements.txt
#   3. erzeugt config.yaml aus config.example.yaml (falls noch nicht vorhanden)
#   4. installiert systemd .service + .timer (mit ersetzten Pfaden)
#
# Das Skript macht KEINE riskanten Annahmen über absolute Pfade: es leitet
# PROJECT_DIR aus seinem eigenen Speicherort ab. Mit --dry-run werden nur
# Hinweise ausgegeben, ohne etwas zu installieren.
#
# Aufruf:
#   ./scripts/install_systemd.sh            # installiert (sudo für systemd nötig)
#   ./scripts/install_systemd.sh --dry-run  # nur anzeigen, nichts ändern
#
set -euo pipefail

# --- Pfade robust bestimmen --------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
VENV_DIR="${PROJECT_DIR}/venv"
SERVICE_SRC="${PROJECT_DIR}/systemd/homelab-dashboard.service"
TIMER_SRC="${PROJECT_DIR}/systemd/homelab-dashboard.timer"
SYSTEMD_DIR="/etc/systemd/system"
RUN_USER="${SUDO_USER:-$(id -un)}"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

info()  { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }
err()   { printf '\033[1;31m[ERR ]\033[0m %s\n' "$*" >&2; }

info "Projektordner : ${PROJECT_DIR}"
info "Ausführen als : ${RUN_USER}"
[[ "${DRY_RUN}" == "1" ]] && warn "DRY-RUN: es wird nichts verändert."

# --- 1. Voraussetzungen prüfen ----------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  err "python3 nicht gefunden. Bitte installieren: sudo apt install python3 python3-venv"
  exit 1
fi

PYTHON_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
info "Gefundenes Python: ${PYTHON_VERSION}"

if [[ ! -f "${PROJECT_DIR}/requirements.txt" ]]; then
  err "requirements.txt nicht gefunden unter ${PROJECT_DIR}"
  exit 1
fi

# --- 2. venv + Abhängigkeiten ------------------------------------------------
if [[ "${DRY_RUN}" == "1" ]]; then
  info "Würde venv erstellen: ${VENV_DIR}"
  info "Würde installieren  : pip install -r requirements.txt"
else
  if [[ ! -d "${VENV_DIR}" ]]; then
    info "Erstelle venv: ${VENV_DIR}"
    if ! python3 -m venv "${VENV_DIR}"; then
      err "venv-Erstellung fehlgeschlagen. Fehlt python3-venv? -> sudo apt install python3-venv"
      exit 1
    fi
  else
    info "venv existiert bereits, überspringe Erstellung."
  fi
  info "Installiere Abhängigkeiten ..."
  "${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null
  "${VENV_DIR}/bin/pip" install -r "${PROJECT_DIR}/requirements.txt"
fi

# --- 3. config.yaml sicherstellen -------------------------------------------
if [[ ! -f "${PROJECT_DIR}/config.yaml" ]]; then
  if [[ "${DRY_RUN}" == "1" ]]; then
    info "Würde config.yaml aus config.example.yaml erzeugen."
  else
    warn "config.yaml fehlt – erstelle Kopie aus config.example.yaml."
    cp "${PROJECT_DIR}/config.example.yaml" "${PROJECT_DIR}/config.yaml"
    warn "Bitte ${PROJECT_DIR}/config.yaml jetzt an dein Homelab anpassen!"
  fi
else
  info "config.yaml vorhanden."
fi

# --- 4. systemd-Units vorbereiten -------------------------------------------
# Platzhalter in den Unit-Templates ersetzen und nach /etc/systemd/system legen.
TMP_SERVICE="$(mktemp)"
TMP_TIMER="$(mktemp)"
trap 'rm -f "${TMP_SERVICE}" "${TMP_TIMER}"' EXIT

sed -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
    -e "s|__USER__|${RUN_USER}|g" \
    "${SERVICE_SRC}" > "${TMP_SERVICE}"
cp "${TIMER_SRC}" "${TMP_TIMER}"

info "Vorbereitete service-Unit:"
sed 's/^/    /' "${TMP_SERVICE}"

if [[ "${DRY_RUN}" == "1" ]]; then
  cat <<EOF

[DRY-RUN] Zum Installieren manuell ausführen:

  sudo cp "${TMP_SERVICE}" ${SYSTEMD_DIR}/homelab-dashboard.service
  sudo cp "${TIMER_SRC}"   ${SYSTEMD_DIR}/homelab-dashboard.timer
  sudo systemctl daemon-reload
  sudo systemctl enable --now homelab-dashboard.timer

Test eines Einzellaufs:
  sudo systemctl start homelab-dashboard.service
  journalctl -u homelab-dashboard.service -n 30 --no-pager
EOF
  exit 0
fi

# Ab hier echte Installation – braucht Root für /etc/systemd/system.
if [[ "${EUID}" -ne 0 ]]; then
  warn "Für die systemd-Installation werden Root-Rechte benötigt."
  warn "Bitte erneut mit sudo ausführen ODER die obigen Befehle manuell nutzen:"
  cat <<EOF

  sudo cp "${TMP_SERVICE}" ${SYSTEMD_DIR}/homelab-dashboard.service
  sudo cp "${TIMER_SRC}"   ${SYSTEMD_DIR}/homelab-dashboard.timer
  sudo systemctl daemon-reload
  sudo systemctl enable --now homelab-dashboard.timer
EOF
  exit 0
fi

info "Installiere systemd-Units nach ${SYSTEMD_DIR} ..."
cp "${TMP_SERVICE}" "${SYSTEMD_DIR}/homelab-dashboard.service"
cp "${TIMER_SRC}"   "${SYSTEMD_DIR}/homelab-dashboard.timer"
systemctl daemon-reload
systemctl enable --now homelab-dashboard.timer

info "Fertig. Status:"
systemctl status homelab-dashboard.timer --no-pager || true
cat <<EOF

Nächste Schritte:
  - Einzellauf testen : sudo systemctl start homelab-dashboard.service
  - Logs ansehen      : journalctl -u homelab-dashboard.service -n 30 --no-pager
  - Timer prüfen      : systemctl list-timers homelab-dashboard.timer
EOF
