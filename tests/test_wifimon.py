import ipaddress
import io
import json
import subprocess
from types import SimpleNamespace

import pytest

from wifi_monitor.services import enrichment
from wifi_monitor.services.enrichment import classify, enrich, lookup_vendor
from wifi_monitor.core.models import Device
from wifi_monitor.providers.discovery import RouterHttpProvider
from wifi_monitor.core.config import NetworkConfig, load_config
from wifi_monitor.storage.repository import Storage
from wifi_monitor.providers.windows import parse_neighbours, scan_windows
from wifi_monitor.services.scanner import Scanner


def test_vendor_and_randomized():
    assert lookup_vendor("b8:27:eb:00:00:01").startswith("Raspberry Pi")
    assert lookup_vendor("da:a1:19:00:00:01").startswith("Private")


def test_classify_by_hostname_then_vendor():
    assert classify("Unknown", "Daniels-iPhone") == "Phone"
    assert classify("Brother", "") == "Printer"


def test_router_parse_extracts_mac_ip():
    p = RouterHttpProvider(NetworkConfig("x", "x", "tplink", options={"host": "1.1.1.1"}))
    html = "<tr><td>192.168.0.5</td><td>AA-BB-CC-DD-EE-FF</td></tr>"
    devs = p.parse(html)
    assert devs[0].mac == "aa:bb:cc:dd:ee:ff" and devs[0].ip == "192.168.0.5"


def test_storage_new_devices_and_offline():
    s = Storage(":memory:")
    assert [d.mac for d in s.save_scan("n", [Device(mac="aa:aa:aa:aa:aa:aa")])] == ["aa:aa:aa:aa:aa:aa"]
    assert s.save_scan("n", [Device(mac="bb:bb:bb:bb:bb:bb")])
    devs = {d.mac: d for d in s.list_devices("n")}
    assert devs["aa:aa:aa:aa:aa:aa"].online is False and devs["bb:bb:bb:bb:bb:bb"].online
    assert s.rename("n", "bb:bb:bb:bb:bb:bb", "TV")


def test_windows_neighbours_only_count_live_unique_unicast_devices():
    rows = [
        ("192.168.1.1", "74-24-9F-D4-84-40", "Reachable"),
        ("192.168.1.230", "E0-1C-FC-FA-4E-6E", "Reachable"),
        ("192.168.1.231", "E0-1C-FC-FA-4E-6E", "Reachable"),
        ("192.168.1.9", "00-00-00-00-00-00", "Unreachable"),
        ("192.168.1.10", "E0-1C-FC-FA-4E-00", "Stale"),
        ("192.168.1.255", "FF-FF-FF-FF-FF-FF", "Permanent"),
        ("192.168.1.11", "00-00-00-00-00-00", "Reachable"),
        ("192.168.1.12", "01-00-5E-00-00-01", "Reachable"),
        ("172.28.224.1", "74-24-9F-D4-84-41", "Reachable"),
    ]
    payload = json.dumps([
        dict(IPAddress=ip, LinkLayerAddress=mac, State=state)
        for ip, mac, state in rows
    ])
    devices = parse_neighbours(payload, ipaddress.IPv4Network("192.168.1.0/24"))
    assert {d.mac for d in devices} == {"74:24:9f:d4:84:40", "e0:1c:fc:fa:4e:6e"}


def test_windows_scan_propagates_powershell_failure(monkeypatch):
    def run(args, **kwargs):
        assert args[0] == "powershell.exe"
        assert "$index = 17" in args[-1]
        return subprocess.CompletedProcess(args, 1, "", "Adapter disconnected")
    monkeypatch.setattr("wifi_monitor.providers.windows.subprocess.run", run)
    network = NetworkConfig("n", "n", "windows", "192.168.1.0/24",
                            {"interface_index": 17})
    with pytest.raises(RuntimeError, match="Adapter disconnected"):
        scan_windows(network)


@pytest.mark.parametrize("subnet,index", [
    ("192.168.0.0/16", 17), ("::/64", 17), ("192.168.1.0/24", "17"),
])
def test_windows_scan_rejects_invalid_configuration(subnet, index):
    with pytest.raises(ValueError):
        scan_windows(NetworkConfig("n", "n", "windows", subnet,
                                   {"interface_index": index}))


