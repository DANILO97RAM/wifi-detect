"""Vendor lookup (offline OUI table) and device-type enrichment."""
import argparse
from functools import lru_cache
import logging
from pathlib import Path
import re
import tempfile
from urllib.error import URLError
from urllib.request import urlopen

from wifi_monitor.core.models import Device, normalize_mac

OUI_URL = "https://standards-oui.ieee.org/oui/oui.txt"
OUI_MIRROR_URL = "https://www.wireshark.org/download/automated/data/manuf"
DEFAULT_OUI_FILE = Path(__file__).resolve().parents[2] / "data" / "oui.txt"

# Small built-in OUI sample; extend or load a full IEEE oui.txt via load_oui_file().
OUI = {
    "00:1a:11": "Google", "f4:f5:d8": "Google", "3c:5a:b4": "Google",
    "ac:bc:32": "Apple", "f0:18:98": "Apple", "a4:83:e7": "Apple", "3c:22:fb": "Apple",
    "00:16:32": "Samsung", "8c:79:f5": "Samsung", "fc:a6:21": "Samsung",
    "b8:27:eb": "Raspberry Pi", "dc:a6:32": "Raspberry Pi", "e4:5f:01": "Raspberry Pi",
    "18:b4:30": "Nest", "50:c7:bf": "TP-Link", "f4:f2:6d": "TP-Link",
    "00:05:5d": "D-Link", "1c:7e:e5": "D-Link",
    "fc:65:de": "Amazon", "44:65:0d": "Amazon", "00:04:4b": "Nvidia",
    "00:0c:29": "VMware", "08:00:27": "VirtualBox",
    "d8:3a:dd": "Raspberry Pi", "00:1b:63": "Apple", "00:50:f2": "Microsoft",
    "00:e0:4c": "Realtek", "b4:2e:99": "Giga-Byte", "00:1e:a6": "Brother",
    "00:17:c8": "Kyocera", "3c:2a:f4": "Brother", "00:80:77": "Brother",
    "00:00:48": "Epson", "64:eb:8c": "Epson", "44:d2:44": "Seiko Epson",
    "00:0e:58": "Sonos", "5c:aa:fd": "Sonos", "b8:e9:37": "Sonos",
    "00:12:fb": "Samsung", "d0:03:4b": "Apple", "00:09:b0": "Onkyo",
}

TYPE_RULES = [
    (r"\b(ipad|tablet|kindle|galaxy\s+tab|surface\s+pro)\b", "Tablet"),
    (r"\b(chromecast|roku|fire\s*tv|apple\s*tv|shield\s*tv)\b", "Streaming device"),
    (r"\b(tv|televisor|television|smart\s*tv|bravia|webos|tizen)\b", "TV"),
    (r"\b(iphone|android|galaxy|pixel|redmi|oneplus|smartphone|celular|telefono)\b"
     r"|\b(samsung\s+[sazm]\s*\d{1,3}|moto\s+(g\d*|e\d*|edge)|poco\s+[fxm]\d+)\b", "Phone"),
    (r"\b(printer|impresora|laserjet|deskjet|officejet|epson|brother|pixma)\b", "Printer"),
    (r"\b(playstation|ps[345]|xbox|nintendo|steam\s*deck)\b", "Game console"),
    (r"\b(router|mesh|switch|access\s*point|punto\s+de\s+acceso|deco|repeater|repetidor)\b",
     "Network gear"),
    (r"\b(echo|alexa|sonos|homepod|nest\s+(audio|mini))\b", "Smart speaker"),
    (r"\b(camera|camara|cam|doorbell|tapo|wyze|reolink|ring\s+(camera|doorbell))\b", "Camera"),
    (r"\b(macbook|imac|laptop|desktop|pc|windows|msi|thinkpad|ideapad|pavilion|omen|"
     r"victus|elitebook|probook|computador|computadora|portatil)\b", "Computer"),
    (r"\b(plug|enchufe|bulb|bombilla|thermostat|termostato|shelly|sonoff)\b", "Smart home"),
]

VENDOR_TYPES = {
    "Apple": "Apple device", "Samsung": "Samsung device", "Google": "Google device",
    "Raspberry Pi": "Computer (SBC)", "Nest": "Smart home", "Amazon": "Smart speaker / Fire",
    "Sonos": "Smart speaker", "Brother": "Printer", "Epson": "Printer", "Seiko Epson": "Printer",
    "Kyocera": "Printer", "TP-Link": "Network gear", "D-Link": "Network gear",
    "VMware": "Virtual machine", "VirtualBox": "Virtual machine", "Microsoft": "Computer",
    "Realtek": "Network adapter", "Giga-Byte": "Computer", "Nvidia": "Streaming / Computer",
    "Intel": "Computer / network adapter", "MSI": "Computer",
    "HP": "Computer / Printer", "Dell": "Computer", "Lenovo": "Computer / Tablet",
    "ASUS": "Computer / Network gear", "Acer": "Computer", "LG": "LG device",
    "Sony": "Sony device", "Hisense": "Hisense device", "TCL": "TCL device",
    "Vizio": "TV / other device", "Roku": "TV / Streaming",
    "Xiaomi": "Xiaomi device", "Huawei": "Huawei device", "Motorola": "Motorola device",
    "OnePlus": "Phone / other device", "Oppo": "Phone / other device",
    "Vivo": "Phone / other device", "Realme": "Phone / other device",
    "Espressif": "Smart home / IoT", "Tuya": "Smart home / IoT",
    "Ubiquiti": "Network gear", "Netgear": "Network gear", "Linksys": "Network gear",
    "MikroTik": "Network gear", "Tenda": "Network gear", "SpaceX": "Network gear",
    "Canon": "Printer / Camera", "Hikvision": "Camera / Recorder", "Dahua": "Camera / Recorder",
}

