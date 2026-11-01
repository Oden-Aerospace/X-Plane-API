"""Packet helpers for X-Plane's UDP CMND command-trigger protocol.

A ``CMND`` packet asks the simulator to actuate one command once, like a
momentary button press. This module handles wire-format encoding only. It
does not identify supported commands or establish X-Plane 12 compatibility;
those require validation against the target simulator and aircraft. The
protocol provides no acknowledgment: delivery and effect must be confirmed
by reading simulator state afterwards.

Protocol reference: X-Plane UDP "CMND" packet — a 4-byte ``CMND`` prologue,
one NUL, then the command path as a C string.
"""

from __future__ import annotations

_HEADER = b"CMND\x00"
# Match the RREF codec's conservative path budget; command paths observed in
# the installed Commands.txt catalog are far shorter.
_MAX_COMMAND_BYTES = 399


def encode_command_request(command_path: str) -> bytes:
    """Encode one CMND packet that triggers ``command_path`` once.

    Raises ``ValueError`` for an empty path, an embedded NUL, or a path
    longer than the supported byte budget.
    """
    if not isinstance(command_path, str) or not command_path:
        raise ValueError("command_path must be a non-empty string")
    if "\x00" in command_path:
        raise ValueError("command_path must not contain a NUL character")

    encoded_path = command_path.encode("utf-8")
    if len(encoded_path) > _MAX_COMMAND_BYTES:
        raise ValueError(
            f"command_path exceeds {_MAX_COMMAND_BYTES} UTF-8 bytes"
        )

    return _HEADER + encoded_path + b"\x00"