def test_names_survive_database_reopen_and_rescan(tmp_path):
    path = str(tmp_path / "devices.db")
    storage = Storage(path)
    mac = "aa:bb:cc:dd:ee:ff"
    storage.save_scan("starlink", [Device(mac=mac, ip="192.168.1.5")])
    assert storage.rename("starlink", mac, "Saved router")
    storage.conn.close()

    reopened = Storage(path)
    try:
        assert reopened.list_devices("starlink")[0].name == "Saved router"
        reopened.save_scan("starlink", [Device(mac=mac, ip="192.168.1.9")])
        device = reopened.list_devices("starlink")[0]
        assert device.name == "Saved router"
        assert device.ip == "192.168.1.9"
        reopened.save_scan("starlink", [])
        assert reopened.list_devices("starlink")[0].name == "Saved router"
    finally:
        reopened.conn.close()


def test_database_path_is_relative_to_config_not_working_directory(tmp_path, monkeypatch):
    config_dir = tmp_path / "installation"
    config_dir.mkdir()
    config = config_dir / "config.yaml"
    config.write_text("db_path: wifi-monitor.db\nnetworks: []\n")
    monkeypatch.chdir(tmp_path)
    assert load_config(config).db_path == str(config_dir / "wifi-monitor.db")


def test_launcher_uses_existing_config_from_another_directory(tmp_path, monkeypatch):
    import FLDSMDFR as launcher

    installation = tmp_path / "installation"
    installation.mkdir()
    (installation / "config.yaml").write_text("networks: []\n")
    captured = {}
    monkeypatch.setattr(launcher, "PROJECT_ROOT", installation)
    monkeypatch.setattr(launcher.sys, "argv", ["FLDSMDFR.py"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launcher.Scanner, "start", lambda self: None)
    monkeypatch.setattr(launcher, "create_app", lambda config, storage, scanner: storage)

    def run(storage, **kwargs):
        captured["db_path"] = storage.conn.execute("PRAGMA database_list").fetchone()["file"]
        storage.conn.close()

    monkeypatch.setattr(launcher.uvicorn, "run", run)
    launcher.main()
    assert captured["db_path"] == str(installation / "wifi-monitor.db")


@pytest.mark.parametrize("vendor,label,expected", [
    ("Samsung Electronics Co.,Ltd", "Samsung S24", "Phone"),
    ("Private (locally administered MAC)", "Samsung A36", "Phone"),
    ("Samsung Electronics Co.,Ltd", "Samsung TV sala", "TV"),
    ("Samsung Electronics Co.,Ltd", "Galaxy-Tab", "Tablet"),
    ("LG Electronics", "LG_webOS", "TV"),
    ("Sony Corporation", "BRAVIA living room", "TV"),
    ("Amazon Technologies Inc.", "Fire-TV", "Streaming device"),
    ("Google, Inc.", "Chromecast TV", "Streaming device"),
    ("Intel Corporate", "PC HP", "Computer"),
    ("Unknown", "PC MSI", "Computer"),
    ("Unknown", "Leidy Router", "Network gear"),
    ("Unknown", "Nintendo Switch", "Game console"),
    ("Unknown", "Ethernet switch", "Network gear"),
    ("Unknown", "HP LaserJet", "Printer"),
    ("Unknown", "Moto G84", "Phone"),
    ("Unknown", "nest-mini", "Smart speaker"),
    ("Unknown", "Cameron", "Unknown"),
    ("Unknown", "Darwin", "Unknown"),
])
def test_specific_device_identifiers_and_word_boundaries(vendor, label, expected):
    assert classify(vendor, label) == expected


@pytest.mark.parametrize("vendor,expected", [
    ("Samsung Electronics Co.,Ltd", "Samsung device"),
    ("Intel Corporate", "Computer / network adapter"),
    ("Micro-Star INT'L CO., LTD.", "Computer"),
    ("D-Link International", "Network gear"),
    ("TP-Link Systems Inc.", "Network gear"),
    ("Space Exploration Technologies", "Network gear"),
])
def test_registry_vendor_names_do_not_imply_exact_models(vendor, expected):
    assert classify(vendor, "") == expected


def test_saved_name_overrides_hostname_and_private_mac_does_not_imply_phone():
    mac = "da:a1:19:00:00:01"
    assert classify("Private", "Galaxy-S24", mac, "TV sala") == "TV"
    assert classify("Private", "", mac) == "Unknown"


def test_load_ieee_registry_and_private_mac_precedence(tmp_path, monkeypatch):
    registry = tmp_path / "oui.txt"
    registry.write_text(
        "00-11-22   (hex)   Samsung Electronics Co.,Ltd\n"
        "02-11-22   (hex)   Example Corporation\n"
        "INVALID   (hex)   Not a manufacturer\n",
    )
    monkeypatch.setattr(enrichment, "DEFAULT_OUI_FILE", registry)
    monkeypatch.setattr(enrichment, "OUI", {})
    enrichment._load_default_oui.cache_clear()
    try:
        assert lookup_vendor("00-11-22-AA-BB-CC") == "Samsung Electronics Co.,Ltd"
        assert lookup_vendor("02:11:22:aa:bb:cc").startswith("Private")
        assert classify(lookup_vendor("00:11:22:aa:bb:cc"), "Samsung TV") == "TV"
    finally:
        enrichment._load_default_oui.cache_clear()


def test_failed_registry_update_preserves_existing_file(tmp_path, monkeypatch):
    registry = tmp_path / "oui.txt"
    registry.write_text("existing registry")
    monkeypatch.setattr(enrichment, "DEFAULT_OUI_FILE", registry)
    monkeypatch.setattr(enrichment, "urlopen", lambda url, timeout: io.BytesIO(b"<html>Error</html>"))
    with pytest.raises(ValueError, match="no valid IEEE"):
        enrichment.update_oui_file()
    assert registry.read_text() == "existing registry"


def test_registry_update_uses_only_public_url_and_loads_offline(tmp_path, monkeypatch):
    registry = tmp_path / "data" / "oui.txt"
    payload = b"00-11-22   (hex)   Example Corporation\n"
    calls = []

    def download(url, timeout):
        calls.append(url)
        return io.BytesIO(payload)

    monkeypatch.setattr(enrichment, "DEFAULT_OUI_FILE", registry)
    monkeypatch.setattr(enrichment, "OUI", {})
    monkeypatch.setattr(enrichment, "urlopen", download)
    enrichment._load_default_oui.cache_clear()
    try:
        assert enrichment.update_oui_file() == 1
        assert registry.read_bytes() == payload
        assert lookup_vendor("00:11:22:aa:bb:cc") == "Example Corporation"
        assert calls == [enrichment.OUI_URL]
        assert list(registry.parent.iterdir()) == [registry]
    finally:
        enrichment._load_default_oui.cache_clear()


def test_scan_preserves_saved_names_and_estimates_type_from_them(monkeypatch):
    storage = Storage(":memory:")
    mac = "da:a1:19:00:00:01"
    network = NetworkConfig("starlink", "Starlink", "windows")
    config = SimpleNamespace(networks=[network], poll_interval=30)
    provider = SimpleNamespace(scan=lambda: [Device(mac=mac)])
    monkeypatch.setattr("wifi_monitor.services.scanner.create_provider", lambda network: provider)
    scanner = Scanner(config, storage)
    try:
        storage.save_scan("starlink", [Device(mac=mac)])
        storage.rename("starlink", mac, "Samsung A36")
        scanner.scan_network(network)
        device = storage.list_devices("starlink")[0]
        assert (device.name, device.type) == ("Samsung A36", "Phone")
        storage.rename("starlink", mac, "Samsung TV sala")
        scanner.scan_network(network)
        device = storage.list_devices("starlink")[0]
        assert (device.name, device.type) == ("Samsung TV sala", "TV")
        storage.rename("starlink", mac, "")
        assert enrich(storage.list_devices("starlink")[0]).type == "Unknown"
        assert not scanner.errors
    finally:
        storage.conn.close()


def test_parse_oui_accepts_wireshark_manuf_format():
    entries = enrichment.parse_oui(
        "# comment\n00:00:01         \tXerox       \tXerox Corporation\n"
        "00:55:DA:00/28   \tShinko\tShinko Technos\n"
    )
    assert entries == {"00:00:01": "Xerox Corporation"}
