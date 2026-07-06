"""
Output sinks for analog readings.

  * SqliteSink -> robust local logging (WAL mode, one row per reading)
  * CsvSink    -> one CSV file per pack, append mode
  * MqttSink   -> publishes JSON + per-value topics (needs paho-mqtt)

All sinks are best-effort: a failing sink logs a warning and never crashes
the polling loop.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from typing import Optional

from .protocol import AnalogReading

log = logging.getLogger("vkbms.sinks")


def reading_to_dict(r: AnalogReading, source: str = "") -> dict:
    name = source or r.source or f"Pack {r.address}"
    d = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "source": name,           # unique identity (display + key + filename)
        "pack": r.address,        # on-wire address
        "balance": [bool((r.balance_mask >> i) & 1) for i in range(16)],
        "balance_mask": int(r.balance_mask),
        "warnings": ", ".join(r.warnings),
        "protections": ", ".join(r.protections),
        "alarm": 1 if (r.warnings or r.protections) else 0,
        "voltage_v": r.voltage_v,
        "current_a": r.current_a,
        "soc": r.soc,
        "soh": r.soh,
        "remain_ah": r.remain_ah,
        "full_ah": r.full_ah,
        "cycles": r.cycles,
        "min_mv": r.min_mv,
        "max_mv": r.max_mv,
        "delta_mv": r.delta_mv,
    }
    for i, mv in enumerate(r.cells_mv, 1):
        d[f"v{i:02d}"] = mv
    names = ["cell_t1", "cell_t2", "cell_t3", "cell_t4", "env_t", "mos_t"]
    for i, t in enumerate(r.temps_c):
        d[names[i] if i < len(names) else f"t{i}"] = t
    return d


class SqliteSink:
    realtime = False              # written only on the DB tick (db_interval)

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=5)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        cells = ", ".join(f"v{i:02d} INTEGER" for i in range(1, 17))
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                time TEXT, source TEXT, pack INTEGER,
                voltage_v REAL, current_a REAL, soc INTEGER, soh INTEGER,
                remain_ah REAL, full_ah REAL, cycles INTEGER,
                min_mv INTEGER, max_mv INTEGER, delta_mv INTEGER,
                cell_t1 INTEGER, cell_t2 INTEGER, cell_t3 INTEGER, cell_t4 INTEGER,
                env_t INTEGER, mos_t INTEGER,
                balance_mask INTEGER, warnings TEXT, protections TEXT, alarm INTEGER,
                {cells}
            )""")
        # Migration: fehlende Spalten in bestehenden DBs ergänzen
        have = {row[1] for row in self.conn.execute("PRAGMA table_info(readings)")}
        for col, typ in (("balance_mask", "INTEGER"), ("warnings", "TEXT"),
                         ("protections", "TEXT"), ("alarm", "INTEGER")):
            if col not in have:
                self.conn.execute(f"ALTER TABLE readings ADD COLUMN {col} {typ}")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_time ON readings(source, time)")
        self.conn.commit()

    def write(self, r: AnalogReading) -> None:
        d = reading_to_dict(r)
        cols = [k for k, v in d.items() if not isinstance(v, (list, dict))]
        try:
            self.conn.execute(
                f"INSERT INTO readings ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                [d[c] for c in cols],
            )
            self.conn.commit()
            # merge WAL into the main file so a live copy of bms.db is complete
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error as e:
            log.warning("sqlite write failed: %s", e)

    def close(self) -> None:
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.conn.close()
        except sqlite3.Error:
            pass


class CsvSink:
    realtime = False

    def __init__(self, directory: str):
        self.dir = directory
        os.makedirs(directory, exist_ok=True)
        self._headers: dict[int, list] = {}

    def write(self, r: AnalogReading) -> None:
        d = {k: v for k, v in reading_to_dict(r).items() if not isinstance(v, (list, dict))}
        safe = "".join(c if c.isalnum() else "_" for c in d["source"]).strip("_") or f"pack{r.address}"
        path = os.path.join(self.dir, f"{safe}.csv")
        new = not os.path.exists(path)
        try:
            with open(path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(d.keys()))
                if new:
                    w.writeheader()
                w.writerow(d)
        except OSError as e:
            log.warning("csv write failed: %s", e)

    def close(self) -> None:
        pass


