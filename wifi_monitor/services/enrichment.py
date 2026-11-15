"""Vendor lookup (offline OUI table) and device-type enrichment."""

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
    (("iphone", "android", "galaxy", "pixel", "redmi", "oneplus"), "Phone"),
    (("ipad", "tablet", "kindle"), "Tablet"),
    (("macbook", "laptop", "desktop", "-pc", "win"), "Computer"),
    (("tv", "roku", "chromecast", "firetv", "bravia"), "TV / Streaming"),
    (("printer", "epson", "brother", "hp-", "canon"), "Printer"),
    (("echo", "alexa", "nest", "sonos", "homepod"), "Smart speaker"),
    (("cam", "ring", "doorbell"), "Camera"),
    (("playstation", "xbox", "switch", "nintendo"), "Game console"),
]

VENDOR_TYPES = {
    "Apple": "Phone / Apple device", "Samsung": "Phone / TV", "Google": "Google device",
    "Raspberry Pi": "Computer (SBC)", "Nest": "Smart home", "Amazon": "Smart speaker / Fire",
    "Sonos": "Smart speaker", "Brother": "Printer", "Epson": "Printer", "Seiko Epson": "Printer",
    "Kyocera": "Printer", "TP-Link": "Network gear", "D-Link": "Network gear",
    "VMware": "Virtual machine", "VirtualBox": "Virtual machine", "Microsoft": "Computer",
    "Realtek": "Computer", "Giga-Byte": "Computer", "Nvidia": "Streaming / Computer",
}


def load_oui_file(path: str) -> None:
    """Load lines like '00-1A-11   (hex)   Google' from the IEEE oui.txt."""
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if "(hex)" in line:
                prefix, _, vendor = line.partition("(hex)")
                OUI[prefix.strip().lower().replace("-", ":")] = vendor.strip()


def is_randomized(mac: str) -> bool:
    """Locally administered bit set => private/random MAC (modern phones)."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0b10)
    except ValueError:
        return False


def lookup_vendor(mac: str) -> str:
    vendor = OUI.get(mac.lower()[:8])
    if vendor:
        return vendor
    return "Private (randomized MAC)" if is_randomized(mac) else "Unknown"


def classify(vendor: str, hostname: str, mac: str = "") -> str:
    host = (hostname or "").lower()
    for keywords, dev_type in TYPE_RULES:
        if any(k in host for k in keywords):
            return dev_type
    if vendor in VENDOR_TYPES:
        return VENDOR_TYPES[vendor]
    if vendor.startswith("Private"):
        return "Phone / Tablet (private MAC)"
    return "Unknown"


def enrich(device):
    device.vendor = lookup_vendor(device.mac)
    device.type = classify(device.vendor, device.hostname, device.mac)
    return device
