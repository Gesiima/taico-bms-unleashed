# V-KING / TAICO BMS — Logger (Phase 1)

Robuster Logger für das VK48150-BMS (PACE/Pylontech-Protokollfamilie, Version `0x52`).
Liest die Live-Werte aller konfigurierten Packs über **eine** Verbindung (TCP-Gateway
oder COM-Port) und schreibt sie nach **SQLite**, **CSV** und/oder **MQTT**.

Diese Phase deckt **Monitoring/Logging** ab. Der Protokoll-Kern enthält bereits
Bausteine für Parameter, MOS-Steuerung und System-Config — die GUI dafür kommt später
und setzt auf denselben Kern auf.

## Installation

Voraussetzung: Python 3.9+.

```bash
pip install pyyaml                 # Pflicht
pip install paho-mqtt             # nur falls MQTT genutzt wird
pip install pyserial             # nur falls direkter COM-Port statt TCP-Gateway
```

## Konfiguration

```bash
cp config.example.yaml config.yaml
```

Dann in `config.yaml` anpassen:
- **Gateway-IP** und Port (`netport1` = TCP Server, Port 9999, geroutet auf Uart 1)
- **Pack-Adressen** je Bus (z. B. `[1, 2]`)
- **MQTT-Broker** (Host/Port/Topic)
- **Intervalle** getrennt einstellbar: `poll_interval` (BMS-Abfrage), `live_interval`
  (Dashboard), `mqtt_interval` (Publish), `db_interval` (Datenbank). Beide BMS werden
  pro Zyklus **parallel** gelesen. `bms.db` wird per Auto-Checkpoint laufend
  zusammengeführt, ist also auch im Betrieb als Einzeldatei vollständig.

Wichtig: Das Gateway erlaubt nur **eine** TCP-Verbindung gleichzeitig (`Max Accept = 1`).
Die Hersteller-Software darf also nicht parallel auf demselben Port verbunden sein.

## Starten

**Variante A — Web-Dashboard (empfohlen):**

```bash
pip install flask
python run_web.py
```

Dann im Browser `http://localhost:8080` öffnen (oder vom Handy/Tablet im selben Netz
`http://<IP-des-PCs>:8080`). Der Hintergrund-Poller läuft mit, schreibt weiter in
SQLite/MQTT, und die Seite zeigt beide Packs live (Auto-Refresh alle 3 s).

**Variante B — reiner Hintergrund-Logger (ohne Web):**

```bash
python run_logger.py            # nutzt config.yaml
python run_logger.py mein.yaml  # alternative Konfig
```

Beenden mit Ctrl-C.

> Eine **Vorschau** des Dashboards (mit Beispieldaten, ohne BMS) liegt als
> `dashboard_preview.html` bei — einfach doppelklicken.

## Ausgabe

- **SQLite** (`data/bms.db`, WAL-Modus): Tabelle `readings`, eine Zeile je Messung,
  mit Spannung, Strom, SOC/SOH, Kapazität, Zyklen, Min/Max/Δ, 6 Temperaturen, V01–V16.
- **CSV** (optional): `data/csv/pack1.csv`, `pack2.csv` …
- **MQTT** (optional):
  - `bms/status` → `online` / `offline` (Gesamt-Tool, via Last-Will)
  - `bms/pack1/online` → `true` / `false` (antwortet das BMS?)
  - `bms/pack1/voltage_v`, `current_a`, `soc`, `soh`, `remain_ah`, `full_ah`, `cycles`, `min_mv`, `max_mv`, `delta_mv`
  - `bms/pack1/cells/01` … `cells/16` (Einzelzellen, abschaltbar via `publish_cells`)
  - `bms/pack1/temp/cell_t1` … `temp/mos_t`
  - `bms/pack1/state` → Gesamt-JSON (nur wenn `publish_state_json: true`)

## Projektstruktur

```
vkbms/
  protocol.py    Frame-Bau/-Parsing, Prüfsummen, Wert-Dekoder  (getestet gegen Captures)
  transport.py   TCP- und Serial-Transport mit Reassemblierung/Reconnect
  sinks.py       SQLite-, CSV- und MQTT-Senken
  poller.py      Polling-Engine + headless Logger
  server.py      Flask-Webserver + Hintergrund-Poller + JSON-API
  web/index.html Live-Dashboard (Monitor-Tab)
run_web.py       Einstiegspunkt Web-Dashboard
run_logger.py    Einstiegspunkt headless
config.example.yaml
```

## Erledigt in v0.3.0

- Versionsanzeige im Dashboard (Statuszeile).
- HTTP-Zugriffslogs per Config abschaltbar (`web.access_log`, Standard aus).
- MQTT kompatibel mit paho-mqtt 2.x, klare Verbindungsmeldung + Auto-Reconnect.
- Einzelzellspannungen als MQTT-Unterordner `bms/packX/cells/01…16` (abschaltbar).
- online/offline-Status: `bms/status` (Tool, via Last-Will) + `bms/packX/online` je BMS.
- `state`-JSON-Topic per Config abschaltbar (Standard aus).

## Erledigt in v0.2.0

- Getrennte Intervalle: `poll_interval`, `live_interval`, `mqtt_interval`, `db_interval`.
- Auto-Checkpoint der SQLite-WAL (kompakte, jederzeit vollständige `bms.db`).
- Sauberes Beenden ohne Traceback.

## Nächste Schritte (gesammelt / geplant)

1. Parameter-Tab: lesen/schreiben (CID2 47/49) mit Rücklese-Prüfung.
2. MOS-Tab: CFET/DFET/Strombegrenzung schalten (CID2 E2) mit Bestätigung + Statusrückmeldung.
3. System-Config-Tab: Zeit/Kapazität/Kalibrierung/Produktinfo.

Die Navigationspunkte dafür sind im Dashboard schon angelegt (als „bald").
Protokoll-Details: siehe `VKING_BMS_Protokoll_Spezifikation.md`.
