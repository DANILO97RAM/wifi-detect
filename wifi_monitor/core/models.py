"""Domain models shared by all application layers."""
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Device:
    mac: str
    ip: str = ""
    hostname: str = ""
    vendor: str = "Unknown"
    type: str = "Unknown"
    name: str = ""
    online: bool = True
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_mac(mac: str) -> str:
    return mac.strip().lower().replace("-", ":")
