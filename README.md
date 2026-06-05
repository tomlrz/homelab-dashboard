# Homelab Status Dashboard (E-Ink)

Ein kompaktes, robustes Statusdashboard für ein selbst gehostetes Homelab.
Ein **Raspberry Pi Zero 2 W** prüft regelmäßig, ob NAS, Proxmox und einzelne
Dienste laufen, und zeigt den Status an – im Terminal/Log oder später auf einem
**schwarz/weiß/rot E-Ink-Display**.

## Projektziel

- Eigenständiger Status-Check, der **auf dem Pi selbst** läuft.
- Der Pi fragt NAS, Proxmox und Dienste ab – die Hauptlogik liegt **nicht** auf
  NAS, Proxmox oder Home Assistant.
- Dadurch kann der Pi auch dann noch Fehler anzeigen, wenn NAS oder Proxmox
  ausfallen.
- Kein Prometheus, kein Docker, keine Datenbank, kein Webframework – nur ein
  kleines, gut wartbares Python-Programm.
- **Display-unabhängig**: Start mit Textausgabe, das echte E-Ink-Display lässt
  sich später ergänzen, ohne die Check-Logik zu ändern.

## Funktionsweise

```
config.yaml ──> checks (HTTP/TCP/Ping) ──> Dashboard ──> Renderer (Text | E-Paper)
```

Standardmäßig läuft das Programm **einmal** und beendet sich. Die regelmäßige
Ausführung (alle 5 Minuten) übernimmt ein **systemd-Timer** – bewusst keine
Endlosschleife im Programm.

## Features

- **Schwarz/Weiß/Rot-Layout** mit Alarm-Banner (weiße Schrift auf Rot bei
  Ausfall), Status-Glyphen (gefüllt = ok, hohl = warn, rot = fail),
  Zusammenfassungszeile und „Fehler zuerst"-Sortierung.
- **„Down seit …"** – Ausfalldauer pro Dienst (statt nur „Timeout").
- **Flapping-Schutz (Debounce):** ein einzelner Aussetzer wird als *instabil*
  (WARN) angezeigt; erst nach `failure_threshold` Fehlschlägen in Folge wird es
  rot (FAIL).
- **Mini-Verlaufsstreifen** pro Dienst (Sparkline aus den letzten Checks).
- **Pi-Eigenstatus** in der Fußzeile: CPU-Temperatur, Unterspannung/Throttling
  (`vcgencmd`), WLAN-Signal, Uptime.
- **Parallele Checks** (Thread-Pool) – schnell auch bei vielen Diensten/Timeouts.
- **E-Ink-schonend:** Neuzeichnen nur bei Statusänderung (`redraw_only_on_change`).
- **Push-Benachrichtigung** bei Statuswechsel via **ntfy / Telegram / Gotify**.
- **Antwortzeit-Warnung:** `warn_response_ms` stuft „erreichbar, aber langsam"
  auf WARN herab.

Der Laufzeit-Zustand (down-seit, Verlauf, letzte Signatur, letzter gemeldeter
Status) liegt in `state.json` neben der `config.yaml` und ist per `.gitignore`
ausgeschlossen.

## Projektstruktur

```
.
├── README.md
├── requirements.txt
├── config.example.yaml          # Vorlage – kopieren nach config.yaml
├── .gitignore                   # config.yaml & venv sind ausgeschlossen
├── src/
│   ├── main.py                  # Einstiegspunkt: laden, prüfen, rendern, melden
│   ├── config.py                # YAML laden + validieren (typisiert)
│   ├── checks.py                # HTTP/TCP/Ping-Checks (robust, fehlertolerant)
│   ├── models.py                # Datenmodelle (Status, CheckResult, Dashboard)
│   ├── state.py                 # Zustand: down-seit, Debounce, Verlauf, Signatur
│   ├── system_info.py           # Pi-Eigenstatus (Temp/Throttle/WLAN/Uptime)
│   ├── notify.py                # Push via ntfy/Telegram/Gotify
│   └── renderers/
│       ├── base.py              # gemeinsame Renderer-Schnittstelle
│       ├── text_renderer.py     # kompakte Text-/Terminalausgabe
│       └── epaper_renderer_placeholder.py  # Platzhalter fürs E-Ink-Display
├── scripts/
│   └── install_systemd.sh       # venv + systemd-Einrichtung
└── systemd/
    ├── homelab-dashboard.service
    └── homelab-dashboard.timer
```

