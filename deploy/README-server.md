# Server-Betrieb (Ubuntu + systemd)

Das Tool läuft als systemd-Dienst, z. B. unter `/opt/vkbms`.

## Installation
```bash
sudo mkdir -p /opt/vkbms/data
sudo cp -r vkbms run_web.py config.yaml /opt/vkbms/
sudo pip3 install flask pyyaml paho-mqtt --break-system-packages
# pyserial nur nötig, wenn ein Bus type: serial (direkter COM-Port) nutzt:
# sudo pip3 install pyserial --break-system-packages
sudo cp deploy/vkbms.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vkbms
```

## Wichtige Punkte in der Unit (`deploy/vkbms.service`)
- **Absoluter DB-Pfad empfohlen.** In `config.yaml`:
  ```yaml
  output:
    sqlite:
      path: /opt/vkbms/data/bms.db
  ```
- **`ReadWritePaths=/opt/vkbms`** ist nötig, weil `ProtectSystem=full` sonst das
  Schreiben verhindern könnte — gebraucht für die Datenbank **und** für die
  Settings-Seite, die `config.yaml` zurückschreibt.
- **Selbst-Neustart:** Die Buttons „Speichern & Neustarten" und „Dienst neu starten"
  auf der Einstellungs-Seite beenden den Prozess; `Restart=always` startet ihn neu.
  Ohne systemd (manueller Start) beendet sich das Tool nur und muss von Hand neu
  gestartet werden.
- **Konfiguration:** Busse (TCP/Serial), Adressen, Intervalle, Alert-Schwelle und MQTT
  lassen sich komplett über die Einstellungs-Seite pflegen; sie schreibt `config.yaml`
  zurück (daher `ReadWritePaths` nötig).
- **Netzwerk:** `IPAddressAllow` deckt `127.0.0.1` und `192.168.0.0/16` ab. Liegt
  dein MQTT-Broker/Client außerhalb dieses Bereichs, ergänze die Adresse.

## Nützliche Befehle
```bash
systemctl status vkbms
journalctl -u vkbms -f          # Live-Log
sudo systemctl restart vkbms
```
