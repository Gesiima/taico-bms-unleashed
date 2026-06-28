# Roadmap

Erledigte Releases sind in `CHANGELOG.md` dokumentiert. Hier nur der Ausblick.

## Geplant / offen

**GUI / Bedienung**
- Mobile-Optimierung der gesamten Oberfläche (niedrige Priorität; Referenz: Chrome
  auf Pixel 6): kompaktere Chart-Toolbar, responsive Navigation, größere Tap-Flächen,
  Zell-Strip-Skalierung, einspaltige Einstellungen.
- Touch-/Pinch-Zoom im Chart.
- Eigener Bestätigungsdialog/Toast auch auf Chart- und Einstellungs-Seite
  (Dashboard hat ihn bereits).

**Alerts / Schutz**
- Weitere Warn-/Schutzarten dekodieren (Unterspannung, Übertemperatur, Überstrom …),
  sobald Mitschnitte mit diesen Zuständen vorliegen. Aktuell gesichert: Zelle/Pack
  Überspannung (Warnung) und Zelle Überspannung (Schutz).
- Optional: Warnungen/Schutz zusätzlich über MQTT veröffentlichen.

**Protokoll-Funktionen (Kern vorhanden, GUI offen)**
- Parameter-Tab: alle 105 Parameter lesen/schreiben (CID2 47/49) mit Rücklese-Prüfung.
- History-/Warnsätze des BMS auslesen (CID2 4B/4C).
- RTC stellen (4D/4E), Kapazität/Kalibrierung (E5/ED), Produktinfo (F1/F2).

**Daten**
- CSV-/Daten-Export direkt aus der Chart-Seite.

**Sicherheit**
- Optional: Authentifizierung für die Web-Oberfläche.

## Bekannte Einschränkungen
- **Current-Limiting** ist im Status nicht ablesbar → kein eigener Button; das CL-Bit
  wird beim MOS-Schalten unverändert mitgesendet (Tool-getrackt, Standard aus).
- **Power Off (EF)** löst auf dieser Firmware einen kurzen Reset/Reboot aus; das BMS
  kommt von selbst zurück und setzt dabei latched Warnungen/Schutz zurück.
- Das Gateway erlaubt nur **eine** gleichzeitige TCP-Verbindung pro Port
  (`Max Accept = 1`).
