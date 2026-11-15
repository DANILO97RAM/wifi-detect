"""Entry point: python wifimon_main.py [config.yaml]"""
import logging
import sys
from pathlib import Path

import uvicorn

from wifi_monitor.api.routes import create_app
from wifi_monitor.core.config import load_config
from wifi_monitor.services.scanner import Scanner
from wifi_monitor.storage.repository import Storage

PROJECT_ROOT = Path(__file__).resolve().parent


def main():
    logging.basicConfig(level=logging.INFO)
    config = load_config(sys.argv[1] if len(sys.argv) > 1 else PROJECT_ROOT / "config.yaml")
    storage = Storage(config.db_path)
    scanner = Scanner(config, storage)
    scanner.start()
    uvicorn.run(create_app(config, storage, scanner), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
