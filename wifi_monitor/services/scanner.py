"""Service layer: periodic scanning, enrichment and new-device alerts."""
import logging
import threading

from wifi_monitor.services.enrichment import enrich
from wifi_monitor.providers.discovery import create_provider

log = logging.getLogger("wifimon")


class Scanner:
    def __init__(self, config, storage, notifiers=None):
        self.config = config
        self.storage = storage
        self.notifiers = notifiers or [lambda msg: log.warning(msg)]
        self.stop = threading.Event()
        self.first_scan_done = set()
        self.errors = {}

    def scan_network(self, network):
        try:
            devices = [enrich(d) for d in create_provider(network).scan()]
        except Exception as exc:  # keep the loop alive on provider failures
            self.errors[network.id] = str(exc)
            log.error("scan failed for %s: %s", network.id, exc)
            return
        self.errors.pop(network.id, None)
        first = network.id not in self.first_scan_done and not self.storage.known_macs(network.id)
        new = self.storage.save_scan(network.id, devices)
        self.first_scan_done.add(network.id)
        if first:  # baseline scan: don't alert for every existing device
            return
        for d in new:
            msg = f"New device on {network.name}: {d.mac} ({d.vendor}, {d.ip})"
            self.storage.add_alert(network.id, d.mac, msg)
            for notify in self.notifiers:
                notify(msg)

    def scan_all(self):
        for network in self.config.networks:
            self.scan_network(network)

    def _loop(self):
        while not self.stop.is_set():
            self.scan_all()
            self.stop.wait(self.config.poll_interval)

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
