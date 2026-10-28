"""Local network liveness with an exact expected MAC, independent of JWT."""
import ipaddress
from pathlib import Path
import re
import subprocess
import sys


def normalize_host(value):
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, TypeError):
        raise ValueError("A private LAN IPv4 address is required") from None
    if not any(address in ipaddress.IPv4Network(net) for net in
               ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        raise ValueError("A private LAN IPv4 address is required")
    return str(address)


def normalize_mac(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}", value):
        raise ValueError("An Ethernet MAC address is required")
    value = value.lower()
    if value == "00:00:00:00:00:00" or int(value[:2], 16) & 1:
        raise ValueError("A unicast device MAC address is required")
    return value


def _ping(host):
    if sys.platform == "darwin":
        return subprocess.run(["/sbin/ping", "-c", "1", "-W", "1000", host],
                              capture_output=True, timeout=3, check=False).returncode == 0
    from icmplib import ping, SocketPermissionError
    try:
        return ping(host, count=1, timeout=1, privileged=False).is_alive
    except SocketPermissionError:
        return ping(host, count=1, timeout=1, privileged=True).is_alive


def _neighbor_mac(host):
    if sys.platform == "darwin":
        output = subprocess.run(["/usr/sbin/arp", "-n", host], capture_output=True,
                                text=True, timeout=3, check=False).stdout.lower()
        match = re.search(r"\bat\s+([0-9a-f:]+)\s", output)
        return ":".join(part.zfill(2) for part in match.group(1).split(":")) if match else None
    for line in Path("/proc/net/arp").read_text().splitlines()[1:]:
        columns = line.split()
        if len(columns) >= 6 and columns[0] == host:
            try:
                if int(columns[2], 16) & 2:
                    return normalize_mac(columns[3])
            except ValueError:
                return None
    return None


class LANProbe:
    def __init__(self, host, expected_mac):
        self.host = normalize_host(host)
        self.expected_mac = normalize_mac(expected_mac)
        self.last_failure = None
        self.last_error_type = None

    def check(self):
        """True requires both a new ICMP reply and matching LAN neighbor identity.

        False means unreachable or a different identity. None means this host
        cannot perform the check. Both block controls; neither implies that a
        stored cloud Shadow is a physical online indication.
        """
        self.last_failure = self.last_error_type = None
        try:
            if not _ping(self.host):
                self.last_failure = "icmp_unreachable"
                return False
            neighbor = _neighbor_mac(self.host)
            if neighbor is None:
                self.last_failure = "neighbor_missing"
                return False
            if neighbor != self.expected_mac:
                self.last_failure = "neighbor_mismatch"
                return False
            return True
        except Exception as exc:
            # Store only a class name, never exception text or network payloads.
            self.last_failure = "local_probe_error"
            self.last_error_type = type(exc).__name__
            return None