## Installation auf Raspberry Pi OS Lite

```bash
# 1. System aktualisieren und Grundpakete installieren
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

# 2. Projekt holen (oder per scp/USB kopieren)
cd ~
git clone <DEIN_REPO_URL> homelab-dashboard   # oder Ordner manuell kopieren
cd homelab-dashboard

# 3. Konfiguration anlegen
cp config.example.yaml config.yaml
nano config.yaml        # an dein Homelab anpassen (IPs, URLs, Ports)

# 4. venv + Abhängigkeiten + systemd einrichten
./scripts/install_systemd.sh            # erst mit --dry-run testen, s.u.
```

> **Pillow auf dem Pi:** Wird nur für den E-Paper-(Placeholder-)Renderer
> benötigt. Falls die Installation hakt:
> `sudo apt install -y libjpeg-dev zlib1g-dev`. Wer nur `renderer: text` nutzt,
> kann die Pillow-Zeile in `requirements.txt` weglassen.

## Beispiel-`config.yaml`

Siehe [`config.example.yaml`](config.example.yaml) für die kommentierte
Vollversion. Minimalbeispiel:

```yaml
update_interval_seconds: 300

nas:
  name: "NAS"
  type: "tcp"
  host: "192.168.1.10"
  port: 443
  timeout: 3

proxmox:
  name: "PROXMOX"
  type: "https"
  url: "https://192.168.1.20:8006/"
  timeout: 4
  verify_ssl: false          # Proxmox: self-signed Zertifikat

services:
  - name: "Nextcloud"
    type: "https"
    url: "https://nas.example.lan/nextcloud/status.php"
    timeout: 5
  - name: "Immich"
    type: "http"
    url: "http://192.168.1.10:2283/api/server/ping"
    timeout: 5

display:
  renderer: "text"           # oder "epaper_placeholder"
  rotation: 0
  width: 250
  height: 122
```

### Check-Typen

| `type`  | Pflichtfelder      | Beschreibung                                            |
|---------|--------------------|--------------------------------------------------------|
| `http`  | `url`              | HTTP-GET, prüft Statuscode (default 200–399 = OK)       |
| `https` | `url`              | wie `http`, mit TLS; `verify_ssl: false` für self-signed|
| `tcp`   | `host`, `port`     | prüft, ob ein Port offen ist (keine Sonderrechte nötig) |
| `ping`  | `host` (+`port`)   | ICMP-Ping; fällt auf TCP zurück, wenn `port` gesetzt    |

### Statuslogik

- **OK** – Dienst erreichbar / HTTP-Code im erwarteten Bereich.
- **WARN** – erreichbar, aber unerwarteter HTTP-Code oder SSL-Auffälligkeit.
- **FAIL (error)** – Timeout, Connection refused oder Host nicht erreichbar.
- **Gesamtstatus**: `error`, sobald ein Check `error` ist; sonst `warn`, wenn
  mind. ein `warn`; sonst `ok`. Der Exitcode ist `1` bei Gesamtstatus `error`,
  sonst `0`.

## Start per CLI

```bash
# Einzeldurchlauf (Standard), nutzt ./config.yaml
venv/bin/python src/main.py

# Anderer Config-Pfad
venv/bin/python src/main.py --config /pfad/zur/config.yaml

# Ausführliches Logging (Logs gehen nach stderr, Anzeige nach stdout)
venv/bin/python src/main.py -v

# Optionaler Dauerbetrieb zum lokalen Testen (sonst systemd-Timer nutzen)
venv/bin/python src/main.py --watch
```

