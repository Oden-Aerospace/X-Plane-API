"""Blocking UDP client for X-Plane's RREF dataref subscriptions."""

from __future__ import annotations

from dataclasses import dataclass
import math
import socket
import time
from typing import Callable

from xplane_api.protocols.rref import (
    decode_dataref_values,
    encode_subscription_request,
)


@dataclass(frozen=True, slots=True)
class TimedDatarefValue:
    """Latest value and local monotonic receive time for one subscribed path."""

    path: str
    value: float
    received_at_monotonic: float


class RrefClient:
    """Manage read-only RREF subscriptions to one X-Plane UDP endpoint.

    ``poll`` receives at most one UDP packet. RREF packets can contain several
    indexed values, and may arrive asynchronously or out of order. The client
    caches the latest value for each active subscription.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        bind_address: str = "0.0.0.0",
        socket_timeout: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
        sock: socket.socket | None = None,
    ) -> None:
        if not host:
            raise ValueError("host must be a non-empty address")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("port must be an integer from 1 through 65535")
        if not math.isfinite(socket_timeout) or socket_timeout < 0:
            raise ValueError("socket_timeout must be a finite non-negative number")

        self.host = socket.gethostbyname(host)
        self.port = port
        self._clock = clock
        self._socket = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind((bind_address, 0))
        self._socket_timeout = socket_timeout
        self._socket.settimeout(socket_timeout)
        self._path_to_index: dict[str, int] = {}
        self._path_to_frequency: dict[str, int] = {}
        self._index_to_path: dict[int, str] = {}
        self._latest: dict[str, TimedDatarefValue] = {}
        self._next_index = 1
        self._closed = False

    @property
    def local_address(self) -> tuple[str, int]:
        """The local address X-Plane sends subscribed values to."""
        address = self._socket.getsockname()
        return str(address[0]), int(address[1])

    @property
    def subscriptions(self) -> dict[str, int]:
        """A copy of active path-to-index subscriptions."""
        return dict(self._path_to_index)

    @property
    def latest_values(self) -> dict[str, TimedDatarefValue]:
        """A copy of the latest received values, including receive times."""
        return dict(self._latest)

    def subscribe(self, dataref_path: str, frequency_hz: int) -> int:
        """Subscribe to one dataref and return its client-chosen index."""
        self._ensure_open()
        if dataref_path in self._path_to_index:
            raise ValueError(f"already subscribed to {dataref_path!r}")
        index = self._next_index
        while index in self._index_to_path:
            index += 1
        packet = encode_subscription_request(dataref_path, frequency_hz, index)
        self._socket.sendto(packet, (self.host, self.port))
        self._path_to_index[dataref_path] = index
        self._path_to_frequency[dataref_path] = frequency_hz
        self._index_to_path[index] = dataref_path
        self._next_index = index + 1
        return index

    def unsubscribe(self, dataref_path: str) -> bool:
        """Unsubscribe from a path; return false if it was not subscribed."""
        self._ensure_open()
        index = self._path_to_index.get(dataref_path)
        if index is None:
            return False
        packet = encode_subscription_request(dataref_path, 0, index)
        self._socket.sendto(packet, (self.host, self.port))
        del self._path_to_index[dataref_path]
        del self._path_to_frequency[dataref_path]
        del self._index_to_path[index]
        self._latest.pop(dataref_path, None)
        return True

    def unsubscribe_all(self) -> None:
        """Send an unsubscribe request for every active dataref."""
        self._ensure_open()
        for path in tuple(self._path_to_index):
            self.unsubscribe(path)

    def resubscribe_all(self) -> int:
        """Resend subscriptions, e.g. after X-Plane restarts.

        UDP has no persistent session. The caller detects that X-Plane is
        available again and explicitly invokes this method. Cached values are
        retained and remain subject to the stale threshold until refreshed.
        """
        self._ensure_open()
        for path, index in self._path_to_index.items():
            packet = encode_subscription_request(
                path, self._path_to_frequency[path], index
            )
            self._socket.sendto(packet, (self.host, self.port))
        return len(self._path_to_index)

    def poll(self, timeout: float | None = None) -> tuple[TimedDatarefValue, ...]:
        """Receive and cache one RREF packet, returning its known values.

        A timeout returns an empty tuple. Packets from a different host or with
        unrecognized subscription indices are ignored.
        """
        self._ensure_open()
        wait = self._socket_timeout if timeout is None else timeout
        if not math.isfinite(wait) or wait < 0:
            raise ValueError("timeout must be a finite non-negative number")
        self._socket.settimeout(wait)
        try:
            packet, address = self._socket.recvfrom(65535)
        except (socket.timeout, BlockingIOError):
            return ()
        finally:
            self._socket.settimeout(self._socket_timeout)

        if address[0] != self.host:
            return ()
        decoded = decode_dataref_values(packet)
        received_at = self._clock()
        updated: list[TimedDatarefValue] = []
        for value in decoded:
            path = self._index_to_path.get(value.index)
            if path is None:
                continue
            sample = TimedDatarefValue(path, value.value, received_at)
            self._latest[path] = sample
            updated.append(sample)
        return tuple(updated)

    def close(self) -> None:
        """Unsubscribe best-effort and close the UDP socket."""
        if self._closed:
            return
        for path, index in tuple(self._path_to_index.items()):
            try:
                packet = encode_subscription_request(path, 0, index)
                self._socket.sendto(packet, (self.host, self.port))
            except OSError:
                pass
        self._path_to_index.clear()
        self._path_to_frequency.clear()
        self._index_to_path.clear()
        self._latest.clear()
        self._closed = True
        self._socket.close()

    def __enter__(self) -> RrefClient:
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("RREF client is closed")
