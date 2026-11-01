"""Packet helpers for X-Plane's UDP RREF dataref protocol.

This module handles wire-format encoding and decoding only. It does not
identify supported datarefs or establish X-Plane 12 compatibility; those
require validation against the target simulator and aircraft.

Protocol reference: https://xppython3.readthedocs.io/en/latest/development/udp/rref.html
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Tuple

_REQUEST = struct.Struct("<4sxii400s")
# The published packet example shows RREF\0; XP 12.4.3 emits RREF,.
_RESPONSE_HEADERS = (b"RREF\x00", b"RREF,")
_VALUE = struct.Struct("<if")
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1
_MAX_DATAREF_BYTES = 399  # reserve one byte for the C-string terminator


@dataclass(frozen=True)
class DatarefValue:
    """One indexed dataref value received in an RREF response."""

    index: int
    value: float


def encode_subscription_request(
    dataref_path: str,
    frequency_hz: int,
    index: int,
) -> bytes:
    """Encode an RREF subscription or unsubscription request.

    ``frequency_hz=0`` unsubscribes the supplied index/path pair. The request
    frequency and index are signed 32-bit integers in X-Plane's UDP packet.
    """
    if not isinstance(dataref_path, str) or not dataref_path:
        raise ValueError("dataref_path must be a non-empty string")
    if "\x00" in dataref_path:
        raise ValueError("dataref_path must not contain a NUL character")

    encoded_path = dataref_path.encode("utf-8")
    if len(encoded_path) > _MAX_DATAREF_BYTES:
        raise ValueError(
            f"dataref_path exceeds {_MAX_DATAREF_BYTES} UTF-8 bytes"
        )

    if isinstance(frequency_hz, bool) or not isinstance(frequency_hz, int):
        raise ValueError("frequency_hz must be an integer")
    if frequency_hz < 0 or frequency_hz > _INT32_MAX:
        raise ValueError("frequency_hz must be between 0 and 2^31 - 1")

    if isinstance(index, bool) or not isinstance(index, int):
        raise ValueError("index must be an integer")
    if index < _INT32_MIN or index > _INT32_MAX:
        raise ValueError("index must fit in a signed 32-bit integer")

    return _REQUEST.pack(b"RREF", frequency_hz, index, encoded_path)


def decode_dataref_values(packet: bytes) -> Tuple[DatarefValue, ...]:
    """Decode one RREF UDP response containing one or more indexed values.

    Raises ``ValueError`` for an unexpected header or malformed payload. The
    caller maps returned indices to its active subscription table.
    """
    if len(packet) < 5:
        raise ValueError("RREF response is shorter than its header")
    if packet[:5] not in _RESPONSE_HEADERS:
        raise ValueError("unexpected RREF response header")

    payload = packet[5:]
    if not payload:
        raise ValueError("RREF response contains no values")
    if len(payload) % _VALUE.size:
        raise ValueError("RREF response payload is not a whole number of values")

    return tuple(
        DatarefValue(*_VALUE.unpack_from(payload, offset))
        for offset in range(0, len(payload), _VALUE.size)
    )