Beispielausgabe (TextRenderer):

```
# HOMELAB STATUS

NAS       [OK]   23ms
PROXMOX   [OK]   31ms

SERVICES
Nextcloud [OK]   120ms
Immich    [OK]   95ms
Paperless [FAIL] Timeout

OVERALL: FAIL
LAST: 2026-06-05 14:30
```

## systemd-Timer einrichten

Das Skript leitet alle Pfade automatisch aus dem Projektordner ab
(`PROJECT_DIR`) – **keine** hartkodierten absoluten Pfade.

```bash
# Erst ansehen, was passieren würde (ändert nichts):
./scripts/install_systemd.sh --dry-run

# Dann echt installieren (systemd-Teil braucht Root):
sudo ./scripts/install_systemd.sh
```

Das Skript:

1. prüft `python3`/`python3-venv`,
2. erstellt `venv/` und installiert `requirements.txt`,
3. legt bei Bedarf `config.yaml` aus der Vorlage an,
4. ersetzt die Platzhalter `__PROJECT_DIR__` / `__USER__` in der Service-Unit,
5. installiert `homelab-dashboard.service` + `.timer` und aktiviert den Timer.

Nützliche Befehle danach:

```bash
sudo systemctl start homelab-dashboard.service          # Einzellauf testen
journalctl -u homelab-dashboard.service -n 30 --no-pager
systemctl list-timers homelab-dashboard.timer           # nächster Lauf?
```

Das Prüfintervall steckt in [`systemd/homelab-dashboard.timer`](systemd/homelab-dashboard.timer)
(`OnUnitActiveSec=5min`).

## Updates auf dem Pi (git)

Das Projekt ist für den Git-Workflow gemacht: Code im Repo, lokale Anpassungen
in `config.yaml` (per `.gitignore` ausgeschlossen, wird beim Pullen **nie**
überschrieben).

```bash
# Einmalig auf dem Pi:
git clone <DEIN_REPO_URL> homelab-dashboard
cd homelab-dashboard
cp config.example.yaml config.yaml && nano config.yaml
sudo ./scripts/install_systemd.sh

# Später Updates einspielen:
cd ~/homelab-dashboard
git pull
venv/bin/pip install -r requirements.txt        # falls sich Deps geändert haben
sudo systemctl restart homelab-dashboard.timer  # bzw. .service einmal testen
```

> Tipp: Wenn du Felder in `config.example.yaml` ergänzt bekommst (nach einem
> `git pull`), gleiche deine `config.yaml` per `diff config.example.yaml config.yaml`
> ab.

## Benachrichtigungen (Push)

Optional schickt der Dienst bei **Statuswechsel** (Ausfall / Wiederherstellung)
eine Push-Nachricht. Am einfachsten ist **ntfy**:

1. ntfy-App installieren (Android/iOS) oder `https://ntfy.sh/<topic>` im Browser.
2. Ein **schwer erratbares** Topic abonnieren, z. B. `homelab-7f3a91`.
3. In `config.yaml`:
   ```yaml
   notify:
     provider: "ntfy"
     ntfy_topic: "homelab-7f3a91"
     notify_on: ["error", "recovery"]
   ```

Alternativen: **Telegram** (Bot-Token + Chat-ID) oder **Gotify** (self-hosted).
Felder dafür stehen kommentiert in `config.example.yaml`. Ein fehlgeschlagener
Versand stört den Dienst nie – er wird nur geloggt.

## Später das echte E-Ink-Display ergänzen

Der Renderer wird **allein über `config.yaml`** umgeschaltet
(`display.renderer`). Die Check-Logik bleibt unangetastet.

