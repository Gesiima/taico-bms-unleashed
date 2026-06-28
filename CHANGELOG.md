# Changelog — VK-BMS Tool

Format: neueste Version oben. Versionierung: MAJOR.MINOR.PATCH.

## [0.8.0] — 2026-06-28
### Monitor / Dashboard
- Master-Sub-Packs werden jetzt **gruppiert** dargestellt: pro Bus/Leitung eine Überschrift,
  darunter die zugehörigen Packs. Mit **Pause-Button pro Bus** (Verbindung wird freigegeben,
  z. B. für VCOM/Originalsoftware; Laufzeit, nicht persistent).
- **Balancing-Visualisierung**: aktive Zellen werden markiert (16-Bit-Maske aus dem 42-Frame,
  gegen die Parameter Balance-Startspannung/Druckdifferenz verifiziert).
- **Warn-/Schutz-Status** je Pack als Badge (Zelle/Pack Überspannung Warnung, Zelle Überspannung
  Schutz). Dekodiert aus State-Byte 5 (Warnung) und 6 (Schutz).
- **Zell-Alarm** ab konfigurierbarem Schwellwert `cell_alert_mv` (global, z. B. 3600 mV):
  betroffene Zelle wird rot hervorgehoben.
- Zellnummern besser lesbar (Kontrast/Schatten auf allen Balkenfarben).
### Verlauf / Chart
- Legende kompakt: Name und Wert direkt nebeneinander, stabile Wertbreite (kein Springen).
- Diagrammbreite folgt jetzt dem **Container** (Achsenwerte bleiben im Bild), inkl. Resize.
- Ampere-Achse zeigt Nachkommastelle, wenn die Werte eng beieinander liegen.
### Einstellungen
- **Serial-Konfiguration in der GUI**: bei Typ „serial" erscheinen COM-Port/Device + Baudrate
  statt Host/Port.
- **Bus hinzufügen/entfernen** direkt in der GUI.
- **Aktiv-Schalter pro Bus** (persistent): inaktive Busse werden nicht gepollt.
### Intern
- Balancing-Maske im Analog-Decoder; Warn-/Schutz-Properties im Status-Decoder.
- DB/CSV schreiben nur skalare Felder (Balancing-Liste wird nicht als Spalte persistiert).
- Neue API: `POST /api/pause` (Bus pausieren/fortsetzen); `/api/state` liefert `cell_alert_mv`
  und `buses`.

## [0.7.0] — 2026-06-28
### Verlauf
- Rechte Achsen kompakt: keine Achsen-Titel mehr; Skalenwerte mit Einheit direkt am Wert
  (z. B. „53,5 V", „10 A") -> deutlich weniger Randbreite.
- Legende schmaler (Werte auf bekannte Maximalbreite dimensioniert).
- Chart folgt jetzt der Fensterbreite (Resize), nicht nur beim Laden.
### Einstellungen
- Dauerhafter „Dienst neu starten"-Button (immer sichtbar, unabhängig von Änderungen).

## [0.6.3] — 2026-06-28
### Hinzugefügt
- Live-Stufen 60 min, 2 h, 6 h ergänzt (lange Fenster aktualisieren etwas ruhiger).
- Cursor-Zeit wieder da: Zeile „Zeit: …" über der Legende (ohne Hover letzter Zeitstempel).
### Geändert
- Legende kompakter: Name und Wert direkt nebeneinander, keine große Lücke mehr.

## [0.6.2] — 2026-06-28
### Geändert
- Verlauf-Legende komplett neu: eigene, kompakte Legende (Label + Wert eng, feste Spalten,
  kein Springen). Marker als echtes Häkchen-Verhalten — aktiv = gefülltes Kästchen in
  Seriefarbe, inaktiv = leer/ausgegraut. Werte folgen dem Cursor (sonst letzter Wert).

## [0.6.1] — 2026-06-28
### Behoben
- CFET/DFET-Buttons sprangen beim Wechsel Laden/Entladen/Ruhe, weil das Statusbadge
  unterschiedlich breit war. Badge hat jetzt feste Breite -> Buttons bleiben fix.

## [0.6.0] — 2026-06-28
### Hinzugefügt
- **SQLite-Index** auf `(source, time)` — wird beim Start automatisch angelegt; History-
  Abfragen bleiben auch bei großen Datenbanken schnell.
- **Status-Anzeige** (live/veraltet + Zeitstempel) jetzt im Header **aller** Seiten
  (Monitor, Verlauf, Einstellungen).
- **Sichtbarer Zoom-Auswahlbereich** im Verlauf: eingefärbtes Rechteck beim Aufziehen
  plus Live-Anzeige der markierten Zeitspanne.

## [0.5.0] — 2026-06-28
### Dashboard
- Karten werden **in-place** aktualisiert (kein Neuaufbau mehr) → MOS-/Power-Off-Buttons
  flackern/springen nicht mehr bei Zustandswechsel.
- Eigener **Bestätigungsdialog** (Modal im Design) statt Browser-`confirm()`; Rückmeldungen
  als **Toast** statt `alert()`.
- Navigation auf allen Seiten identisch (gleicher Titel) → Menüpunkte springen nicht mehr.
- „Parameter (bald)" aus der Navigation entfernt.
### Verlauf / Chart
- **Bugfix**: Verlauf zeigte keine Daten (Zeitformat `from/to` vs. DB) — behoben.
- Default-Zeitraum beim Öffnen = jetzt − 24 h.
- **Zoom bleibt im Live-Modus erhalten** (wird nicht mehr bei jedem Update zurückgesetzt).
- **Strom** hat eine eigene Skala (unabhängig von der Gesamtspannung); Zellen links,
  Spannung und Strom je eigene Achse rechts.
