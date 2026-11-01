"""Normalized, timestamped autopilot (AFCS) state and mode vocabulary.

The public contract uses generic AFCS mode names like a modern small-aircraft
flight-control annunciator. Raw simulator status values are retained for
diagnostics, and simulator states that do not map onto the vocabulary are
represented explicitly as ``UNKNOWN`` — never guessed. Numeric field names
carry their units. ``field_status`` records whether each value is valid,
unavailable, stale, or invalid, following ``FlightState`` conventions.

This models the X-Plane 12 autopilot as annunciated through its datarefs. It
is simulation tooling, not certified avionics, and does not model any
specific real AFCS product.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Mapping

from xplane_api.models.state import FieldStatus


AUTOPILOT_SCHEMA_VERSION = 1


class LateralMode(str, Enum):
    """Annunciated lateral (roll) AFCS mode."""

    NONE = "NONE"
    ROL = "ROL"
    HDG = "HDG"
    NAV = "NAV"
    GPSS = "GPSS"
    APR = "APR"
    BC = "BC"
    GA = "GA"
    UNKNOWN = "UNKNOWN"


class VerticalMode(str, Enum):
    """Annunciated vertical (pitch) AFCS mode."""

    NONE = "NONE"
    PIT = "PIT"
    ALT = "ALT"
    ALTS = "ALTS"
    ALTV = "ALTV"
    VS = "VS"
    FLC = "FLC"
    GS = "GS"
    VNV = "VNV"
    GA = "GA"
    UNKNOWN = "UNKNOWN"


class AfcsButton(str, Enum):
    """Momentary mode-controller buttons exposed as inputs."""

    AP = "AP"
    AP_DISC = "AP_DISC"
    FD = "FD"
    YD = "YD"
    HDG = "HDG"
    ROL = "ROL"
    NAV = "NAV"
    APR = "APR"
    BC = "BC"
    ALT = "ALT"
    VS = "VS"
    FLC = "FLC"
    VNV = "VNV"
    GA = "GA"
    GS = "GS"


class AfcsSelector(str, Enum):
    """Continuous target selectors (bugs/dials) exposed as inputs."""

    HEADING = "HEADING"
    ALTITUDE = "ALTITUDE"
    VERTICAL_SPEED = "VERTICAL_SPEED"
    AIRSPEED = "AIRSPEED"
    COURSE = "COURSE"


_BOOL_FIELDS = (
    "ap_engaged",
    "flight_director_on",
    "yaw_damper_on",
    "ap_disconnect_annunciated",
)
_LATERAL_MODE_FIELDS = ("lateral_mode_active", "lateral_mode_armed")
_VERTICAL_MODE_FIELDS = ("vertical_mode_active", "vertical_mode_armed")
_NUMERIC_FIELDS = (
    "selected_heading_deg_mag",
    "selected_altitude_ft",
    "selected_vertical_speed_fpm",
    "selected_airspeed_kt",
    "selected_course_deg_mag",
    "fd_pitch_command_deg",
    "fd_roll_command_deg",
)
_VALUE_FIELDS = (
    _BOOL_FIELDS + _LATERAL_MODE_FIELDS + _VERTICAL_MODE_FIELDS + _NUMERIC_FIELDS
)


@dataclass(frozen=True, slots=True)
class AutopilotState:
    """One annunciated autopilot state snapshot.

    Mode fields use ``NONE`` for a validly blank annunciation (nothing active
    or armed) and ``None`` when the underlying data is unavailable or stale.
    ``raw_status_values`` retains the raw simulator values the modes were
    derived from, keyed by dataref path, for diagnostics; it is not part of
    the mode contract.
    """

    timestamp_utc: datetime

    ap_engaged: bool | None = None
    flight_director_on: bool | None = None
    yaw_damper_on: bool | None = None
    ap_disconnect_annunciated: bool | None = None

    lateral_mode_active: LateralMode | None = None
    lateral_mode_armed: LateralMode | None = None
    vertical_mode_active: VerticalMode | None = None
    vertical_mode_armed: VerticalMode | None = None

    selected_heading_deg_mag: float | None = None
    selected_altitude_ft: float | None = None
    selected_vertical_speed_fpm: float | None = None
    selected_airspeed_kt: float | None = None
    selected_course_deg_mag: float | None = None

    fd_pitch_command_deg: float | None = None
    fd_roll_command_deg: float | None = None

    raw_status_values: Mapping[str, float] = field(default_factory=dict)
    field_status: Mapping[str, FieldStatus] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp_utc, datetime):
            raise TypeError("timestamp_utc must be a datetime")
        if self.timestamp_utc.tzinfo is None or self.timestamp_utc.utcoffset() is None:
            raise ValueError("timestamp_utc must include a timezone")
        object.__setattr__(
            self, "timestamp_utc", self.timestamp_utc.astimezone(timezone.utc)
        )

        for field_name in _BOOL_FIELDS:
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, bool):
                raise TypeError(f"{field_name} must be bool or None")

        for field_name in _LATERAL_MODE_FIELDS:
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, LateralMode):
                try:
                    object.__setattr__(self, field_name, LateralMode(value))
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"invalid {field_name}: {value!r}"
                    ) from exc

        for field_name in _VERTICAL_MODE_FIELDS:
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, VerticalMode):
                try:
                    object.__setattr__(self, field_name, VerticalMode(value))
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"invalid {field_name}: {value!r}"
                    ) from exc

        for field_name in _NUMERIC_FIELDS:
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{field_name} must be numeric or None")
            if not math.isfinite(float(value)):
                raise ValueError(f"{field_name} must be finite")
            object.__setattr__(self, field_name, float(value))

        raw_values = dict(self.raw_status_values)
        for path, raw in raw_values.items():
            if not isinstance(path, str) or not path:
                raise ValueError("raw_status_values keys must be dataref paths")
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise TypeError(f"raw_status_values[{path!r}] must be numeric")
            raw_values[path] = float(raw)
        object.__setattr__(
            self, "raw_status_values", MappingProxyType(raw_values)
        )

        raw_status = dict(self.field_status)
        unknown = raw_status.keys() - set(_VALUE_FIELDS)
        if unknown:
            raise ValueError(f"unknown field_status keys: {sorted(unknown)}")

        normalized_status: dict[str, FieldStatus] = {}
        for field_name in _VALUE_FIELDS:
            raw_value = raw_status.get(field_name)
            if raw_value is None:
                status = (
                    FieldStatus.VALID
                    if getattr(self, field_name) is not None
                    else FieldStatus.UNAVAILABLE
                )
            else:
                try:
                    status = FieldStatus(raw_value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"invalid status for {field_name}: {raw_value!r}"
                    ) from exc
            if status is FieldStatus.VALID and getattr(self, field_name) is None:
                raise ValueError(
                    f"{field_name} cannot be valid when its value is None"
                )
            normalized_status[field_name] = status
        object.__setattr__(self, "field_status", MappingProxyType(normalized_status))

    def is_usable(self, field_name: str) -> bool:
        """Return whether a named field is present and explicitly valid."""
        if field_name not in _VALUE_FIELDS:
            raise KeyError(f"unknown field: {field_name}")
        return self.field_status[field_name] is FieldStatus.VALID

    def to_json_dict(self) -> dict[str, Any]:
        """Return a lossless JSON-compatible representation of this state."""
        payload: dict[str, Any] = {
            "schema_version": AUTOPILOT_SCHEMA_VERSION,
            "timestamp_utc": self.timestamp_utc.isoformat(),
        }
        for field_name in _VALUE_FIELDS:
            value = getattr(self, field_name)
            if isinstance(value, Enum):
                value = value.value
            payload[field_name] = value
        payload["raw_status_values"] = dict(self.raw_status_values)
        payload["field_status"] = {
            name: status.value for name, status in self.field_status.items()
        }
        return payload

    @classmethod
    def from_json_dict(cls, payload: Mapping[str, Any]) -> "AutopilotState":
        """Rebuild a state from :meth:`to_json_dict` output."""
        version = payload.get("schema_version")
        if version != AUTOPILOT_SCHEMA_VERSION:
            raise ValueError(f"unsupported autopilot schema version: {version!r}")
        kwargs: dict[str, Any] = {
            "timestamp_utc": datetime.fromisoformat(payload["timestamp_utc"]),
            "raw_status_values": dict(payload.get("raw_status_values", {})),
            "field_status": {
                name: FieldStatus(value)
                for name, value in payload.get("field_status", {}).items()
            },
        }
        for field_name in _VALUE_FIELDS:
            kwargs[field_name] = payload.get(field_name)
        return cls(**kwargs)