1. Displaymodell bestimmen (z.B. *Waveshare 2.13" V4 B/W/R*) und Auflösung in
   `display.width` / `display.height` eintragen.
2. Waveshare-Bibliothek installieren (Beispiel):
   ```bash
   sudo raspi-config        # SPI aktivieren (Interface Options -> SPI)
   venv/bin/pip install RPi.GPIO spidev
   # waveshare_epd-Modul aus dem offiziellen Repo ins Projekt legen/installieren
   ```
3. In [`src/renderers/epaper_renderer_placeholder.py`](src/renderers/epaper_renderer_placeholder.py)
   die mit `# TODO(waveshare)` markierten Stellen ausfüllen:
   - im Konstruktor `epdXinY.EPD()` initialisieren,
   - in `render()` das fertige Pillow-Bild in schwarz-/rot-Buffer aufteilen und
     per `self.epd.display(...)` senden, danach `self.epd.sleep()`.
4. In `config.yaml` `renderer: "epaper_placeholder"` setzen (bzw. die Klasse
   nach dem Ausfüllen z.B. in `EpaperRenderer` umbenennen) und testen.

Schon **vor** der Hardware erzeugt der Placeholder bei
`renderer: epaper_placeholder` eine Bildvorschau `epaper_preview.png` – damit
lässt sich das Layout am Schreibtisch prüfen.

## Troubleshooting

**Dienst wird als FAIL angezeigt, obwohl er läuft**
- Stimmen `host`/`port`/`url` und ist der Pi im richtigen Netz/Subnetz?
- `timeout` zu klein? Auf langsamen Diensten (Nextcloud-Start) ggf. erhöhen.
- Manuell gegenchecken: `curl -v <url>` bzw. `nc -vz <host> <port>` vom Pi aus.

**HTTPS mit self-signed Zertifikat (Proxmox, internes NAS)**
- `verify_ssl: false` beim betreffenden Service/Host setzen. Das Dashboard
  meldet SSL-Fehler sonst als `WARN`.

**Ping braucht Rechte / funktioniert nicht**
- ICMP braucht auf manchen Systemen Root oder ist per Firewall geblockt.
- **Empfehlung:** statt `type: ping` lieber `type: tcp` mit einem echten Port
  nutzen – das braucht keine Sonderrechte und ist aussagekräftiger.
- Wenn du `ping` brauchst: gib zusätzlich einen `port` an, dann fällt der Check
  automatisch auf TCP zurück. Alternativ ICMP für unprivilegierte Nutzer
  erlauben: `sudo sysctl -w net.ipv4.ping_group_range="0 2147483647"`.

**WLAN-Probleme beim Pi Zero 2 W**
- Power-Management des WLANs deaktivieren (spart Aussetzer):
  `sudo iw dev wlan0 set power_save off` (dauerhaft via systemd/rc.local).
- 2,4-GHz-Empfang prüfen; der Zero 2 W kann kein 5 GHz.
- Stabiles Netzteil verwenden – Unterspannung (`vcgencmd get_throttled` ≠ `0x0`)
  führt zu WLAN-/Stabilitätsproblemen.
- Bei Hostnamen-Problemen testweise IPs statt `*.local`-Namen verwenden.

## Lokal testen (ohne Pi, ohne Hardware)

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml
# In config.yaml ein paar erreichbare Ziele eintragen, z.B.:
#   url: https://example.com   (type: https)
venv/bin/python src/main.py -v
```

Mit `display.renderer: "epaper_placeholder"` entsteht zusätzlich
`epaper_preview.png` zur Layout-Kontrolle.

## Codequalität / Kompatibilität

- Python **3.11** getestet, kompatibel ab **3.9** (Typing via
  `from __future__ import annotations`).
- Durchgehend Typannotationen, robuste Fehlerbehandlung: ein einzelner
  fehlschlagender Check bringt nie das ganze Dashboard zum Absturz.
- `logging` überall – Ausnahme ist die eigentliche TextRenderer-Ausgabe
  (`print`), da sie das „Produkt" ist.
- Externe Abhängigkeiten bewusst minimal: `requests`, `PyYAML`, `Pillow`.
