# V-KING / TAICO BMS — Unleashed

Eigenständiges Monitoring- und Steuerungs-Tool für das **VK48150**-BMS
(PACE/Pylontech-Protokollfamilie, Version `0x52`) als vollwertiger Ersatz der
Hersteller-Software. Liest alle konfigurierten Packs über **eine** Verbindung
(TCP-Gateway oder COM-Port), speichert lokal nach **SQLite**, **CSV** und/oder
**MQTT** und bietet ein **Web-Dashboard** mit Live-Werten, Verlaufs-Charts und
Einstellungen — inklusive MOS-Steuerung.

Das Protokoll wurde per .NET-Dekompilierung und Mitschnitt-Analyse vollständig
zurückentwickelt; Details in `VKING_BMS_Protokoll_Spezifikation.md`.

## Funktionen

**Monitor (Dashboard)**
- Live-Karten je Pack: Modus (Laden/Entladen/Ruhe), SOC/SOH, Spannung, Strom,
  Rest-/Vollkapazität, Zyklen, 6 Temperaturen, alle 16 Zellspannungen als Balken
  mit Min/Max- und Δ-Anzeige.
- **Master-Sub-Packs gruppiert**: mehrere Adressen auf einer Leitung werden unter
  einer Bus-Überschrift zusammengefasst.
- **Balancing-Visualisierung**: aktuell balancierende Zellen werden markiert.
- **Warn-/Schutz-Badges** je Pack (Zelle/Pack Überspannung, Zelle-Überspannung-Schutz).
- **Zell-Alarm** ab konfigurierbarem Schwellwert (`cell_alert_mv`).
- **MOS-Steuerung**: CFET/DFET schalten und Power Off — mit Bestätigungsdialog und
  Rücklese-Prüfung (bis ~2 s).
- **Pause pro Bus**: gibt die Verbindung zur Laufzeit frei (z. B. um kurzzeitig die
  Originalsoftware/VCOM auf derselben Leitung zu nutzen), ohne den Dienst zu stoppen.

**Verlauf (Chart)**
- uPlot-Diagramm: Zellspannungen (linke mV-Achse), Pack-Spannung und Strom je eigene
  rechte Achse (unabhängige Skalen, Werte mit Einheit).
- **Verlauf** (frei wählbarer Zeitraum) oder **Live** (5/10/30/60 min, 2 h, 6 h).
- Zoom per Ziehen mit sichtbarem Auswahlbereich + Zeitspanne; im Live-Modus bleibt der
  Zoom erhalten.
- Kompakte Legende mit Ein-/Ausblenden je Serie (gefülltes Kästchen = aktiv) und
  Werten, die dem Cursor folgen (inkl. Cursor-Zeit).
- Verlustfreies Downsampling für große Zeiträume; SQLite-Index für schnelle Abfragen.

**Einstellungen**
- Alle Konfigurationswerte editierbar (Intervalle, Web, Alert-Schwelle, MQTT, Busse).
- **Busse hinzufügen/entfernen**, **Aktiv-Schalter pro Bus** (persistent), Umschaltung
  **TCP ↔ Serial** mit den jeweils passenden Feldern.
- Passwort maskiert, „Speichern & Neustarten" und ein jederzeit sichtbarer
  „Dienst neu starten"-Button.

## Installation

Voraussetzung: Python 3.9+.

```bash
pip install pyyaml flask          # Pflicht für das Web-Dashboard
pip install paho-mqtt             # nur falls MQTT genutzt wird
pip install pyserial              # nur falls direkter COM-Port statt TCP-Gateway
```

## Konfiguration

```bash
cp config.example.yaml config.yaml
```

Wichtige Werte in `config.yaml`:
- **Busse**: je Bus eine Verbindung (`tcp` mit Host/Port oder `serial` mit Port/Baudrate)
  und die Liste der **Pack-Adressen** (z. B. `[1, 2]` für Master + Sub-Pack auf einer
  Leitung). `enabled: false` nimmt einen Bus dauerhaft aus dem Polling.
- **Intervalle**: `poll_interval` (BMS-Abfrage, Frische-Basis), `live_interval`
  (Dashboard), `mqtt_interval` (Publish), `db_interval` (Datenbank). Alle Busse werden
  pro Zyklus **parallel** gelesen.
