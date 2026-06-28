"""
Local web server: runs the poller in a background thread, keeps the latest
reading per pack in memory, writes to the configured sinks, and serves a live
dashboard plus a small JSON API.

Run with: python run_web.py [config.yaml]
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime

from .sinks import reading_to_dict
from . import __version__

log = logging.getLogger("vkbms.server")

# shared state updated by the background thread, read by the API
_state_lock = threading.Lock()
_state: dict = {"packs": {}, "updated": None, "connected": False, "error": None}


def _background_loop(cfg: dict, stop: threading.Event) -> None:
    from .poller import Engine
    engine = Engine(cfg)
    log.info("Background poller: %d bus(es) | poll %.1fs · live %.1fs · mqtt %.1fs · db %.1fs",
             len(engine.buses), engine.poll_interval, engine.live_interval,
             engine.mqtt_interval, engine.db_interval)

    def on_reading(analog, status):
        d = reading_to_dict(analog)
        d["alarm"] = bool(status and status.any_alarm)
        with _state_lock:
            _state["packs"][d["source"]] = d

    while not stop.is_set():
        start = time.monotonic()
        readings = engine.poll_cycle()
        engine.dispatch(readings, on_reading=on_reading)
        with _state_lock:
            _state["updated"] = datetime.now().isoformat(timespec="seconds")
            _state["connected"] = bool(readings)
            _state["error"] = None if readings else "keine Antwort vom BMS"
        time.sleep(max(0.0, engine.poll_interval - (time.monotonic() - start)))

    engine.close()
    log.info("Background poller stopped")


def create_app(cfg: dict):
    try:
        from flask import Flask, jsonify, send_from_directory
    except ImportError:
        raise SystemExit("Flask not installed. Run: pip install flask")

    here = os.path.dirname(os.path.abspath(__file__))
    web_dir = os.path.join(here, "web")
    app = Flask(__name__, static_folder=None)

    @app.route("/")
    def index():
        with open(os.path.join(web_dir, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        return html.replace("{{VERSION}}", f"v{__version__}")

    @app.route("/api/state")
    def state():
        with _state_lock:
            return jsonify({
                "packs": [_state["packs"][k] for k in sorted(_state["packs"])],
                "updated": _state["updated"],
                "connected": _state["connected"],
                "error": _state["error"],
                "version": __version__,
            })

    return app


def run(cfg: dict) -> None:
    logging.basicConfig(
        level=getattr(logging, cfg.get("log_level", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    web = cfg.get("web", {})
    host = web.get("host", "0.0.0.0")
    port = int(web.get("port", 8080))

    # HTTP access logs (GET /api/state ... 200) off by default; toggle in config
    if not web.get("access_log", False):
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

    stop = threading.Event()
    t = threading.Thread(target=_background_loop, args=(cfg, stop), daemon=True)
    t.start()

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