VENDOR_RULES = [
    (r"\btp[\s-]*link\b", "TP-Link"), (r"\bd[\s-]*link\b", "D-Link"),
    (r"\bintel\b", "Intel"), (r"\bmicro[\s-]*star\b|\bmsi\b", "MSI"),
    (r"\bhewlett[\s-]*packard\b|\bhp\b", "HP"), (r"\bgiga[\s-]*byte\b", "Giga-Byte"),
    (r"\braspberry\s*pi\b", "Raspberry Pi"),
    (r"\bspace\s+exploration\b|\bspacex\b", "SpaceX"),
]


def parse_oui(text: str) -> dict[str, str]:
    entries = {
        normalize_mac(prefix): vendor.strip()
        for prefix, vendor in re.findall(
            r"^\s*([0-9A-Fa-f]{2}(?:-[0-9A-Fa-f]{2}){2})\s+\(hex\)\s+([^\r\n]+)",
            text, re.MULTILINE,
        )
    }
    # Wireshark "manuf" format: "AA:BB:CC<TAB>Short<TAB>Long name" (24-bit entries only)
    for prefix, short, long_name in re.findall(
        r"^([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){2})[ \t]+(\S+)(?:[ \t]+([^\r\n]+))?$",
        text, re.MULTILINE,
    ):
        entries.setdefault(normalize_mac(prefix), (long_name or short).strip())
    if not entries:
        raise ValueError("The OUI file contains no valid IEEE manufacturer entries")
    return entries


def load_oui_file(path: str | Path) -> None:
    """Load the IEEE registry locally without transmitting device addresses."""
    OUI.update(parse_oui(Path(path).read_text(encoding="utf-8-sig")))


@lru_cache(maxsize=1)
def _load_default_oui() -> None:
    if DEFAULT_OUI_FILE.exists():
        load_oui_file(DEFAULT_OUI_FILE)
    else:
        logging.getLogger("wifimon").warning(
            "Using the limited built-in vendor table. Download the IEEE registry with "
            "python -m wifi_monitor.services.enrichment --update-oui"
        )


def update_oui_file() -> int:
    text = ""
    for url in (OUI_URL, OUI_MIRROR_URL):
        try:
            with urlopen(url, timeout=60) as response:
                text = response.read().decode("utf-8-sig")
            break
        except OSError as error:
            logging.getLogger("wifimon").warning("Could not download %s: %s", url, error)
    if not text:
        raise URLError("No registry source is reachable")
    entries = parse_oui(text)
    DEFAULT_OUI_FILE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=DEFAULT_OUI_FILE.parent, delete=False,
    ) as output:
        temporary = Path(output.name)
        try:
            output.write(text)
            output.close()
            temporary.replace(DEFAULT_OUI_FILE)
        finally:
            temporary.unlink(missing_ok=True)
    OUI.update(entries)
    return len(entries)


def is_randomized(mac: str) -> bool:
    """A locally administered address may be randomized or configured manually."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0b10)
    except ValueError:
        return False


def lookup_vendor(mac: str) -> str:
    mac = normalize_mac(mac)
    _load_default_oui()
    if is_randomized(mac):
        return "Private (locally administered MAC)"
    return OUI.get(mac[:8], "Unknown")


def classify(vendor: str, hostname: str, mac: str = "", name: str = "") -> str:
    for label in (name, hostname):
        text = re.sub(r"[_-]+", " ", (label or "").casefold())
        for pattern, dev_type in TYPE_RULES:
            if re.search(pattern, text):
                return dev_type
    if is_randomized(normalize_mac(mac)) or vendor.startswith("Private"):
        return "Unknown"
    for pattern, canonical in VENDOR_RULES:
        if re.search(pattern, vendor, re.IGNORECASE):
            return VENDOR_TYPES[canonical]
    for canonical, dev_type in VENDOR_TYPES.items():
        if re.search(r"\b" + re.escape(canonical) + r"\b", vendor, re.IGNORECASE):
            return dev_type
    return "Unknown"


def enrich(device: Device) -> Device:
    device.vendor = lookup_vendor(device.mac)
    device.type = classify(device.vendor, device.hostname, device.mac, device.name)
    return device


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download the IEEE vendor registry for offline lookup")
    parser.add_argument("--update-oui", action="store_true", required=True)
    parser.parse_args()
    try:
        count = update_oui_file()
    except (OSError, URLError, ValueError) as error:
        parser.exit(1, f"Registry update failed: {error}\nThe existing registry was not replaced.\n")
    print(f"Downloaded {count} manufacturer prefixes to {DEFAULT_OUI_FILE}")
    print("Restart the app to load the updated registry.")
