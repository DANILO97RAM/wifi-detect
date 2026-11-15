"""Provider layer: each provider returns the devices currently on one network.

Add a new router by subclassing DeviceProvider and decorating with @register.
"""
import base64
import ipaddress
import re
import socket
import subprocess
import urllib.request
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor

from wifi_monitor.core.models import Device, normalize_mac
from wifi_monitor.providers.windows import scan_windows

MAC_RE = re.compile(r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

PROVIDERS = {}


def register(name):
    def deco(cls):
        PROVIDERS[name] = cls
        return cls
    return deco


def create_provider(network):
    if network.provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{network.provider}'. Available: {sorted(PROVIDERS)}")
    return PROVIDERS[network.provider](network)


class DeviceProvider(ABC):
    def __init__(self, network):
        self.network = network
        self.options = network.options

    @abstractmethod
    def scan(self) -> list:
        """Return a list[Device] currently connected."""


def _hostname(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ""


@register("arp")
class ArpProvider(DeviceProvider):
    """Pings the subnet to populate the kernel neighbour table, then reads it.

    Needs no root and no router login. Works on any network this host is on.
    """

    def _ping(self, ip):
        subprocess.run(["ping", "-c", "1", "-W", "1", str(ip)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _neighbours(self):
        out = subprocess.run(["ip", "neigh"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            ip_m, mac_m = IP_RE.search(line), MAC_RE.search(line)
            if ip_m and mac_m and not line.rstrip().endswith(("FAILED", "INCOMPLETE")):
                yield ip_m.group(0), normalize_mac(mac_m.group(0))

    def scan(self):
        net = ipaddress.ip_network(self.network.subnet, strict=False)
        hosts = list(net.hosts())[:1024]
        with ThreadPoolExecutor(max_workers=64) as pool:
            list(pool.map(self._ping, hosts))
        found = {}
        for ip, mac in self._neighbours():
            if ipaddress.ip_address(ip) in net:
                found[mac] = Device(mac=mac, ip=ip, hostname=_hostname(ip))
        return list(found.values())


class RouterHttpProvider(DeviceProvider):
    """Fetches a router page with HTTP Basic auth and extracts MAC/IP pairs.

    Client-list URLs differ per model/firmware, so they are configurable via
    options.client_list_urls. If none works, falls back to the ARP scan.
    """

    default_paths = ["/"]

    def _fetch(self, path):
        host = self.options["host"]
        req = urllib.request.Request(f"http://{host}{path}")
        user, pw = self.options.get("username", ""), self.options.get("password", "")
        token = base64.b64encode(f"{user}:{pw}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
        with urllib.request.urlopen(req, timeout=int(self.options.get("timeout", 5))) as r:
            return r.read().decode("utf-8", errors="ignore")

    def parse(self, text):
        devices = {}
        for line in re.split(r"[\n\r]|</tr>|},", text):
            mac_m, ip_m = MAC_RE.search(line), IP_RE.search(line)
            if mac_m:
                mac = normalize_mac(mac_m.group(0))
                ip = ip_m.group(0) if ip_m else ""
                devices[mac] = Device(mac=mac, ip=ip, hostname=_hostname(ip) if ip else "")
        return list(devices.values())

    def scan(self):
        for path in self.options.get("client_list_urls", self.default_paths):
            try:
                devices = self.parse(self._fetch(path))
            except OSError:
                continue
            if devices:
                return devices
        return ArpProvider(self.network).scan()


@register("tplink")
class TpLinkProvider(RouterHttpProvider):
    default_paths = ["/userRpm/AssignedIpAddrListRpm.htm", "/userRpm/WlanStationRpm.htm"]


@register("dlink")
class DLinkProvider(RouterHttpProvider):
    default_paths = ["/wireless_clients.asp", "/dhcp_clients.asp", "/DHCPTable.htm"]


@register("starlink")
class StarlinkProvider(DeviceProvider):
    """The Starlink router exposes no open client-list API, so use the ARP scan."""

    def scan(self):
        return ArpProvider(self.network).scan()


@register("windows")
class WindowsProvider(DeviceProvider):
    """Use the Windows Wi-Fi adapter when Ubuntu runs behind WSL NAT."""

    def scan(self):
        return scan_windows(self.network)
