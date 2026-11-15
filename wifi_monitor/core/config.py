"""Config layer: loads config.yaml describing the monitored networks."""
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class NetworkConfig:
    id: str
    name: str
    provider: str
    subnet: str = ""
    options: dict = field(default_factory=dict)


@dataclass
class AppConfig:
    poll_interval: int
    db_path: str
    host: str
    port: int
    networks: list


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    raw = yaml.safe_load(config_path.read_text()) or {}
    db_path = Path(raw.get("db_path", "wifi-monitor.db")).expanduser()
    if not db_path.is_absolute():
        db_path = config_path.parent / db_path
    networks = [
        NetworkConfig(
            id=n["id"],
            name=n.get("name", n["id"]),
            provider=n["provider"],
            subnet=n.get("subnet", ""),
            options=n.get("options") or {},
        )
        for n in raw.get("networks", [])
    ]
    return AppConfig(
        poll_interval=int(raw.get("poll_interval", 30)),
        db_path=str(db_path),
        host=raw.get("host", "127.0.0.1"),
        port=int(raw.get("port", 8000)),
        networks=networks,
    )
