"""ADS-B Type Code 4 identification/category message encoder."""

from __future__ import annotations

from xplane_api.adsb.frame import ModeSFrame, build_df17_frame

_TYPE_CODE = 4  # aircraft identification with category not supplied
_CATEGORY_NO_INFORMATION = 0
_CALLSIGN_WIDTH = 8
_ALLOWED_CALLSIGN_CHARS = frozenset(" ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def encode_callsign(callsign: str) -> int:
    """Encode a Mode-S identification field as eight six-bit characters.

    The input is uppercased and padded with spaces. Only the common ADS-B
    callsign alphabet (space, A-Z, 0-9) is accepted; ambiguous punctuation is
    rejected instead of silently substituted.
    """
    if not isinstance(callsign, str):
        raise TypeError("callsign must be a string")
    normalized = callsign.strip().upper()
    if not normalized:
        raise ValueError("callsign must not be empty")
    if len(normalized) > _CALLSIGN_WIDTH:
        raise ValueError("callsign must contain at most eight characters")
    invalid = set(normalized) - _ALLOWED_CALLSIGN_CHARS
    if invalid:
        raise ValueError(f"unsupported callsign characters: {sorted(invalid)!r}")

    encoded = 0
    padded = normalized.ljust(_CALLSIGN_WIDTH)
    for character in padded:
        encoded = (encoded << 6) | (ord(character) & 0x3F)
    return encoded


def build_identification_frame(
    callsign: str,
    icao_address: str | int,
    *,
    capability: int,
) -> ModeSFrame:
    """Build a DF17/TC4 frame; require the capability code explicitly."""
    me_payload = (_TYPE_CODE << 51) | (_CATEGORY_NO_INFORMATION << 48) | encode_callsign(callsign)
    return build_df17_frame(
        icao_address=icao_address,
        me_payload=me_payload,
        capability=capability,
    )


__all__ = ["build_identification_frame", "encode_callsign"]
