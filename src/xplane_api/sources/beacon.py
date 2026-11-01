"""X-Plane multicast beacon discovery for local UDP connections."""

from __future__ import annotations

from dataclasses import dataclass
import math
import platform
import socket
import struct
import time


BEACON_GROUP = "239.255.1.1"
BEACON_PORT = 49707
_BEACON_HEADER = b"BECN\x00"
_BEACON_DATA = struct.Struct("<BBiiIH")


@dataclass(frozen=True, slots=True)
class XPlaneBeacon:
    """Network endpoint and metadata advertised by an X-Plane instance."""

    host: str
    port: int
    version: int
    role: int
    hostname: str
    raknet_port: int


def parse_beacon(packet: bytes, sender_host: str) -> XPlaneBeacon:
    """Parse a beacon packet, raising ValueError for unsupported packets."""
    minimum_size = len(_BEACON_HEADER) + _BEACON_DATA.size + 2
    if len(packet) < minimum_size:
        raise ValueError("X-Plane beacon is shorter than its fixed fields")
    if packet[: len(_BEACON_HEADER)] != _BEACON_HEADER:
        raise ValueError("unexpected X-Plane beacon header")

    major, minor, application_host_id, version, role, udp_port = _BEACON_DATA.unpack(
        packet[len(_BEACON_HEADER) : len(_BEACON_HEADER) + _BEACON_DATA.size]
    )
    if major != 1 or minor != 2:
        raise ValueError(f"unsupported beacon protocol version {major}.{minor}")
    if application_host_id != 1:
        raise ValueError("beacon is not from the X-Plane application")
    if udp_port == 0:
        raise ValueError("beacon advertises UDP port zero")

    hostname_bytes = packet[len(_BEACON_HEADER) + _BEACON_DATA.size : -2]
    hostname = hostname_bytes.split(b"\x00", 1)[0].decode("utf-8", "replace")
    raknet_port = struct.unpack("<H", packet[-2:])[0]
    return XPlaneBeacon(
        host=sender_host,
        port=udp_port,
        version=version,
        role=role,
        hostname=hostname,
        raknet_port=raknet_port,
    )


def discover_xplane(
    timeout_seconds: float = 3.0,
    *,
    host_filter: str | None = None,
) -> XPlaneBeacon:
    """Wait for the first X-Plane master beacon on the local network.

    XP12 may require enabling "Accept incoming connections" in its Network
    settings. A timeout raises ``TimeoutError``. The discovery socket is always
    closed before returning or raising.
    """
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise ValueError("timeout_seconds must be a finite positive number")
    if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
        raise ValueError("timeout_seconds must be a finite positive number")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bind_address = (
            ("", BEACON_PORT)
            if platform.system() == "Windows"
            else (BEACON_GROUP, BEACON_PORT)
        )
        sock.bind(bind_address)
        membership = struct.pack(
            "=4s4s", socket.inet_aton(BEACON_GROUP), socket.inet_aton("0.0.0.0")
        )
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        deadline = time.monotonic() + float(timeout_seconds)

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("no X-Plane beacon received before timeout")
            sock.settimeout(remaining)
            try:
                packet, address = sock.recvfrom(15000)
            except socket.timeout as exc:
                raise TimeoutError("no X-Plane beacon received before timeout") from exc

            try:
                beacon = parse_beacon(packet, address[0])
            except ValueError:
                continue
            if beacon.role != 1:
                continue
            if host_filter is not None and beacon.host != host_filter:
                continue
            return beacon
    finally:
        sock.close()


__all__ = ["BEACON_GROUP", "BEACON_PORT", "XPlaneBeacon", "discover_xplane", "parse_beacon"]
