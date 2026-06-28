#!/usr/bin/env python3
"""
Headless logger entry point.

Usage:
    python run_logger.py [config.yaml]

Reads YAML config (default: config.yaml), then polls all configured packs and
writes to the configured sinks (SQLite / CSV / MQTT) until Ctrl-C.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_config(path: str) -> dict:
    try:
        import yaml
    except ImportError:
        sys.exit("PyYAML not installed. Run: pip install pyyaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    if not os.path.exists(path):
        sys.exit(f"Config not found: {path}\n"
                 f"Copy config.example.yaml to {path} and edit it first.")
    from vkbms.poller import run
    run(load_config(path))


if __name__ == "__main__":
    main()
