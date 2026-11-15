import ipaddress
import json
import subprocess

import pytest

from wifi_monitor.services.enrichment import classify, lookup_vendor
from wifi_monitor.core.models import Device
from wifi_monitor.providers.discovery import RouterHttpProvider
from wifi_monitor.core.config import NetworkConfig, load_config
from wifi_monitor.storage.repository import Storage
from wifi_monitor.providers.windows import parse_neighbours, scan_windows


def test_vendor_and_randomized():
    assert lookup_vendor("b8:27:eb:00:00:01") == "Raspberry Pi"
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
    import wifimon_main

    installation = tmp_path / "installation"
    installation.mkdir()
    (installation / "config.yaml").write_text("networks: []\n")
    captured = {}
    monkeypatch.setattr(wifimon_main, "PROJECT_ROOT", installation)
    monkeypatch.setattr(wifimon_main.sys, "argv", ["wifimon_main.py"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wifimon_main.Scanner, "start", lambda self: None)
    monkeypatch.setattr(wifimon_main, "create_app", lambda config, storage, scanner: storage)

    def run(storage, **kwargs):
        captured["db_path"] = storage.conn.execute("PRAGMA database_list").fetchone()["file"]
        storage.conn.close()

    monkeypatch.setattr(wifimon_main.uvicorn, "run", run)
    wifimon_main.main()
    assert captured["db_path"] == str(installation / "wifi-monitor.db")
