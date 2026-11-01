"""Packet helpers for X-Plane's UDP DREF dataref-write protocol.

A ``DREF`` packet writes one float value to one dataref, like turning a
cockpit selector to a value. This module handles wire-format encoding only.
It does not identify writable datarefs or establish X-Plane 12
compatibility; those require validation against the target simulator and
aircraft. The protocol provides no acknowledgment: the effect must be
confirmed by reading the dataref back afterwards.

Protocol reference: X-Plane UDP "DREF" packet — a 4-byte ``DREF`` prologue,
one NUL, a little-endian float32, then the dataref path as a NUL-terminated
C string padded with spaces to 500 bytes (509 bytes total).
"""

from __future__ import annotations

import math
import struct

_PACKET = struct.Struct("<4sxf500s")
_PATH_FIELD_BYTES = 500
# Reserve one byte for the C-string terminator inside the 500-byte field.
_MAX_DATAREF_BYTES = 499


def encode_dataref_write(dataref_path: str, value: float) -> bytes:
    """Encode one DREF packet writing ``value`` to ``dataref_path``.

    ``value`` must be a finite real number representable as a float32.
    Raises ``ValueError`` for an empty path, an embedded NUL, an overlong
    path, or a non-finite value.
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

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value must be a real number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("value must be finite")

    padded_path = encoded_path + b"\x00"
    padded_path += b" " * (_PATH_FIELD_BYTES - len(padded_path))
    return _PACKET.pack(b"DREF", numeric, padded_path)
