"""
Local web server: runs the poller in a background thread, keeps the latest
reading per pack in memory, writes to the configured sinks, and serves the
dashboard, history charts, settings page and a JSON API.

Run with: python run_web.py [config.yaml]
"""
from __future__ import annotations

import logging
import os
import ipaddress
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from .sinks import reading_to_dict
from . import __version__

log = logging.getLogger("vkbms.server")

_state_lock = threading.Lock()
_state: dict = {"packs": {}, "updated": None, "connected": False, "error": None}
_engine = None                 # set when the background loop starts
_config_path = None            # path to config.yaml (for the settings page)
_cfg: dict = {}


# ---------------------------------------------------------------- background
def _db_path() -> str | None:
    sq = _cfg.get("output", {}).get("sqlite", {})
    if not sq.get("enabled"):
        return None
    return os.path.abspath(sq.get("path", "data/bms.db"))


def _cleanup_loop(stop: threading.Event) -> None:
    """Delete rows older than retention_days, once per day. 0 = disabled."""
    days = int(_cfg.get("output", {}).get("sqlite", {}).get("retention_days", 0))
    path = _db_path()
    if not days or not path:
        return
    while not stop.is_set():
        try:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            con = sqlite3.connect(path)
            n = con.execute("DELETE FROM readings WHERE time < ?", (cutoff,)).rowcount
            con.commit()
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.close()
            if n:
                log.info("DB-Cleanup: %d Zeilen älter als %d Tage gelöscht", n, days)
        except sqlite3.Error as e:
            log.warning("DB-Cleanup fehlgeschlagen: %s", e)
        stop.wait(24 * 3600)


def _background_loop(cfg: dict, stop: threading.Event) -> None:
    global _engine
    from .poller import Engine
    _engine = Engine(cfg)
    _engine.wire_commands()
    log.info("Background poller: %d bus(es) | poll %.1fs · live %.1fs · mqtt %.1fs · db %.1fs",
             len(_engine.buses), _engine.poll_interval, _engine.live_interval,
             _engine.mqtt_interval, _engine.db_interval)

    def on_reading(analog, status):
        d = reading_to_dict(analog)
        d["alarm"] = bool(status and status.any_alarm)
        key = analog.pack_key
        mos = _engine.mos.get(key, {})
        d["cfet"] = mos.get("cfet")
        d["dfet"] = mos.get("dfet")
        d["pack_key"] = key
        bus_addr = _engine._pack_map.get(key)
        d["bus"] = bus_addr[0].name if bus_addr else ""
        # Power Off only on the directly-wired main pack; expose flags to the UI.
        master_addr = _engine._bus_master.get(d["bus"])
        d["is_master"] = key in _engine._master_keys
        d["master_resolvable"] = master_addr is not None
        d["can_poweroff"] = (master_addr is None) or d["is_master"]
        info = _engine._product.get(key) or {}
        d["serial"] = info.get("serial", "")
        d["model"] = info.get("model", "")
        d["fw"] = info.get("version", "")
        d["warnings"] = status.warnings if status else []
        d["protections"] = status.protections if status else []
        with _state_lock:
            _state["packs"][d["source"]] = d

    while not stop.is_set():
        start = time.monotonic()
        readings = _engine.poll_cycle()
        _engine.dispatch(readings, on_reading=on_reading)
        with _state_lock:
            _state["updated"] = datetime.now().isoformat(timespec="seconds")
            _state["connected"] = bool(readings)
            _state["error"] = None if readings else "keine Antwort vom BMS"
        time.sleep(max(0.0, _engine.poll_interval - (time.monotonic() - start)))

    _engine.close()
    log.info("Background poller stopped")


# ---------------------------------------------------------------- history
CELL_KEYS = [f"v{n:02d}" for n in range(1, 17)]


