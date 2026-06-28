# Server-Betrieb (Ubuntu + systemd)

Das Tool läuft als systemd-Dienst, z. B. unter `/opt/vkbms`.

## Installation
```bash
sudo mkdir -p /opt/vkbms/data
sudo cp -r vkbms run_web.py config.yaml /opt/vkbms/
sudo pip3 install flask pyyaml paho-mqtt --break-system-packages
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
- **Selbst-Neustart:** Der Button „Speichern & Neustarten" beendet den Prozess;
  `Restart=always` startet ihn neu. Ohne systemd (manueller Start) beendet sich
  das Tool nur und muss von Hand neu gestartet werden.
- **Netzwerk:** `IPAddressAllow` deckt `127.0.0.1` und `192.168.0.0/16` ab. Liegt
  dein MQTT-Broker/Client außerhalb dieses Bereichs, ergänze die Adresse.

## Nützliche Befehle
```bash
systemctl status vkbms
journalctl -u vkbms -f          # Live-Log
sudo systemctl restart vkbms
```
