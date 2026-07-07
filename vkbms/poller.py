"""
Polling engine + headless logger entry point.

A "bus" is one transport (TCP or serial) carrying one or more pack addresses.
Only the addresses you configure are polled, directly, with a short timeout —
so missing packs never stall the loop (unlike the vendor app, which blindly
sweeps addresses 1..8).
"""
from __future__ import annotations

import logging
import threading
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from . import protocol as P
from .transport import BaseTransport, TransportError, make_transport

log = logging.getLogger("vkbms.poller")


def setup_logging(cfg: dict) -> None:
    """Configure root logging once, for both entrypoints (web + logger).

    Console/journal gets `log_level` (default INFO) so the systemd journal stays
    lean. If `log_file.enabled`, a daily-rotating file additionally captures down
    to its own level (default DEBUG), so verbose per-bus DEBUG / raw frames land
    in the file — not the journal. Retention = number of daily files kept.
    """
    import os
    from logging.handlers import TimedRotatingFileHandler

    console_level = getattr(logging, str(cfg.get("log_level", "INFO")).upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    lf = cfg.get("log_file", {}) or {}
    file_enabled = bool(lf.get("enabled", False))
    file_level = getattr(logging, str(lf.get("level", "DEBUG")).upper(), logging.DEBUG)

    root = logging.getLogger()
    # root must pass the lowest level so the file handler can still see DEBUG
    root.setLevel(min(console_level, file_level) if file_enabled else console_level)
    for h in list(root.handlers):        # idempotent: clear handlers on re-init
        root.removeHandler(h)

    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(fmt)
    root.addHandler(console)

    if file_enabled:
        path = lf.get("path", "data/logs/vkbms.log")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        retention = int(lf.get("retention_days", 2))
        fh = TimedRotatingFileHandler(path, when="midnight", interval=1,
                                      backupCount=retention, encoding="utf-8")
        fh.setLevel(file_level)
        fh.setFormatter(fmt)
        root.addHandler(fh)
        log.info("Logdatei aktiv: %s (Ebene %s, Aufbewahrung %d Tage)",
                 path, logging.getLevelName(file_level), retention)

    # keep third-party noise out of the (DEBUG) file/journal
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


@dataclass
class Bus:
    name: str
    transport: BaseTransport
    addresses: list[int]
    label: str = ""
    read_status: bool = True
    timeout: float = 1.0
    lock: threading.Lock = field(default_factory=threading.Lock)  # serialize socket use
    logger: logging.Logger = field(default_factory=lambda: log)   # per-bus child logger
    debug_raw: bool = False                                       # log full TX/RX hex frames
    master_address: Optional[int] = None    # directly-wired pack (only it may Power Off)


def _hex(data) -> str:
    """Uppercase, space-separated hex of a frame (bytes or str) for raw DEBUG dumps."""
    if isinstance(data, str):
        data = data.encode("latin-1", "replace")
    return " ".join(f"{b:02X}" for b in data)


def _slug(name: str) -> str:
    """Topic-/Key-tauglicher Slug aus einem Bus-Namen, z. B. "BMS 1" -> "bms1"."""
    s = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    return s or "bms"


def pack_key(bus: "Bus", address: int) -> str:
    """Eindeutiger Schlüssel je Pack über Bus + Adresse (kollisionsfrei auch bei
    gleicher Adresse auf verschiedenen Bussen). Form: "<busslug>/pack<addr>"."""
    return f"{_slug(bus.name)}/pack{address}"


def pack_identity(bus: Bus, address: int) -> str:
    """Unique, human-readable identity per pack (handles same address on
    different buses, e.g. two BMS on separate gateway ports)."""
    label = bus.label or bus.name
    if len(bus.addresses) > 1:
        return f"{label} · #{address}"
    return label


def _parse_addresses(value) -> list[int]:
    """Accept a YAML list ([1,2]), a single int, or a string like '1, 2' / '1 2'."""
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        parts = value.replace(",", " ").split()
        return [int(p) for p in parts]
    out = []
    for a in value:
        if isinstance(a, str):
            out.extend(int(p) for p in a.replace(",", " ").split())
        else:
            out.append(int(a))
    return out


def build_buses(cfg: dict) -> list[Bus]:
    buses = []
    for i, b in enumerate(cfg.get("buses", [])):
        if not b.get("enabled", True):
            continue                       # persistent „inaktiv" -> Bus nicht aufbauen
        name = b.get("name", f"bus{i}")
        # per-bus child logger: setting its level to DEBUG surfaces this bus's detail
        # even when the root level is higher (INFO/WARNING).
        blog = logging.getLogger(f"vkbms.bus.{_slug(name)}")
        lvl = b.get("log_level")
        if lvl:
            blog.setLevel(str(lvl).upper())
        ma = b.get("master_address")
        try:
            ma = int(ma) if ma not in (None, "") else None
        except (TypeError, ValueError):
            ma = None
        # New model: main_address (single, required) + sub_addresses (optional, many).
        # Legacy fallback: addresses[] (+ master_address) for configs not yet re-saved.
        main = b.get("main_address")
        try:
            main = int(main) if main not in (None, "") else None
        except (TypeError, ValueError):
            main = None
        if main is not None:
            subs = [a for a in _parse_addresses(b.get("sub_addresses", []) or []) if a != main]
            addresses = [main] + subs
            master = main
        else:
            addresses = _parse_addresses(b.get("addresses", []) or [])
            master = ma
        if not addresses:
            logging.getLogger("vkbms.poller").warning(
                "Bus '%s' ohne Adresse (main_address fehlt) – übersprungen", name)
            continue
        buses.append(Bus(
            name=name,
            transport=make_transport(b["connection"]),
            addresses=addresses,
            label=b.get("label", name),
            read_status=bool(b.get("read_status", True)),
            timeout=float(b.get("timeout", 1.0)),
            logger=blog,
            debug_raw=bool(b.get("debug_raw_frames", False)),
            master_address=master,
        ))
    return buses


def _bus_query(bus: Bus, req, label: str):
    """Send one request and return the parsed frame. When the bus has
    debug_raw enabled, log the full TX/RX frames as hex."""
    lg = bus.logger
    if bus.debug_raw:
        lg.debug("TX %s: %s", label, _hex(req))
    raw = bus.transport.query(req, bus.timeout)
    if bus.debug_raw:
        lg.debug("RX %s: %s", label, _hex(raw))
    return P.parse_frame(raw)


def poll_pack(bus: Bus, address: int):
    """Return (AnalogReading, StatusReading|None) or None on failure.

    Per-attempt failures are logged at DEBUG only; the Engine logs a single
    clear line when a pack changes between online and offline. With the bus
    logger at DEBUG, each request is echoed in human-readable form (and, with
    debug_raw_frames, as raw hex).
    """
    lg = bus.logger
    try:
        lg.debug("→ Analog anfordern (Adr %d)", address)
        with bus.lock:
            resp = _bus_query(bus, P.req_analog(address), f"analog adr{address}")
        if not resp.ok:
            lg.debug("[%s] pack %d analog RTN=%#x", bus.name, address, resp.rtn)
            return None
        analog = P.decode_analog(resp)
        analog.source = pack_identity(bus, address)
        analog.pack_key = pack_key(bus, address)
        if analog.cells_mv:
            lg.debug("← Analog: U=%.2f V, I=%.2f A, SOC=%d %%, Zellen min %d / max %d mV (Δ %d)",
                     analog.voltage_v, analog.current_a, analog.soc,
                     min(analog.cells_mv), max(analog.cells_mv),
                     max(analog.cells_mv) - min(analog.cells_mv))
    except (TransportError, P.ProtocolError, IndexError) as e:
        lg.debug("[%s] pack %d analog failed: %s", bus.name, address, e)
        return None

    status = None
    if bus.read_status:
        try:
            lg.debug("→ Status anfordern (Adr %d)", address)
            with bus.lock:
                sresp = _bus_query(bus, P.req_status(address), f"status adr{address}")
            if sresp.ok:
                status = P.decode_status(sresp)
                lg.debug("← Status: CFET=%s DFET=%s Warnungen=[%s] Schutz=[%s]",
                         status.cfet_on, status.dfet_on,
                         ", ".join(status.warnings), ", ".join(status.protections))
        except (TransportError, P.ProtocolError, IndexError) as e:
            lg.debug("[%s] pack %d status failed: %s", bus.name, address, e)
    if status is not None:
        analog.warnings = status.warnings
        analog.protections = status.protections
    return analog, status


class Engine:
    """Polls all buses (in parallel) and routes readings to sinks.

    Live state and realtime sinks (MQTT) update every poll; logging sinks
    (SQLite, CSV) are throttled to `db_interval` so the dashboard can be fast
    while the database stays compact.
    """

    def __init__(self, cfg: dict):
        from concurrent.futures import ThreadPoolExecutor
        from .sinks import build_sinks
        self.buses = build_buses(cfg)
        self.sinks = build_sinks(cfg)
        # poll_interval = how often the BMS is queried (the freshness floor).
        # The three outputs are throttled independently and each gets the
        # latest poll; defaults keep live/mqtt at the poll rate, DB slower.
        self.poll_interval = float(cfg.get("poll_interval", 1.0))
        self.live_interval = float(cfg.get("live_interval", self.poll_interval))
        self.mqtt_interval = float(cfg.get("mqtt_interval", self.poll_interval))
        self.db_interval = float(cfg.get("db_interval", 10.0))
        self._pool = ThreadPoolExecutor(max_workers=max(1, len(self.buses)))
        self._next = {"live": 0.0, "mqtt": 0.0, "db": 0.0}
        self.paused = set()           # bus names paused at runtime
        # per-pack availability tracking (online/offline via MQTT)
        self.offline_after = int(cfg.get("offline_after", 3))
        active = [pack_key(b, a) for b in self.buses for a in b.addresses]
        # auch deaktivierte Busse kennen, damit sie in MQTT als offline erscheinen
        disabled = []
        for bd in cfg.get("buses", []):
            if bd.get("enabled", True):
                continue
            slug = _slug(bd.get("name", ""))
            for a in _parse_addresses(bd.get("addresses", [])):
                disabled.append(f"{slug}/pack{a}")
        self._expected = active + disabled
        self._miss = {k: 0 for k in self._expected}
        self._online = {k: None for k in self._expected}
        # map pack key -> (bus, address) for MOS / power-off commands (nur aktive)
        self._pack_map = {pack_key(b, a): (b, a) for b in self.buses for a in b.addresses}
        # desired MOS state per pack; cfet/dfet mirror the BMS, cl is tool-tracked
        self.mos = {k: {"cfet": None, "dfet": None, "cl": False} for k in active}
        # Master pack (directly wired) per bus: only it may Power Off.
        # Effective master = configured master_address, or the sole address on a
        # single-pack bus, else None (multi-pack bus without master configured).
        def _eff_master(b):
            if b.master_address is not None:
                return b.master_address if b.master_address in b.addresses else None
            return b.addresses[0] if len(b.addresses) == 1 else None
        self._bus_master = {b.name: _eff_master(b) for b in self.buses}
        self._master_keys = {pack_key(b, self._bus_master[b.name]) for b in self.buses
                             if self._bus_master[b.name] is not None}
        # Power-Off-Nachlauf: erwartetes Offline direkt nach einem Power Off nicht
        # als WARNING, sondern als erwartet loggen (Fenster in Sekunden).
        self._po_grace = {}           # pack_key -> monotonic deadline
        self._po_offline = set()      # packs currently offline due to a power off
        self._product = {}            # pack_key -> {manufacturer,model,version,serial}
        # DEBUG-Auto-Reset (nur Laufzeit): erhoehtes Logging nach X Minuten zuruecksetzen
        self._debug_timer = None
        self._schedule_debug_auto_reset(cfg)

    def _debug_active(self) -> bool:
        """True only if *elevated* logging is on that auto-reset should undo:
        the console/journal at DEBUG, or a bus explicitly at DEBUG / raw frames.
        A DEBUG log FILE is the intended persistent state and is NOT counted."""
        root = logging.getLogger()
        console_debug = any(
            isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
            and h.level and h.level <= logging.DEBUG
            for h in root.handlers)
        bus_debug = any(
            b.debug_raw or (b.logger.level and b.logger.level <= logging.DEBUG)
            for b in self.buses)
        return console_debug or bus_debug

    def _schedule_debug_auto_reset(self, cfg: dict) -> None:
        dar = cfg.get("debug_auto_reset", {}) or {}
        minutes = int(dar.get("minutes", 30))
        if not dar.get("enabled") or minutes <= 0 or not self._debug_active():
            return
        import threading as _t
        self._debug_timer = _t.Timer(minutes * 60, self._reset_debug, [minutes])
        self._debug_timer.daemon = True
        self._debug_timer.start()
        log.info("DEBUG-Auto-Reset aktiv: setzt erhoehtes Logging nach %d min zurueck", minutes)

    def _reset_debug(self, minutes: int) -> None:
        """Lower verbose logging back to INFO (runtime only; config unchanged)."""
        root = logging.getLogger()
        for h in root.handlers:                       # console handler -> INFO
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                h.setLevel(logging.INFO)
        if root.level < logging.INFO and not any(isinstance(h, logging.FileHandler)
                                                 for h in root.handlers):
            root.setLevel(logging.INFO)
        for b in self.buses:                          # per-bus back to INFO / raw off
            b.debug_raw = False
            b.logger.setLevel(logging.INFO)
        log.info("DEBUG automatisch zurueckgesetzt (nach %d min) -> Level INFO", minutes)

    def _due(self, now: float) -> dict:
        out = {}
        for key, interval in (("live", self.live_interval),
                              ("mqtt", self.mqtt_interval),
                              ("db", self.db_interval)):
            due = now >= self._next[key]
            if due:
                self._next[key] = now + interval
            out[key] = due
        return out

    def _poll_bus(self, bus: Bus):
        out = []
        for addr in bus.addresses:
            r = poll_pack(bus, addr)
            if r is not None:
                out.append(r)
        return out

    def poll_cycle(self):
        """Poll every active (non-paused) bus in parallel; return readings."""
        active = [b for b in self.buses if b.name not in self.paused]
        results = []
        for fut in [self._pool.submit(self._poll_bus, b) for b in active]:
            try:
                results.extend(fut.result())
            except Exception as e:  # noqa: BLE001
                log.warning("bus poll failed: %s", e)
        # Static product info (serial/model/firmware): fetch once per pack, then cache.
        for analog, _ in results:
            if analog.pack_key not in self._product:
                bus, addr = self._pack_map.get(analog.pack_key, (None, None))
                if bus is not None:
                    self._fetch_product(bus, addr, analog.pack_key)
        return results

    def _fetch_product(self, bus, addr, key) -> None:
        try:
            with bus.lock:
                resp = P.parse_frame(bus.transport.query(P.req_product_info(addr), bus.timeout))
            if not resp.ok:
                return
            pi = P.decode_product_info(resp)
        except (TransportError, P.ProtocolError, IndexError) as e:
            bus.logger.debug("[%s] pack %d product info failed: %s", bus.name, addr, e)
            return
        info = {"manufacturer": pi.manufacturer, "model": pi.model,
                "version": pi.version, "serial": pi.serial}
        self._product[key] = info
        bus.logger.info("Produktinfo %s: %s SN %s FW %s",
                        key, pi.model or "?", pi.serial or "?", pi.version or "?")
        for s in self.sinks:                 # einmalig (retained) an MQTT melden
            if hasattr(s, "publish_info"):
                s.publish_info(key, info)

    def pause_bus(self, name: str, paused: bool) -> dict:
        """Pause/resume polling of a bus at runtime; paused frees the connection."""
        found = next((b for b in self.buses if b.name == name), None)
        if not found:
            return {"ok": False, "error": f"unbekannter Bus {name}"}
        if paused:
            self.paused.add(name)
            with found.lock:
                found.transport.close()        # Slot freigeben (z. B. für VCOM)
            log.info("Bus %s pausiert (Verbindung freigegeben)", name)
        else:
            self.paused.discard(name)
            log.info("Bus %s fortgesetzt", name)
        return {"ok": True, "paused": paused}

    def _update_availability(self, readings) -> None:
        """Mark each expected pack online/offline (with miss tolerance), log the
        transition once, and push it to sinks that support it (MQTT)."""
        now = time.monotonic()
        responded = {a.pack_key for a, _ in readings}
        for key in self._expected:
            if key in responded:
                self._miss[key] = 0
                online = True
            else:
                self._miss[key] += 1
                if self._miss[key] >= self.offline_after:
                    online = False
                else:
                    continue  # within tolerance: keep current state
            if self._online[key] != online:
                self._online[key] = online
                if online:
                    if key in self._po_offline:
                        log.info("%s nach Power Off wieder online", key)
                        self._po_offline.discard(key)
                    else:
                        log.info("%s wieder online", key)
                else:
                    if self._po_grace.get(key, 0.0) > now:
                        # offline is the expected consequence of a recent Power Off
                        log.info("%s nach Power Off vorübergehend offline (erwartet)", key)
                        self._po_offline.add(key)
                    else:
                        log.warning("%s offline (keine Antwort)", key)
                for s in self.sinks:
                    if hasattr(s, "set_availability"):
                        s.set_availability(key, online)

    def _update_mos(self, readings) -> None:
        """Mirror CFET/DFET state from the status block and publish via MQTT."""
        for analog, status in readings:
            if status is None:
                continue
            key = analog.pack_key
            if key not in self.mos:
                continue
            if status.cfet_on is not None:
                self.mos[key]["cfet"] = status.cfet_on
            if status.dfet_on is not None:
                self.mos[key]["dfet"] = status.dfet_on
            for s in self.sinks:
                if hasattr(s, "publish_mos"):
                    s.publish_mos(key, status.cfet_on, status.dfet_on)

    def set_mos(self, pack_key: str, cfet: Optional[bool] = None,
                dfet: Optional[bool] = None, source: str = "Web") -> dict:
        """Switch CFET/DFET. Sends the FULL desired mask (E2); CL bit is kept
        from the tracked state. Reads status back to verify. `source` (Web/MQTT)
        is recorded in the log so the command's origin is visible."""
        if pack_key not in self._pack_map:
            return {"ok": False, "error": f"unbekannter Pack {pack_key}"}
        bus, addr = self._pack_map[pack_key]
        st = self.mos[pack_key]
        want_cfet = st["cfet"] if cfet is None else bool(cfet)
        want_dfet = st["dfet"] if dfet is None else bool(dfet)
        mask = ((P.MOS_CFET if want_cfet else 0)
                | (P.MOS_DFET if want_dfet else 0)
                | (P.MOS_CURRENT_LIMIT if st["cl"] else 0))
        status = None
        lg = bus.logger
        lg.debug("→ MOS setzen (Adr %d): CFET=%s DFET=%s (Maske %#04x)",
                 addr, want_cfet, want_dfet, mask)
        try:
            with bus.lock:
                cmd = P.req_mos_control(addr, mask)
                if bus.debug_raw:
                    lg.debug("TX mos adr%d: %s", addr, _hex(cmd))
                raw = bus.transport.query(cmd, bus.timeout)
                if bus.debug_raw:
                    lg.debug("RX mos adr%d: %s", addr, _hex(raw))
                deadline = time.monotonic() + 2.0
                while True:
                    time.sleep(0.3)
                    try:
                        sresp = _bus_query(bus, P.req_status(addr), f"status adr{addr}")
                        status = P.decode_status(sresp) if sresp.ok else status
                    except (TransportError, P.ProtocolError, IndexError):
                        pass  # einzelne Lesefehler tolerieren, bis Deadline
                    if status and status.cfet_on == want_cfet and status.dfet_on == want_dfet:
                        break
                    if time.monotonic() >= deadline:
                        break
        except (TransportError, P.ProtocolError, IndexError) as e:
            log.warning("set_mos %s failed: %s", pack_key, e)
            return {"ok": False, "error": str(e)}
        if status:
            st["cfet"], st["dfet"] = status.cfet_on, status.dfet_on
        lg.debug("← MOS Rücklese (Adr %d): CFET=%s DFET=%s", addr, st["cfet"], st["dfet"])
        log.info("MOS %s -> CFET=%s DFET=%s (gewünscht %s/%s) [Quelle: %s]",
                 pack_key, st["cfet"], st["dfet"], want_cfet, want_dfet, source)
        return {"ok": True, "cfet": st["cfet"], "dfet": st["dfet"],
                "verified": bool(status and status.cfet_on == want_cfet
                                 and status.dfet_on == want_dfet)}

    def power_off(self, pack_key: str, source: str = "Web") -> dict:
        if pack_key not in self._pack_map:
            return {"ok": False, "error": f"unbekannter Pack {pack_key}"}
        bus, addr = self._pack_map[pack_key]
        # Power Off (EF) only works on the directly-wired master pack.
        if bus.master_address is None:
            msg = ("Master-Pack nicht konfiguriert – bitte für diesen Bus die "
                   "master_address (direkt angebundenes Pack) in den Einstellungen setzen.")
            log.warning("Power Off abgelehnt für %s: nicht konfiguriert [Quelle: %s]", pack_key, source)
            return {"ok": False, "error": msg, "code": "no_master"}
        if bus.master_address not in bus.addresses:
            msg = (f"Konfiguriertes Master-Pack (Adresse {bus.master_address}) ist auf "
                   f"Bus '{bus.name}' nicht vorhanden - bitte master_address korrigieren.")
            log.warning("Power Off abgelehnt für %s: Master-Adresse %s nicht am Bus [Quelle: %s]",
                        pack_key, bus.master_address, source)
            return {"ok": False, "error": msg, "code": "master_missing"}
        if addr != bus.master_address:
            msg = ("Power Off ist nur am Master-Pack (direkt angebunden) möglich, nicht an "
                   "einem über den Adressbus angesprochenen Sub-Pack.")
            log.warning("Power Off abgelehnt für %s: kein Master (Master=Adr %s) [Quelle: %s]",
                        pack_key, bus.master_address, source)
            return {"ok": False, "error": msg, "code": "not_master"}
        try:
            with bus.lock:
                bus.transport.query(P.req_power_off(addr), bus.timeout)
        except (TransportError, P.ProtocolError) as e:
            log.warning("power_off %s failed: %s", pack_key, e)
            return {"ok": False, "error": str(e)}
        # expected ~5s reset -> classify the following offline as expected, not a fault
        self._po_grace[pack_key] = time.monotonic() + 12.0
        log.info("Power Off an %s gesendet [Quelle: %s]", pack_key, source)
        return {"ok": True}

    def dispatch(self, readings, on_reading=None) -> None:
        now = time.monotonic()
        due = self._due(now)
        self._update_availability(readings)   # status changes propagate every cycle
        self._update_mos(readings)
        for analog, status in readings:
            if due["live"] and on_reading:
                on_reading(analog, status)
            for s in self.sinks:
                cat = "mqtt" if getattr(s, "realtime", False) else "db"
                if due[cat]:
                    try:
                        s.write(analog)
                    except Exception as e:  # noqa: BLE001
                        log.warning("sink %s failed: %s", type(s).__name__, e)

    def wire_commands(self) -> None:
        """Let MQTT command topics drive set_mos / power_off."""
        def on_cmd(pack_key: str, action: str, value: bool):
            if action == "cfet":
                self.set_mos(pack_key, cfet=value, source="MQTT")
            elif action == "dfet":
                self.set_mos(pack_key, dfet=value, source="MQTT")
            elif action == "poweroff" and value:
                self.power_off(pack_key, source="MQTT")
        for s in self.sinks:
            if hasattr(s, "set_command_callback"):
                s.set_command_callback(on_cmd)

    def close(self) -> None:
        if self._debug_timer is not None:
            self._debug_timer.cancel()
        self._pool.shutdown(wait=False)
        for s in self.sinks:
            s.close()
        for bus in self.buses:
            bus.transport.close()


def run(cfg: dict) -> None:
    setup_logging(cfg)
    engine = Engine(cfg)
    log.info("Logger: %d bus(es) | poll %.1fs · live %.1fs · mqtt %.1fs · db %.1fs | %d sink(s)",
             len(engine.buses), engine.poll_interval, engine.live_interval,
             engine.mqtt_interval, engine.db_interval, len(engine.sinks))

    def show(analog, status):
        alarm = " ALARM" if (status and status.any_alarm) else ""
        log.info("%s: %.2fV %+.2fA SOC %d%% cells %d-%dmV (Δ%d) T%s%s",
                 analog.source or f"pack {analog.address}", analog.voltage_v,
                 analog.current_a, analog.soc, analog.min_mv, analog.max_mv,
                 analog.delta_mv, analog.temps_c, alarm)

    try:
        while True:
            start = time.monotonic()
            engine.dispatch(engine.poll_cycle(), on_reading=show)
            time.sleep(max(0.0, engine.poll_interval - (time.monotonic() - start)))
    except KeyboardInterrupt:
        log.info("Stopping (Ctrl-C)")
    finally:
        engine.close()
