# Roadmap

## Erledigt in v0.4.0
Reconnect-Handling, MOS-Status + Schalten (GUI & MQTT), Power Off, Verlaufs-/Chart-Seite,
DB-Cleanup (retention_days), Einstellungs-Seite, systemd-Doku. Siehe CHANGELOG.md.

## Noch offen / Ideen
- **Parameter-Tab**: alle 105 Parameter lesen/schreiben (CID2 47/49) mit Rücklese-Prüfung.
- History-/Warnsätze des BMS auslesen (CID2 4B/4C).
- RTC stellen (4D/4E), Kapazität/Kalibrierung (E5/ED), Produktinfo (F1/F2).
- CSV-/Daten-Export aus der Chart-Seite.
- Optional: Authentifizierung für die Web-Oberfläche.

## Bekannte Einschränkungen
- Current-Limiting ist im Status nicht ablesbar -> kein Button; CL-Bit wird beim
  MOS-Schalten unveraendert mitgesendet (Tool-getrackt, Standard aus).
- "Power Off" (EF) schaltet ab/Sleep; das BMS kommt evtl. nicht von selbst zurueck.
