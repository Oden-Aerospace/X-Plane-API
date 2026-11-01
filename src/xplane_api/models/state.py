"""Normalized, timestamped aircraft state shared by live and replay sources.

Numeric field names carry their units. ``field_status`` records whether a
value is valid, unavailable, stale, or invalid; absent statuses are inferred
as valid for present values and unavailable for ``None`` values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Mapping


SCHEMA_VERSION = 1


class StateSource(str, Enum):
    """Origin of a normalized state sample."""

    LIVE_XPLANE = "live_xplane"
    RECORDING = "recording"
    TEST = "test"


class FieldStatus(str, Enum):
    """Usability of one optional value in a state sample."""

    VALID = "valid"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    INVALID = "invalid"


_VALUE_FIELDS = (
    "latitude_deg",
    "longitude_deg",
    "elevation_msl_m",
    "barometric_altitude_ft",
    "indicated_airspeed_kt",
    "ground_speed_mps",
    "vertical_speed_fpm",
    "true_heading_deg",
    "magnetic_heading_deg",
    "flight_path_heading_deg",
    "ground_track_deg",
    "pitch_deg",
    "roll_deg",
    "turn_indicator_deflection_deg",
    "slip_indicator_deflection_deg",
    "on_ground",
    "flight_id",
    "aircraft_type_code",
    "tail_number",
    "barometer_setting_inhg",
)


@dataclass(frozen=True, slots=True)
class FlightState:
    """A single normalized sample, with explicit unit-bearing field names.

    X-Plane source candidates verified on XP12.4.3/C172 G1000 include:
    latitude/longitude (degrees), elevation (meters MSL), indicated altitude
    (feet), indicated airspeed (knots), groundspeed (m/s), vertical speed
    (feet/minute), AHARS attitude and magnetic/true heading (degrees), and
    turn/slip indicator deflection (degrees). Source adapters must not infer
    missing quality/integrity values from these kinematic fields.
    """

    timestamp_utc: datetime
    source: StateSource = StateSource.TEST
    sequence: int | None = None

    latitude_deg: float | None = None
    longitude_deg: float | None = None
    elevation_msl_m: float | None = None
    barometric_altitude_ft: float | None = None
    indicated_airspeed_kt: float | None = None
    ground_speed_mps: float | None = None
    vertical_speed_fpm: float | None = None
    true_heading_deg: float | None = None
    magnetic_heading_deg: float | None = None
    flight_path_heading_deg: float | None = None
    ground_track_deg: float | None = None
    pitch_deg: float | None = None
    roll_deg: float | None = None
    turn_indicator_deflection_deg: float | None = None
    slip_indicator_deflection_deg: float | None = None
    on_ground: bool | None = None
    flight_id: str | None = None
    aircraft_type_code: str | None = None
    tail_number: str | None = None
    barometer_setting_inhg: float | None = None

    field_status: Mapping[str, FieldStatus] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp_utc, datetime):
            raise TypeError("timestamp_utc must be a datetime")
        if self.timestamp_utc.tzinfo is None or self.timestamp_utc.utcoffset() is None:
            raise ValueError("timestamp_utc must include a timezone")
        object.__setattr__(
            self, "timestamp_utc", self.timestamp_utc.astimezone(timezone.utc)
        )

        if not isinstance(self.source, StateSource):
            try:
                object.__setattr__(self, "source", StateSource(self.source))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"unknown state source: {self.source!r}") from exc

        if self.sequence is not None and (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 0
        ):
            raise ValueError("sequence must be a non-negative integer or None")

        for field_name in _VALUE_FIELDS:
            value = getattr(self, field_name)
            if field_name == "on_ground":
                if value is not None and not isinstance(value, bool):
                    raise TypeError("on_ground must be bool or None")
            elif field_name in ("flight_id", "aircraft_type_code", "tail_number"):
                if value is not None and not isinstance(value, str):
                    raise TypeError(f"{field_name} must be str or None")
            elif value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise TypeError(f"{field_name} must be numeric or None")
                if not math.isfinite(float(value)):
                    raise ValueError(f"{field_name} must be finite")
                object.__setattr__(self, field_name, float(value))

        if self.latitude_deg is not None and not -90.0 <= self.latitude_deg <= 90.0:
            raise ValueError("latitude_deg must be between -90 and 90")
        if self.longitude_deg is not None and not -180.0 <= self.longitude_deg <= 180.0:
            raise ValueError("longitude_deg must be between -180 and 180")

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
                raise ValueError(f"{field_name} cannot be valid when its value is None")
            normalized_status[field_name] = status
        object.__setattr__(self, "field_status", MappingProxyType(normalized_status))

    def is_usable(self, field_name: str) -> bool:
        """Return whether a named field is present and explicitly valid."""
        if field_name not in _VALUE_FIELDS:
            raise KeyError(field_name)
        return (
            getattr(self, field_name) is not None
            and self.field_status[field_name] is FieldStatus.VALID
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize one sample to a JSON-compatible versioned object."""
        result: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "timestamp_utc": self.timestamp_utc.isoformat().replace("+00:00", "Z"),
            "source": self.source.value,
            "sequence": self.sequence,
        }
        for name in _VALUE_FIELDS:
            result[name] = getattr(self, name)
        result["field_status"] = {
            name: status.value for name, status in self.field_status.items()
        }
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FlightState:
        """Parse a versioned JSON-compatible state object strictly."""
        if not isinstance(data, Mapping):
            raise TypeError("state record must be a mapping")
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported state schema_version: {version!r}")

        allowed = {
            "schema_version",
            "timestamp_utc",
            "source",
            "sequence",
            "field_status",
            *_VALUE_FIELDS,
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown state fields: {sorted(unknown)}")

        timestamp_value = data.get("timestamp_utc")
        if not isinstance(timestamp_value, str):
            raise ValueError("timestamp_utc must be an ISO-8601 string")
        parsed_timestamp = datetime.fromisoformat(
            timestamp_value[:-1] + "+00:00"
            if timestamp_value.endswith("Z")
            else timestamp_value
        )

        values = {name: data.get(name) for name in _VALUE_FIELDS}
        return cls(
            timestamp_utc=parsed_timestamp,
            source=StateSource(data.get("source", StateSource.RECORDING.value)),
            sequence=data.get("sequence"),
            field_status=data.get("field_status", {}),
            **values,
        )

    @classmethod
    def value_field_names(cls) -> tuple[str, ...]:
        """Return the ordered names represented in ``field_status``."""
        return _VALUE_FIELDS