- **`cell_alert_mv`**: globaler Schwellwert, ab dem eine Zelle hervorgehoben wird (0 = aus).
- **MQTT-Broker** (Host/Port/Topic, optional Benutzer/Passwort).
- **SQLite** (`output.sqlite.path`, `retention_days`: 0 = unbegrenzt, sonst tägliche
  Bereinigung älterer Zeilen). Die WAL wird per Auto-Checkpoint laufend zusammengeführt,
  `bms.db` ist also auch im Betrieb eine vollständige Einzeldatei.

> **Wichtig:** Das Gateway erlaubt nur **eine** TCP-Verbindung gleichzeitig
> (`Max Accept = 1`). Die Hersteller-Software darf nicht parallel auf demselben Port
> verbunden sein. Über den **Pause**-Button im Dashboard kann die Verbindung kurzzeitig
> freigegeben werden.

## Starten

**Web-Dashboard (empfohlen):**
```bash
python run_web.py                # nutzt config.yaml
python run_web.py mein.yaml      # alternative Konfig
```
Dann `http://localhost:8080` öffnen (oder `http://<IP-des-PCs>:8080` im LAN). Der
Hintergrund-Poller läuft mit und schreibt weiter in SQLite/MQTT.

**Reiner Hintergrund-Logger (ohne Web):**
```bash
python run_logger.py
```
Beenden mit Ctrl-C. Für Dauerbetrieb auf einem Server siehe `deploy/README-server.md`.

## MQTT-Topics

- `bms/status` → `online` / `offline` (Gesamt-Tool, via Last-Will)
- `bms/packX/online` → `true` / `false` (antwortet das BMS? 3-Fehlversuch-Toleranz)
- `bms/packX/voltage_v`, `current_a`, `soc`, `soh`, `remain_ah`, `full_ah`, `cycles`,
  `min_mv`, `max_mv`, `delta_mv`
- `bms/packX/cells/01` … `cells/16` (Einzelzellen, abschaltbar via `publish_cells`)
- `bms/packX/temp/cell_t1` … `temp/mos_t`
- `bms/packX/control/cfet`, `control/dfet` → aktueller FET-Status (`true`/`false`)
- Befehle: `bms/packX/control/cfet/set`, `control/dfet/set`, `control/poweroff/set`
- `bms/packX/state` → Gesamt-JSON (nur wenn `publish_state_json: true`)

## JSON-API (Web)

`GET /api/state` (Live-Stand), `GET /api/history` (Verlauf, downsampled),
`POST /api/mos` (CFET/DFET schalten), `POST /api/poweroff`, `POST /api/pause`
(Bus pausieren/fortsetzen), `GET/POST /api/config`, `POST /api/restart`.

## Projektstruktur

```
vkbms/
  protocol.py        Frame-Bau/-Parsing, Prüfsummen, Wert-Dekoder (Analog inkl.
                     Balancing-Maske, Status inkl. Warn-/Schutz-/FET-Bits)
  transport.py       TCP- und Serial-Transport mit Reassemblierung/Reconnect
  sinks.py           SQLite-, CSV- und MQTT-Senken
  poller.py          Polling-Engine, MOS-Steuerung, Pause + headless Logger
  server.py          Flask-Webserver + Hintergrund-Poller + JSON-API
  web/
    index.html       Live-Dashboard (Monitor)
    chart.html       Verlauf/Live-Chart (uPlot)
    settings.html    Einstellungen
    vendor/          uPlot (offline)
run_web.py           Einstiegspunkt Web-Dashboard
run_logger.py        Einstiegspunkt headless
config.example.yaml  Vorlage für config.yaml
deploy/              systemd-Unit + Server-Anleitung
docs/                Screenshots, Gateway-Konfiguration
```

## Anbindung mittels TCP-RS232-Device-Server

Verwendet wird dieses Gerät; jeder VCOM-fähige Adapter sollte funktionieren:
http://www.hi-flying.com/hf5122

<img src="docs/netport_config.png" width="500">

<img src="docs/serialport_config.png" width="500">

## Screenshots

<img src="docs/monitor.png" width="700">

<img src="docs/diagram.png" width="700">

<img src="docs/mqtt_in_iobroker.png" width="700">

## Stand & Roadmap

Aktuelle Version: **v0.8.1**. Änderungen je Release in `CHANGELOG.md`, geplante Punkte
in `ROADMAP.md`.

## Attribution

The code in this project was generated with assistance from Claude (Anthropic).
All functional requirements, architectural decisions, corrections, and instructions
were provided by Gesiima. No manual code editing was performed; the implementation
was produced iteratively based on high‑level guidance and refinement instructions.