class MqttSink:
    realtime = True               # published every poll (live)

    def __init__(self, host: str, port: int = 1883, base_topic: str = "bms",
                 username: Optional[str] = None, password: Optional[str] = None,
                 publish_state_json: bool = False, publish_cells: bool = True):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            raise RuntimeError("paho-mqtt not installed (pip install paho-mqtt)")
        self.base = base_topic.rstrip("/")
        self.publish_state_json = publish_state_json
        self.publish_cells = publish_cells
        self.status_topic = f"{self.base}/status"
        self.connected = False
        self._cmd_cb = None           # set via set_command_callback (engine)
        # Loop suppression for writable control objects (single object = status + command):
        #   _last_pub[topic] = last value WE published; an inbound message equal to it is
        #   our own echo and is ignored. A different value is a real user command.
        self._last_pub = {}
        #   _seen = pack keys we have already published control state for; retained
        #   startup values for unseen packs are ignored (no accidental switching).
        self._seen = set()

        # paho-mqtt 2.x requires an explicit callback API version; fall back to 1.x
        try:
            self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        except (AttributeError, TypeError):
            self.client = mqtt.Client()

        if username:
            self.client.username_pw_set(username, password or "")
        # Last Will: broker marks us offline automatically if we drop
        self.client.will_set(self.status_topic, "offline", retain=True)

        def on_connect(client, userdata, flags, reason_code, properties=None):
            code = getattr(reason_code, "value", reason_code)
            if code in (0, None):
                self.connected = True
                log.info("MQTT verbunden mit %s:%s", host, port)
                client.publish(self.status_topic, "online", retain=True)
                # One writable object per control (status + command in one topic):
                #   bms/<name>/pack<id>/control/{cfet,dfet,poweroff}
                for act in ("cfet", "dfet", "poweroff"):
                    client.subscribe(f"{self.base}/+/+/control/{act}")
            else:
                self.connected = False
                log.error("MQTT-Verbindung abgelehnt (Code %s) – Login/Port prüfen", code)

        def on_disconnect(client, userdata, *args):
            self.connected = False
            log.warning("MQTT getrennt – versuche erneut zu verbinden…")

        def on_message(client, userdata, msg):
            # Writable control object, e.g. bms/<name>/pack<id>/control/cfet
            #   -> pack_key = "<name>/pack<id>", action = "cfet"
            try:
                raw = msg.payload.decode().strip()
                if raw == "":
                    return                       # ignore empty (e.g. cleared retained)
                parts = msg.topic.split("/")
                action = parts[-1]
                if action not in ("cfet", "dfet", "poweroff"):
                    return
                pack_key = parts[-4] + "/" + parts[-3]
                # Ignore until we have published this pack's state at least once, so a
                # retained value delivered on (re)connect never triggers a switch.
                if pack_key not in self._seen:
                    return
                # Ignore our own echo: value equal to what we last published.
                if self._last_pub.get(msg.topic) == raw.lower():
                    return
                value = raw.lower() in ("1", "true", "on", "yes")
                if self._cmd_cb:
                    self._cmd_cb(pack_key, action, value)
                # poweroff is momentary: reset the object back to false right away.
                if action == "poweroff":
                    self._publish_control(pack_key, "poweroff", False)
            except Exception as e:  # noqa: BLE001
                log.warning("mqtt command ignoriert (%s): %s", msg.topic, e)

        self.client.on_connect = on_connect
        self.client.on_disconnect = on_disconnect
        self.client.on_message = on_message
        # async connect + background loop -> auto-reconnect, surfaces status via callbacks
        self.client.connect_async(host, port, keepalive=60)
        self.client.loop_start()

    def set_command_callback(self, cb) -> None:
        self._cmd_cb = cb

    def write(self, r: AnalogReading) -> None:
        d = reading_to_dict(r)
        prefix = f"{self.base}/{r.pack_key or ('pack%d' % r.address)}"
        try:
            for key in ("voltage_v", "current_a", "soc", "soh",
                        "remain_ah", "full_ah", "cycles",
                        "min_mv", "max_mv", "delta_mv"):
                self.client.publish(f"{prefix}/{key}", d[key], retain=True)
            for i in ("cell_t1", "cell_t2", "cell_t3", "cell_t4", "env_t", "mos_t"):
                if i in d:
                    self.client.publish(f"{prefix}/temp/{i}", d[i], retain=True)
            if self.publish_cells:
                for n in range(1, 17):
                    k = f"v{n:02d}"
                    if k in d:
                        self.client.publish(f"{prefix}/cells/{n:02d}", d[k], retain=True)
            # Balancing: Maske + Liste aktiver Zellen
            active = [str(i + 1) for i in range(16) if (r.balance_mask >> i) & 1]
            self.client.publish(f"{prefix}/balance_mask", d["balance_mask"], retain=True)
            self.client.publish(f"{prefix}/balancing", ",".join(active), retain=True)
            # Alarme / Schutz
            self.client.publish(f"{prefix}/alarm", d["alarm"], retain=True)
            self.client.publish(f"{prefix}/warnings", d["warnings"], retain=True)
            self.client.publish(f"{prefix}/protections", d["protections"], retain=True)
            if self.publish_state_json:
                self.client.publish(f"{prefix}/state", json.dumps(d), retain=True)
        except Exception as e:  # noqa: BLE001  (paho raises various)
            log.warning("mqtt publish failed: %s", e)

    def set_availability(self, pack_key: str, online: bool) -> None:
        """Publish per-pack online state, e.g. bms/pack1/online -> true/false."""
        try:
            self.client.publish(f"{self.base}/{pack_key}/online",
                                "true" if online else "false", retain=True)
        except Exception as e:  # noqa: BLE001
            log.warning("mqtt availability publish failed: %s", e)

    def _publish_control(self, pack_key: str, action: str, value: bool) -> None:
        """Publish a writable control object and remember the value we sent, so the
        broker's echo of our own message is not mistaken for a user command."""
        topic = f"{self.base}/{pack_key}/control/{action}"
        val = "true" if value else "false"
        self._last_pub[topic] = val
        try:
            self.client.publish(topic, val, retain=True)
        except Exception as e:  # noqa: BLE001
            log.warning("mqtt control publish failed: %s", e)

    def publish_mos(self, pack_key: str, cfet, dfet) -> None:
        """Mirror the current FET state into the writable control objects. The first
        call for a pack also creates the momentary poweroff object (baseline false)
        and marks the pack as seen (retained startup values are ignored until then)."""
        if cfet is not None:
            self._publish_control(pack_key, "cfet", bool(cfet))
        if dfet is not None:
            self._publish_control(pack_key, "dfet", bool(dfet))
        if pack_key not in self._seen:
            self._publish_control(pack_key, "poweroff", False)   # baseline, schaltbar
            self._seen.add(pack_key)

    def close(self) -> None:
        try:
            self.client.publish(self.status_topic, "offline", retain=True)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass


def build_sinks(cfg: dict) -> list:
    sinks = []
    out = cfg.get("output", {})
    if out.get("sqlite", {}).get("enabled", True):
        sinks.append(SqliteSink(out.get("sqlite", {}).get("path", "data/bms.db")))
    if out.get("csv", {}).get("enabled", False):
        sinks.append(CsvSink(out.get("csv", {}).get("dir", "data/csv")))
    mq = out.get("mqtt", {})
    if mq.get("enabled", False):
        try:
            sinks.append(MqttSink(
                host=mq["host"], port=int(mq.get("port", 1883)),
                base_topic=mq.get("base_topic", "bms"),
                username=mq.get("username"), password=mq.get("password"),
                publish_state_json=bool(mq.get("publish_state_json", False)),
                publish_cells=bool(mq.get("publish_cells", True)),
            ))
            log.info("MQTT sink connected to %s:%s", mq["host"], mq.get("port", 1883))
        except Exception as e:  # noqa: BLE001
            log.error("MQTT sink disabled: %s", e)
    return sinks
