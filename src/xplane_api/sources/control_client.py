"""Write-capable UDP client for X-Plane command triggers and dataref writes.

Constructing :class:`XPlaneControlClient` is the explicit opt-in to sending
control traffic. Read-only telemetry never requires this class, so read-only
use structurally cannot transmit ``CMND`` or ``DREF`` packets.

X-Plane's UDP protocol gives no acknowledgment for either packet type. A
successful send means only that the packet left this host; the authoritative
result of any input is the simulator state read back afterwards.
"""

from __future__ import annotations

import math
import socket

from xplane_api.protocols.cmnd import encode_command_request
from xplane_api.protocols.dref import encode_dataref_write


class XPlaneControlClient:
    """Send command-trigger and dataref-write packets to one X-Plane endpoint.

    This client transmits control traffic to a local or LAN simulator the
    caller controls. It performs wire-format validation before any packet is
    built; nothing is transmitted when validation fails.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        sock: socket.socket | None = None,
    ) -> None:
        if not host:
            raise ValueError("host must be a non-empty address")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("port must be an integer from 1 through 65535")

        self.host = socket.gethostbyname(host)
        self.port = port
        self._socket = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._closed = False

    def send_command(self, command_path: str) -> None:
        """Trigger ``command_path`` once, like a momentary button press.

        Raises ``ValueError`` for an invalid path and ``OSError`` if the
        packet cannot be sent. Successful return does not confirm the
        simulator acted; confirm by reading state back.
        """
        self._ensure_open()
        packet = encode_command_request(command_path)
        self._socket.sendto(packet, (self.host, self.port))

    def write_dataref(self, dataref_path: str, value: float) -> None:
        """Write a finite float ``value`` to ``dataref_path``.

        Raises ``ValueError`` for an invalid path or non-finite value and
        ``OSError`` if the packet cannot be sent. Successful return does not
        confirm the simulator applied the value; confirm by reading the
        dataref back.
        """
        self._ensure_open()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("value must be a real number")
        if not math.isfinite(float(value)):
            raise ValueError("value must be finite")
        packet = encode_dataref_write(dataref_path, float(value))
        self._socket.sendto(packet, (self.host, self.port))

    def close(self) -> None:
        """Close the send socket; further sends raise ``RuntimeError``."""
        if not self._closed:
            self._closed = True
            self._socket.close()

    def __enter__(self) -> "XPlaneControlClient":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("control client is closed")