- Legende mit fester Spaltenbreite → Werte verschieben das Layout nicht mehr.
### MQTT
- FET-Status/-Befehle unter `control/`: `bms/packX/control/cfet`(`/dfet`) und
  `…/control/cfet/set`, `…/control/dfet/set`, `…/control/poweroff/set`.

## [0.4.1] — 2026-06-28
### Behoben
- **DFET-Erkennung korrigiert**: jetzt byte35 Bit 0 (vorher fälschlich byte34 Bit 7,
  was nur die Stromrichtung anzeigte). Verifiziert in Ruhe, Laden und Entladen.
### Geändert
- Power-Off-Hinweis verkürzt auf „(kurzer RESET)" — EF wirkt auf dieser Firmware als
  kurzer Neustart (BMS kommt nach ~5 s von selbst zurück, unabhängig vom Polling).

## [0.4.0] — 2026-06-28
### Stabilität
- **Robustes Reconnect-Handling**: nicht-destruktiver Drain (killt den Socket nicht mehr),
  Reconnect mit Backoff (1→2→4→8→15 s), TCP-Keepalive, pro Bus serialisiert (Lock).
  Statt Log-Flut nur noch klare Meldungen „packN offline / wieder online".
### MOS / Steuerung
- MOS-Status **CFET/DFET** aus dem Status-Block dekodiert (Byte 35 Bit1 / Byte 34 Bit7),
  im Dashboard angezeigt und via MQTT veröffentlicht (`bms/packX/cfet`, `…/dfet`).
- MOS **schalten** direkt auf der Startseite (Buttons je Pack) mit Sicherheitsabfrage und
  Rücklese-Prüfung. Current-Limiting-Bit wird beim Schalten unverändert mitgesendet.
- MOS schalten **via MQTT**: `bms/packX/cfet/set` / `…/dfet/set` ← `true/false`.
- **Power Off** (CID2 EF) je Pack: Button (mit Warnhinweis) + MQTT `bms/packX/poweroff/set`.
### Visualisierung
- **Verlaufs-/Chart-Seite** (uPlot, offline): BMS-Auswahl, 16 Zellspannungen links,
  Strom & Gesamtspannung rechts (zwei Y-Achsen), Zoom in beiden Achsen, Zeitraumwahl,
  Live-Modus 5/10/30 min. Mehrfach öffenbar (zwei Tabs für BMS 1 & 2 nebeneinander).
### Datenhaltung / Config
- **DB-Cleanup**: `output.sqlite.retention_days` (täglich, `0` = aus).
- **Einstellungs-Seite**: alle sinnvollen Config-Werte lesen/setzen, Passwort maskiert,
  „Ungespeicherte Änderungen"-Hinweis + „Verwerfen" + „Speichern & Neustarten".
- Neuer Parameter `offline_after`.
### Betrieb
- systemd-Unit `deploy/vkbms.service` + `deploy/README-server.md` (absoluter DB-Pfad,
  `ReadWritePaths`, Selbst-Neustart, Netzwerk-Hinweise).

## [0.3.0] — 2026-06-27
### Hinzugefügt
- Versionsanzeige im Dashboard (Statuszeile).
- MQTT: Einzelzellspannungen als Unterordner `bms/packX/cells/01…16` (abschaltbar via `publish_cells`).
- MQTT: online/offline-Status — `bms/status` fürs Tool (via Last-Will) + `bms/packX/online` je BMS (mit Miss-Toleranz).
- Config: `web.access_log` (HTTP-Zugriffslogs, Standard aus).
- Config: `mqtt.publish_state_json` (Gesamt-JSON-Topic, Standard aus).
### Geändert
- MQTT kompatibel mit paho-mqtt 2.x; klare Verbindungsmeldung + Auto-Reconnect über Broker-Loop.
- Temperaturen als eigene Topics `bms/packX/temp/...`.
### Behoben
- `state`-JSON-Topic war redundant zu den Einzeltopics → jetzt standardmäßig aus.

## [0.2.0] — 2026-06-27
### Hinzugefügt
- Getrennte Intervalle: `poll_interval`, `live_interval`, `mqtt_interval`, `db_interval`.
- Auto-Checkpoint der SQLite-WAL → `bms.db` ist auch im Betrieb als Einzeldatei vollständig.
### Geändert
- Sauberes Beenden (Strg+C) ohne Traceback.

## [0.1.0] — 2026-06-27
### Hinzugefügt
- Protokoll-Kern (PACE/V52): Frame-Bau/-Parsing, Prüfsummen, Dekoder für Analog- und Statuswerte. Gegen echte Mitschnitte verifiziert.
- Transport: TCP (Gateway) und Serial (COM/pyserial) mit Reassemblierung bis CR.
- Datensenken: SQLite (WAL), CSV, MQTT.
- Headless-Logger (`run_logger.py`) und Web-Dashboard (`run_web.py`).
- Mehrere Busse, parallel gepollt; nur konfigurierte Pack-Adressen werden abgefragt.
- Live-Dashboard mit Zell-Balance-Leiste, Lade-/Entlade-Anzeige, Temperaturen.
