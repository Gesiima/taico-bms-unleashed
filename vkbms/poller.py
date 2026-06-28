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
import time
from dataclasses import dataclass, field
from typing import Optional

from . import protocol as P
from .transport import BaseTransport, TransportError, make_transport

log = logging.getLogger("vkbms.poller")


@dataclass
class Bus:
    name: str
    transport: BaseTransport
    addresses: list[int]
    label: str = ""
    read_status: bool = True
    timeout: float = 1.0
    lock: threading.Lock = field(default_factory=threading.Lock)  # serialize socket use


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
        buses.append(Bus(
            name=b.get("name", f"bus{i}"),
            transport=make_transport(b["connection"]),
            addresses=_parse_addresses(b["addresses"]),
            label=b.get("label", b.get("name", f"bus{i}")),
            read_status=bool(b.get("read_status", True)),
            timeout=float(b.get("timeout", 1.0)),
        ))
    return buses


def poll_pack(bus: Bus, address: int):
    """Return (AnalogReading, StatusReading|None) or None on failure.

    Per-attempt failures are logged at DEBUG only; the Engine logs a single
    clear line when a pack changes between online and offline.
    """
    try:
        with bus.lock:
            resp = P.parse_frame(bus.transport.query(P.req_analog(address), bus.timeout))
        if not resp.ok:
            log.debug("[%s] pack %d analog RTN=%#x", bus.name, address, resp.rtn)
            return None
        analog = P.decode_analog(resp)
        analog.source = pack_identity(bus, address)
    except (TransportError, P.ProtocolError, IndexError) as e:
        log.debug("[%s] pack %d analog failed: %s", bus.name, address, e)
        return None

    status = None
    if bus.read_status:
        try:
            with bus.lock:
                sresp = P.parse_frame(bus.transport.query(P.req_status(address), bus.timeout))
            if sresp.ok:
                status = P.decode_status(sresp)
        except (TransportError, P.ProtocolError, IndexError) as e:
            log.debug("[%s] pack %d status failed: %s", bus.name, address, e)
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
        self._expected = [f"pack{a}" for b in self.buses for a in b.addresses]
        self._miss = {k: 0 for k in self._expected}
        self._online = {k: None for k in self._expected}
        # map pack key -> (bus, address) for MOS / power-off commands
        self._pack_map = {f"pack{a}": (b, a) for b in self.buses for a in b.addresses}
        # desired MOS state per pack; cfet/dfet mirror the BMS, cl is tool-tracked
        # (current-limiting is not readable from status, defaults off, no button)
        self.mos = {k: {"cfet": None, "dfet": None, "cl": False} for k in self._expected}

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
        return results

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
        responded = {f"pack{a.address}" for a, _ in readings}
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
                    log.info("%s wieder online", key)
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
            key = f"pack{analog.address}"
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
                dfet: Optional[bool] = None) -> dict:
        """Switch CFET/DFET. Sends the FULL desired mask (E2); CL bit is kept
        from the tracked state. Reads status back to verify."""
        if pack_key not in self._pack_map:
            return {"ok": False, "error": f"unbekannter Pack {pack_key}"}
        bus, addr = self._pack_map[pack_key]
        st = self.mos[pack_key]
        want_cfet = st["cfet"] if cfet is None else bool(cfet)
        want_dfet = st["dfet"] if dfet is None else bool(dfet)
        mask = ((P.MOS_CFET if want_cfet else 0)
                | (P.MOS_DFET if want_dfet else 0)
                | (P.MOS_CURRENT_LIMIT if st["cl"] else 0))
        try:
            with bus.lock:
                bus.transport.query(P.req_mos_control(addr, mask), bus.timeout)
                time.sleep(0.3)
                sresp = P.parse_frame(bus.transport.query(P.req_status(addr), bus.timeout))
            status = P.decode_status(sresp) if sresp.ok else None
        except (TransportError, P.ProtocolError, IndexError) as e:
            log.warning("set_mos %s failed: %s", pack_key, e)
            return {"ok": False, "error": str(e)}
        if status:
            st["cfet"], st["dfet"] = status.cfet_on, status.dfet_on
        log.info("MOS %s -> CFET=%s DFET=%s (gewünscht %s/%s)",
                 pack_key, st["cfet"], st["dfet"], want_cfet, want_dfet)
        return {"ok": True, "cfet": st["cfet"], "dfet": st["dfet"],
                "verified": bool(status and status.cfet_on == want_cfet
                                 and status.dfet_on == want_dfet)}

    def power_off(self, pack_key: str) -> dict:
        if pack_key not in self._pack_map:
            return {"ok": False, "error": f"unbekannter Pack {pack_key}"}
        bus, addr = self._pack_map[pack_key]
        try:
            with bus.lock:
                bus.transport.query(P.req_power_off(addr), bus.timeout)
        except (TransportError, P.ProtocolError) as e:
            log.warning("power_off %s failed: %s", pack_key, e)
            return {"ok": False, "error": str(e)}
        log.info("Power Off an %s gesendet", pack_key)
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
                self.set_mos(pack_key, cfet=value)
            elif action == "dfet":
                self.set_mos(pack_key, dfet=value)
            elif action == "poweroff" and value:
                self.power_off(pack_key)
        for s in self.sinks:
            if hasattr(s, "set_command_callback"):
                s.set_command_callback(on_cmd)

    def close(self) -> None:
        self._pool.shutdown(wait=False)
        for s in self.sinks:
            s.close()
        for bus in self.buses:
            bus.transport.close()


def run(cfg: dict) -> None:
    logging.basicConfig(
        level=getattr(logging, cfg.get("log_level", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
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
