"""API layer: FastAPI app serving JSON endpoints and the web UI."""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from wifi_monitor.core.models import normalize_mac
from wifi_monitor.services.enrichment import enrich

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


class RenameBody(BaseModel):
    name: str


def create_app(config, storage, scanner) -> FastAPI:
    app = FastAPI(title="Wi-Fi Monitor")
    networks = {n.id: n for n in config.networks}

    def get_network(network_id):
        if network_id not in networks:
            raise HTTPException(404, "Unknown network")
        return networks[network_id]

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/networks")
    def list_networks():
        return [{"id": n.id, "name": n.name, "provider": n.provider, "subnet": n.subnet}
                for n in config.networks]

    @app.get("/api/networks/{network_id}/devices")
    def devices(network_id: str):
        get_network(network_id)
        return [enrich(d).to_dict() for d in storage.list_devices(network_id)]

    @app.get("/api/networks/{network_id}/summary")
    def summary(network_id: str):
        get_network(network_id)
        devs = storage.list_devices(network_id)
        return {"online": sum(d.online for d in devs), "total": len(devs),
                "error": scanner.errors.get(network_id)}

    @app.post("/api/networks/{network_id}/scan")
    def scan_now(network_id: str):
        scanner.scan_network(get_network(network_id))
        return summary(network_id)

    @app.patch("/api/networks/{network_id}/devices/{mac}")
    def rename(network_id: str, mac: str, body: RenameBody):
        get_network(network_id)
        if not storage.rename(network_id, normalize_mac(mac), body.name.strip()):
            raise HTTPException(404, "Unknown device")
        return {"ok": True}

    @app.get("/api/alerts")
    def alerts():
        return storage.list_alerts()

    @app.post("/api/alerts/ack")
    def ack():
        storage.ack_alerts()
        return {"ok": True}

    return app
