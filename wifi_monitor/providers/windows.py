"""Discover Windows LAN neighbours from WSL using fresh ARP requests."""
import ipaddress
import json
import re
import subprocess

from wifi_monitor.core.models import Device, normalize_mac


SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
    $index = __INTERFACE__
    $addresses = @(__HOSTS__)
    $local = @(Get-NetIPAddress -InterfaceIndex $index -AddressFamily IPv4 |
        Where-Object { $_.AddressState -eq 'Preferred' -and
                       $_.PrefixLength -eq __PREFIX__ -and
                       $addresses -contains $_.IPAddress })
    if ($local.Count -ne 1) {
        throw 'The configured Windows interface does not match the configured subnet.'
    }
    $adapter = Get-NetAdapter -InterfaceIndex $index
    if ($adapter.Status -ne 'Up') { throw 'The Windows network adapter is not connected.' }
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Concurrent;
using System.Net;
using System.Runtime.InteropServices;
using System.Threading.Tasks;

public class LanNeighbour {
    public string IPAddress;
    public string LinkLayerAddress;
    public string State = "Reachable";
}
public static class LanDiscovery {
    [DllImport("iphlpapi.dll", ExactSpelling = true)]
    private static extern uint SendARP(uint destination, uint source, byte[] mac, ref uint length);

    public static LanNeighbour[] Scan(string[] hosts, string source) {
        var found = new ConcurrentBag<LanNeighbour>();
        uint sourceAddress = BitConverter.ToUInt32(IPAddress.Parse(source).GetAddressBytes(), 0);
        Parallel.ForEach(hosts, new ParallelOptions { MaxDegreeOfParallelism = 32 }, host => {
            if (host == source) return;
            byte[] mac = new byte[6];
            uint length = 6;
            uint target = BitConverter.ToUInt32(IPAddress.Parse(host).GetAddressBytes(), 0);
            uint result = SendARP(target, sourceAddress, mac, ref length);
            // Windows reports these when the target cannot be resolved.
            if (result == 31 || result == 67) return;
            if (result != 0) throw new InvalidOperationException("SendARP failed: " + result);
            if (length != 6) throw new InvalidOperationException("Unexpected MAC address length.");
            found.Add(new LanNeighbour {
                IPAddress = host, LinkLayerAddress = BitConverter.ToString(mac)
            });
        });
        return found.ToArray();
    }
}
'@
    $devices = @([LanDiscovery]::Scan([string[]]$addresses, $local[0].IPAddress))
    $devices += [pscustomobject]@{
        IPAddress = $local[0].IPAddress
        LinkLayerAddress = $adapter.MacAddress
        State = 'Reachable'
    }
    ConvertTo-Json -InputObject @($devices) -Compress
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""


def parse_neighbours(text: str, subnet: ipaddress.IPv4Network) -> list[Device]:
    rows = json.loads(text)
    if not isinstance(rows, list):
        raise ValueError("Windows discovery must return a JSON array")
    devices = {}
    for row in rows:
        address = ipaddress.IPv4Address(row["IPAddress"])
        mac = normalize_mac(row["LinkLayerAddress"])
        if row["State"] != "Reachable":
            continue
        if address not in subnet or address in (subnet.network_address, subnet.broadcast_address):
            continue
        if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac):
            raise ValueError("Windows returned an invalid MAC address")
        if mac == "00:00:00:00:00:00" or int(mac[:2], 16) & 1:
            continue
        # One device can have multiple IP addresses; count it once by MAC.
        devices.setdefault(mac, Device(mac=mac, ip=str(address)))
    return list(devices.values())


def scan_windows(network) -> list[Device]:
    subnet = ipaddress.ip_network(network.subnet, strict=True)
    if not isinstance(subnet, ipaddress.IPv4Network) or not 22 <= subnet.prefixlen <= 30:
        raise ValueError("Windows discovery requires an IPv4 subnet between /22 and /30")
    index = network.options.get("interface_index")
    if type(index) is not int or index <= 0:
        raise ValueError("Set options.interface_index to the Windows Wi-Fi interface index")
    script = (SCRIPT.replace("__INTERFACE__", str(index))
              .replace("__PREFIX__", str(subnet.prefixlen))
              .replace("__HOSTS__", ",".join(f"'{address}'" for address in subnet.hosts())))
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=180,
    )
    if result.returncode:
        raise RuntimeError(f"Windows discovery failed: {result.stderr.strip()}")
    return parse_neighbours(result.stdout.strip(), subnet)
