# Wi-Fi Monitor

Free, self-hosted dashboard listing devices on your Wi-Fi: MAC, IP, vendor, device type (best effort), custom name, status and connected count. Alerts on new devices.

## Run
```
cp config.example.yaml config.yaml    # first setup only; keep existing config
pip install -r requirements.txt
python wifimon_main.py config.yaml     # then open http://127.0.0.1:8000
python -m pytest -q tests
```
Run it on a machine connected to the network you want to monitor. Edit [config.yaml](config.yaml) to choose networks (`arp`, `tplink`, `dlink`, `starlink`).

## Ubuntu in WSL / Starlink

The example config uses provider `windows`, subnet `192.168.1.0/24` and Windows
Wi-Fi interface index `17`. WSL NAT cannot read the physical Wi-Fi ARP table.
This provider invokes `powershell.exe` through WSL interoperability and uses
Windows `SendARP` requests on the configured interface, not stale cached entries.
It includes the Windows PC and router; multiple IPs with the same MAC count once.
It does not read the Starlink router's official Wi-Fi client list or distinguish
wired clients. Sleeping or isolated devices may not respond, so the count means
devices detected on the LAN, not a guaranteed count of all Wi-Fi associations.

If you switch networks, update the subnet and interface index. In Windows
PowerShell, `Get-NetIPConfiguration` shows the interface indexes. The provider
rejects a subnet that does not match that interface and supports /22 through /30.
An error preserves the last scan; its count is not a fresh observation.

In Ubuntu/WSL:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python wifimon_main.py config.yaml
```

Open http://localhost:8000 in your Windows browser. Allow the first scan to finish.
No router password is required. Stop the app with Ctrl+C.

## Local data and Git

Git excludes `config.yaml`, `wifi-monitor.db`, the virtual environment and Python
caches. Your saved device names remain in the local database; they are not part
of a commit. Use `config.example.yaml` as the template for a new installation.
Keep a private backup of your database and config before moving installations.
The root launcher loads the config beside itself by default, even when launched
from another working directory. Relative database paths resolve beside the
selected config, so the existing root database and saved names are reused.
Do not replace your existing config or change network IDs when restarting:
custom names are associated with the network ID and MAC address.

## Architecture

```text
wifimon_main.py             # launcher kept at the root
config.example.yaml         # versioned configuration template
config.yaml                 # private configuration, unchanged
wifi-monitor.db             # private saved devices and names, unchanged
wifi_monitor/
  core/                     # config.py, models.py
  providers/                # discovery.py, windows.py
  services/                 # scanner.py, enrichment.py
  storage/                  # repository.py
  api/                      # routes.py
web/
  index.html
tests/
  test_wifimon.py
```

After an update, stop the old app with Ctrl+C and restart using the same launcher:

```bash
source .venv/bin/activate
python wifimon_main.py
python -m pytest -q tests
```

## Notes
- Device type is a guess from vendor/hostname; phones with private (randomized) MACs show as such.
- TP-Link/D-Link read the router client page with Basic auth; paths vary by firmware (set `options.client_list_urls`). They fall back to the ARP scan.
- Starlink's router has no open client list, so it uses the ARP scan.
- The first scan of a network is a baseline and raises no alerts.
- Extend the vendor table with `wifi_monitor.services.enrichment.load_oui_file()` using the IEEE `oui.txt`.