def _query_history(source: str, since_iso: str, until_iso: str | None,
                   max_points: int) -> dict:
    path = _db_path()
    if not path or not os.path.exists(path):
        return {"time": []}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    cols = "time,voltage_v,current_a,soc," + ",".join(CELL_KEYS)
    q = f"SELECT {cols} FROM readings WHERE source=? AND time>=?"
    args = [source, since_iso]
    if until_iso:
        q += " AND time<=?"; args.append(until_iso)
    q += " ORDER BY time"
    rows = con.execute(q, args).fetchall()
    con.close()
    if not rows:
        return {"time": []}
    # downsample by bucket-averaging to at most max_points
    step = max(1, len(rows) // max_points)
    out = {"time": [], "voltage_v": [], "current_a": [], "soc": []}
    for k in CELL_KEYS:
        out[k] = []
    for i in range(0, len(rows), step):
        bucket = rows[i:i + step]
        mid = bucket[len(bucket) // 2]
        out["time"].append(mid[0])
        # NULL-safe averaging: a single NULL value (e.g. a row written around a
        # Power Off/reset) must not break the whole history request. Missing
        # values are ignored; an all-empty bucket yields None (a gap in the chart).
        def _avg(idx, ndigits=None):
            xs = [r[idx] for r in bucket if r[idx] is not None]
            if not xs:
                return None
            return round(sum(xs) / len(xs), ndigits) if ndigits else round(sum(xs) / len(xs))
        out["voltage_v"].append(_avg(1, 2))
        out["current_a"].append(_avg(2, 2))
        out["soc"].append(_avg(3))
        for j, k in enumerate(CELL_KEYS):
            out[k].append(_avg(4 + j))
    return out


# ---------------------------------------------------------------- config io
SECRET = "********"


def _masked_config() -> dict:
    import copy
    c = copy.deepcopy(_cfg)
    mq = c.get("output", {}).get("mqtt", {})
    if mq.get("password"):
        mq["password"] = SECRET
    return c


def _save_config(new: dict) -> None:
    import yaml, copy
    merged = copy.deepcopy(new)
    # keep existing password if the UI sent the mask
    mq = merged.get("output", {}).get("mqtt", {})
    if mq.get("password") == SECRET:
        mq["password"] = _cfg.get("output", {}).get("mqtt", {}).get("password")
    with open(_config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, sort_keys=False, allow_unicode=True)
    log.info("Konfiguration gespeichert: %s", _config_path)


# ---------------------------------------------------------------- flask app
def create_app(cfg: dict):
    try:
        from flask import Flask, jsonify, request, send_file
    except ImportError:
        raise SystemExit("Flask not installed. Run: pip install flask")

    here = os.path.dirname(os.path.abspath(__file__))
    web_dir = os.path.join(here, "web")
    app = Flask(__name__, static_folder=None)

    # ---- Zugriffsbeschränkung nach Quell-Netz (optionale App-Schicht) --------
    # Robuster ist weiterhin systemd (IPAddressAllow) bzw. Firewall/WAF; dies ist
    # eine bequeme, GUI-editierbare Zusatzschicht.
    def _in_nets(ip, nets):
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return False
        for n in nets:
            try:
                if a in ipaddress.ip_network(str(n).strip(), strict=False):
                    return True
            except ValueError:
                continue
        return False

    def _client_ip():
        """Real client IP. X-Forwarded-For is only trusted when the direct peer is
        a configured trusted proxy (anti-spoofing); then walk the chain from the
        right and take the first address that is not itself a trusted proxy."""
        from flask import request
        remote = request.remote_addr or ""
        tp = (_cfg.get("web", {}) or {}).get("trusted_proxies", []) or []
        if tp and _in_nets(remote, tp):
            chain = [p.strip() for p in
                     request.headers.get("X-Forwarded-For", "").split(",") if p.strip()]
            for ip in reversed(chain):
                if not _in_nets(ip, tp):
                    return ip
            return chain[0] if chain else remote
        return remote

    @app.before_request
    def _access_guard():
        from flask import request
        allow = (_cfg.get("web", {}) or {}).get("allow_networks", []) or []
        if not allow:
            return None                      # keine Einschränkung konfiguriert
        ip = _client_ip()
        if _in_nets(ip, ["127.0.0.1/32", "::1/128"]):
            return None                      # localhost immer erlaubt (kein Aussperren)
        if _in_nets(ip, allow):
            return None
        log.warning("Zugriff verweigert für %s (nicht in allow_networks)", ip)
        return ("Zugriff verweigert (IP nicht freigegeben)", 403)

    def _page(name):
        with open(os.path.join(web_dir, name), "r", encoding="utf-8") as f:
            return f.read().replace("{{VERSION}}", f"v{__version__}")

    @app.route("/")
    def index():
        """Serve the live monitor dashboard."""
        return _page("index.html")

    @app.route("/chart")
    def chart():
        """Serve the history/live chart page."""
        return _page("chart.html")

    @app.route("/settings")
    def settings():
        """Serve the configuration editor page."""
        return _page("settings.html")

    @app.route("/vendor/<path:fn>")
    def vendor(fn):
        """Serve bundled front-end vendor assets (e.g. uPlot)."""
        from flask import send_from_directory
        return send_from_directory(os.path.join(web_dir, "vendor"), fn)

    @app.route("/api/state")
    def state():
        """Live snapshot for the UI: all packs plus meta (version, scale, buses)."""
        buses = []
        if _engine:
            for b in _engine.buses:
                buses.append({"name": b.name, "paused": b.name in _engine.paused})
        with _state_lock:
            return jsonify({
                "packs": [_state["packs"][k] for k in sorted(_state["packs"])],
                "updated": _state["updated"], "connected": _state["connected"],
                "error": _state["error"], "version": __version__,
                "cell_alert_mv": int(_cfg.get("cell_alert_mv", 0)),
                "cell_scale_min_mv": int(_cfg.get("cell_scale_min_mv", 2500)),
                "cell_scale_max_mv": int(_cfg.get("cell_scale_max_mv", 3650)),
                "buses": buses,
            })

    @app.route("/api/pause", methods=["POST"])
    def pause():
        """Pause/resume a bus at runtime (frees the serial line without stopping)."""
        d = request.get_json(force=True)
        if _engine is None:
            return jsonify({"ok": False, "error": "engine not ready"}), 503
        return jsonify(_engine.pause_bus(d["bus"], bool(d["paused"])))

    @app.route("/api/db/download")
    def db_download():
        """Download the SQLite DB (WAL checkpointed first for a complete file)."""
        sq = (_cfg.get("output", {}) or {}).get("sqlite", {}) or {}
        path = sq.get("path", "data/bms.db")
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)
        if not os.path.exists(path):
            return jsonify({"ok": False, "error": "keine Datenbank gefunden"}), 404
        # WAL in die Hauptdatei schreiben, damit der Download vollständig ist
        try:
            import sqlite3
            c = sqlite3.connect(path); c.execute("PRAGMA wal_checkpoint(TRUNCATE)"); c.close()
        except Exception:  # noqa: BLE001
            pass
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return send_file(path, as_attachment=True, download_name=f"bms_{ts}.db")

    def _log_files():
        """Return (current_path, [all log files incl. rotated backups])."""
        lf = (_cfg.get("log_file", {}) or {})
        path = lf.get("path", "data/logs/vkbms.log")
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)
        import glob as _glob
        files = sorted(f for f in _glob.glob(path + "*") if os.path.isfile(f))
        return path, files

    @app.route("/api/log/clear", methods=["POST"])
    def log_clear():
        """Delete ALL log files (current + rotated daily backups)."""
        if not (_cfg.get("log_file", {}) or {}).get("enabled"):
            return jsonify({"ok": False, "error": "Logdatei ist nicht aktiv"}), 400
        path, files = _log_files()
        removed = 0
        for f in files:
            try:
                if os.path.abspath(f) == os.path.abspath(path):
                    open(f, "w").close()          # truncate the active file (keep handle valid)
                else:
                    os.remove(f)                  # delete rotated backups
                removed += 1
            except OSError as e:
                log.warning("log clear: %s (%s)", f, e)
        return jsonify({"ok": True, "removed": removed})

    @app.route("/api/log/download")
    def log_download():
        """Download logs: ?scope=current (active file) or ?scope=all (ZIP of all)."""
        if not (_cfg.get("log_file", {}) or {}).get("enabled"):
            return jsonify({"ok": False, "error": "Logdatei ist nicht aktiv"}), 400
        path, files = _log_files()
        scope = request.args.get("scope", "current")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if scope == "all":
            import io, zipfile
            if not files:
                return jsonify({"ok": False, "error": "keine Logdateien vorhanden"}), 404
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in files:
                    z.write(f, arcname=os.path.basename(f))
            buf.seek(0)
            return send_file(buf, as_attachment=True, download_name=f"vkbms_logs_{ts}.zip",
                             mimetype="application/zip")
        if not os.path.exists(path):
            return jsonify({"ok": False, "error": "keine Logdatei vorhanden"}), 404
        return send_file(path, as_attachment=True, download_name=f"vkbms_{ts}.log")

    @app.route("/api/history")
    def history():
        """Downsampled history for a pack: ?minutes= (live) or ?from=&to= (range)."""
        source = request.args.get("source", "")
        minutes = request.args.get("minutes", type=int)
        max_points = request.args.get("max_points", default=2000, type=int)
        if minutes:
            since = (datetime.now() - timedelta(minutes=minutes)).isoformat()
            until = None
        else:
            since = request.args.get("from", "")
            until = request.args.get("to") or None
        return jsonify(_query_history(source, since, until, max_points))

    @app.route("/api/mos", methods=["POST"])
    def mos():
        """Switch CFET/DFET for a pack (delegates to the engine, verifies readback)."""
        d = request.get_json(force=True)
        if _engine is None:
            return jsonify({"ok": False, "error": "engine not ready"}), 503
        res = _engine.set_mos(d["pack"], cfet=d.get("cfet"), dfet=d.get("dfet"), source="Web")
        return jsonify(res)

    @app.route("/api/poweroff", methods=["POST"])
    def poweroff():
        """Trigger a BMS Power Off (~5s reset) for a pack."""
        d = request.get_json(force=True)
        if _engine is None:
            return jsonify({"ok": False, "error": "engine not ready"}), 503
        return jsonify(_engine.power_off(d["pack"], source="Web"))

    @app.route("/api/config", methods=["GET", "POST"])
    def config_io():
        """GET the masked config, or POST a new config to persist to disk."""
        if request.method == "GET":
            return jsonify(_masked_config())
        try:
            _save_config(request.get_json(force=True))
            return jsonify({"ok": True})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/restart", methods=["POST"])
    def restart():
        """Exit the process so systemd (Restart=always) starts a fresh instance."""
        log.info("Neustart über GUI angefordert")
        threading.Timer(0.7, lambda: os._exit(0)).start()  # systemd Restart=always
        return jsonify({"ok": True})

    return app


def run(cfg: dict, config_path: str | None = None) -> None:
    global _config_path, _cfg
    _cfg = cfg
    _config_path = config_path or "config.yaml"
    from .poller import setup_logging
    setup_logging(cfg)
    web = cfg.get("web", {})
    host = web.get("host", "0.0.0.0")
    port = int(web.get("port", 8080))
    if not web.get("access_log", False):
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

    stop = threading.Event()
    t = threading.Thread(target=_background_loop, args=(cfg, stop), daemon=True)
    t.start()
    threading.Thread(target=_cleanup_loop, args=(stop,), daemon=True).start()

    app = create_app(cfg)
    log.info("Dashboard at http://localhost:%d  (or http://<dieser-PC>:%d im Netz)", port, port)
    try:
        app.run(host=host, port=port, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Beende…")
        stop.set()
        try:
            t.join(timeout=3)
        except KeyboardInterrupt:
            pass
        log.info("Gestoppt.")
