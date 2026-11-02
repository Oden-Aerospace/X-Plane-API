"""Typed, unit-bearing snapshots of the pilot-side active flight plan."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Mapping


FLIGHT_PLAN_SCHEMA_VERSION = 1
FLIGHT_PLAN_BRIDGE_VERSION = 3


class FlightPlanStatus(str, Enum):
    AVAILABLE = "available"
    INITIALIZING = "initializing"
    NO_ACTIVE_PLAN = "no_active_plan"
    INVALID_ENTRY_COUNT = "invalid_entry_count"


class PlanFieldStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    REPORTED = "reported"


class NavigationEntryType(str, Enum):
    AIRPORT = "airport"
    NDB = "ndb"
    VOR = "vor"
    ILS = "ils"
    LOCALIZER = "localizer"
    GLIDESLOPE = "glideslope"
    OUTER_MARKER = "outer_marker"
    MIDDLE_MARKER = "middle_marker"
    INNER_MARKER = "inner_marker"
    FIX = "fix"
    DME = "dme"
    LAT_LON = "lat_lon"
    TACAN = "tacan"
    UNKNOWN = "unknown"


_ENTRY_STATUS_FIELDS = {"identifier", "type", "position", "altitude"}


def _required_int(payload: Mapping[str, Any], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _optional_number(payload: Mapping[str, Any], name: str) -> float | None:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number or None")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be a finite number or None")
    return numeric


def _field_statuses(
    payload: Mapping[str, Any], expected_fields: set[str]
) -> Mapping[str, PlanFieldStatus]:
    raw_statuses = payload.get("field_status")
    if not isinstance(raw_statuses, Mapping) or set(raw_statuses) != expected_fields:
        raise ValueError(
            f"field_status must contain exactly {sorted(expected_fields)}"
        )
    try:
        statuses = {
            name: PlanFieldStatus(value) for name, value in raw_statuses.items()
        }
    except (TypeError, ValueError) as exc:
        raise ValueError("field_status contains an unsupported status") from exc
    return MappingProxyType(statuses)


@dataclass(frozen=True, slots=True)
class FlightPlanEntry:
    index: int
    identifier: str | None
    type: NavigationEntryType
    latitude_deg: float | None
    longitude_deg: float | None
    altitude_ft: int | None
    field_status: Mapping[str, PlanFieldStatus]

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int):
            raise TypeError("index must be an integer")
        if self.index < 0:
            raise ValueError("index must be non-negative")
        if self.identifier is not None and (
            not isinstance(self.identifier, str) or not self.identifier
        ):
            raise ValueError("identifier must be a non-empty string or None")
        if not isinstance(self.type, NavigationEntryType):
            try:
                object.__setattr__(self, "type", NavigationEntryType(self.type))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"unsupported navigation entry type: {self.type!r}") from exc

        for name in ("latitude_deg", "longitude_deg"):
            value = getattr(self, name)
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise TypeError(f"{name} must be numeric or None")
                if not math.isfinite(float(value)):
                    raise ValueError(f"{name} must be finite")
                object.__setattr__(self, name, float(value))
        if (self.latitude_deg is None) != (self.longitude_deg is None):
            raise ValueError("latitude_deg and longitude_deg must be both present or None")
        if self.latitude_deg is not None and not -90 <= self.latitude_deg <= 90:
            raise ValueError("latitude_deg must be between -90 and 90")
        if self.longitude_deg is not None and not -180 <= self.longitude_deg <= 180:
            raise ValueError("longitude_deg must be between -180 and 180")
        if self.altitude_ft is not None and (
            isinstance(self.altitude_ft, bool) or not isinstance(self.altitude_ft, int)
        ):
            raise TypeError("altitude_ft must be an integer or None")

        statuses = dict(self.field_status)
        if set(statuses) != _ENTRY_STATUS_FIELDS:
            raise ValueError(
                f"field_status must contain exactly {sorted(_ENTRY_STATUS_FIELDS)}"
            )
        try:
            normalized = {
                name: PlanFieldStatus(value) for name, value in statuses.items()
            }
        except (TypeError, ValueError) as exc:
            raise ValueError("field_status contains an unsupported status") from exc
        values = {
            "identifier": self.identifier,
            "type": None if self.type is NavigationEntryType.UNKNOWN else self.type,
            "position": self.latitude_deg,
            "altitude": self.altitude_ft,
        }
        for name, status in normalized.items():
            if status is PlanFieldStatus.AVAILABLE and values[name] is None:
                raise ValueError(f"{name} cannot be available when its value is missing")
            if status is PlanFieldStatus.UNAVAILABLE and values[name] is not None:
                raise ValueError(f"{name} must be missing when it is unavailable")
            if status is PlanFieldStatus.REPORTED and name != "altitude":
                raise ValueError(f"{name} cannot use the reported status")
            if status is PlanFieldStatus.REPORTED and self.altitude_ft is None:
                raise ValueError("altitude cannot be reported when its value is missing")
        object.__setattr__(self, "field_status", MappingProxyType(normalized))

    @classmethod
    def from_bridge_dict(cls, payload: Mapping[str, Any]) -> "FlightPlanEntry":
        if not isinstance(payload, Mapping):
            raise ValueError("flight plan entry must be an object")
        index = _required_int(payload, "index")
        identifier = payload.get("identifier")
        if identifier is not None and not isinstance(identifier, str):
            raise ValueError("identifier must be a string or None")
        try:
            entry_type = NavigationEntryType(payload.get("type"))
        except (TypeError, ValueError) as exc:
            raise ValueError("type is not a supported navigation entry type") from exc
        altitude = payload.get("altitude_ft")
        if altitude is not None and (
            isinstance(altitude, bool) or not isinstance(altitude, int)
        ):
            raise ValueError("altitude_ft must be an integer or None")
        return cls(
            index=index,
            identifier=identifier,
            type=entry_type,
            latitude_deg=_optional_number(payload, "latitude_deg"),
            longitude_deg=_optional_number(payload, "longitude_deg"),
            altitude_ft=altitude,
            field_status=_field_statuses(payload, _ENTRY_STATUS_FIELDS),
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "identifier": self.identifier,
            "type": self.type.value,
            "latitude_deg": self.latitude_deg,
            "longitude_deg": self.longitude_deg,
            "altitude_ft": self.altitude_ft,
            "field_status": {
                name: status.value for name, status in self.field_status.items()
            },
        }


@dataclass(frozen=True, slots=True)
class FlightPlanState:
    timestamp_utc: datetime
    source: str
    available: bool
    status: FlightPlanStatus
    active_index: int | None
    active_index_status: PlanFieldStatus
    entries: tuple[FlightPlanEntry, ...]
    bridge_version: int = FLIGHT_PLAN_BRIDGE_VERSION
    schema_version: int = FLIGHT_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp_utc, datetime):
            raise TypeError("timestamp_utc must be a datetime")
        if self.timestamp_utc.tzinfo is None or self.timestamp_utc.utcoffset() is None:
            raise ValueError("timestamp_utc must include a timezone")
        object.__setattr__(
            self, "timestamp_utc", self.timestamp_utc.astimezone(timezone.utc)
        )
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("source must be a non-empty string")
        if not isinstance(self.available, bool):
            raise TypeError("available must be a boolean")
        if not isinstance(self.status, FlightPlanStatus):
            try:
                object.__setattr__(self, "status", FlightPlanStatus(self.status))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"unsupported flight plan status: {self.status!r}") from exc
        if not isinstance(self.active_index_status, PlanFieldStatus):
            try:
                object.__setattr__(
                    self,
                    "active_index_status",
                    PlanFieldStatus(self.active_index_status),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("unsupported active_index_status") from exc
        if (
            isinstance(self.bridge_version, bool)
            or not isinstance(self.bridge_version, int)
            or self.bridge_version != FLIGHT_PLAN_BRIDGE_VERSION
        ):
            raise ValueError(f"unsupported bridge version: {self.bridge_version!r}")
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != FLIGHT_PLAN_SCHEMA_VERSION
        ):
            raise ValueError(f"unsupported schema version: {self.schema_version!r}")

        entries = tuple(self.entries)
        if any(not isinstance(entry, FlightPlanEntry) for entry in entries):
            raise TypeError("entries must contain FlightPlanEntry values")
        if tuple(entry.index for entry in entries) != tuple(range(len(entries))):
            raise ValueError("entry indices must be consecutive and ordered")
        if self.available != bool(entries):
            raise ValueError("available must be true exactly when entries are present")
        if self.available != (self.status is FlightPlanStatus.AVAILABLE):
            raise ValueError("available and status disagree")
        if self.active_index is None:
            if self.active_index_status is not PlanFieldStatus.UNAVAILABLE:
                raise ValueError("active_index must be unavailable when it is None")
        else:
            if isinstance(self.active_index, bool) or not isinstance(self.active_index, int):
                raise TypeError("active_index must be an integer or None")
            if not 0 <= self.active_index < len(entries):
                raise ValueError("active_index must refer to an entry")
            if self.active_index_status is not PlanFieldStatus.AVAILABLE:
                raise ValueError("active_index must be available when it has a value")
        object.__setattr__(self, "entries", entries)

    @classmethod
    def from_bridge_dict(cls, payload: Mapping[str, Any]) -> "FlightPlanState":
        if not isinstance(payload, Mapping):
            raise ValueError("flight plan snapshot must be an object")
        schema_version = _required_int(payload, "schema_version")
        bridge_version = _required_int(payload, "bridge_version")
        if schema_version != FLIGHT_PLAN_SCHEMA_VERSION:
            raise ValueError(f"unsupported flight plan schema: {schema_version}")
        if bridge_version != FLIGHT_PLAN_BRIDGE_VERSION:
            raise ValueError(f"unsupported flight plan bridge: {bridge_version}")

        generated_at_ms = _required_int(payload, "generated_at_unix_ms")
        if generated_at_ms < 0:
            raise ValueError("generated_at_unix_ms must be non-negative")
        try:
            timestamp = datetime.fromtimestamp(
                generated_at_ms / 1000, tz=timezone.utc
            )
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError("generated_at_unix_ms is outside the supported range") from exc

        source = payload.get("source")
        if not isinstance(source, str):
            raise ValueError("source must be a string")
        available = payload.get("available")
        if not isinstance(available, bool):
            raise ValueError("available must be a boolean")
        try:
            status = FlightPlanStatus(payload.get("status"))
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported flight plan status") from exc
        active_index = payload.get("active_index")
        if active_index is not None and (
            isinstance(active_index, bool) or not isinstance(active_index, int)
        ):
            raise ValueError("active_index must be an integer or None")
        try:
            active_index_status = PlanFieldStatus(payload.get("active_index_status"))
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported active_index_status") from exc
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            raise ValueError("entries must be a list")
        entries = tuple(FlightPlanEntry.from_bridge_dict(entry) for entry in raw_entries)
        return cls(
            timestamp_utc=timestamp,
            source=source,
            available=available,
            status=status,
            active_index=active_index,
            active_index_status=active_index_status,
            entries=entries,
            bridge_version=bridge_version,
            schema_version=schema_version,
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "bridge_version": self.bridge_version,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "source": self.source,
            "available": self.available,
            "status": self.status.value,
            "active_index": self.active_index,
            "active_index_status": self.active_index_status.value,
            "entries": [entry.to_json_dict() for entry in self.entries],
        }
