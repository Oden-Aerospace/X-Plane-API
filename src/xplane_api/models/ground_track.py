"""Ground-track derivation from successive valid geographic samples."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math

from xplane_api.models.state import FieldStatus, FlightState


_EARTH_RADIUS_M = 6_371_000.0


@dataclass(slots=True)
class GroundTrackDeriver:
    """Enrich samples with course over ground from successive positions.

    ``minimum_movement_m`` is deliberately required: the project has not yet
    selected a GPS-noise/movement threshold. Until a valid baseline has moved
    farther than this threshold, ``ground_track_deg`` is unavailable. The
    baseline remains anchored until sufficient movement accumulates.
    """

    minimum_movement_m: float
    _baseline: FlightState | None = field(init=False, default=None, repr=False)

    @classmethod
    def for_adsb_test_output(cls) -> GroundTrackDeriver:
        """Create the user-selected 1 m minimum-movement configuration."""
        return cls(minimum_movement_m=1.0)

    def __post_init__(self) -> None:
        if (
            isinstance(self.minimum_movement_m, bool)
            or not isinstance(self.minimum_movement_m, (int, float))
            or not math.isfinite(self.minimum_movement_m)
            or self.minimum_movement_m < 0
        ):
            raise ValueError("minimum_movement_m must be finite and non-negative")

    def reset(self) -> None:
        """Forget the course baseline, for source changes or replay resets."""
        self._baseline = None

    def update(self, sample: FlightState) -> FlightState:
        """Return a copy with derived track and updated field status."""
        statuses = dict(sample.field_status)
        statuses["ground_track_deg"] = FieldStatus.UNAVAILABLE
        output = replace(sample, ground_track_deg=None, field_status=statuses)

        if not sample.is_usable("latitude_deg") or not sample.is_usable("longitude_deg"):
            return output

        if self._baseline is None:
            self._baseline = sample
            return output

        baseline = self._baseline
        elapsed = (sample.timestamp_utc - baseline.timestamp_utc).total_seconds()
        if elapsed <= 0:
            statuses["ground_track_deg"] = FieldStatus.INVALID
            return replace(output, field_status=statuses)

        distance_m = _distance_m(
            baseline.latitude_deg,
            baseline.longitude_deg,
            sample.latitude_deg,
            sample.longitude_deg,
        )
        if distance_m <= self.minimum_movement_m:
            return output

        track = _initial_bearing_deg(
            baseline.latitude_deg,
            baseline.longitude_deg,
            sample.latitude_deg,
            sample.longitude_deg,
        )
        statuses["ground_track_deg"] = FieldStatus.VALID
        self._baseline = sample
        return replace(output, ground_track_deg=track, field_status=statuses)


def _distance_m(
    latitude_a_deg: float | None,
    longitude_a_deg: float | None,
    latitude_b_deg: float | None,
    longitude_b_deg: float | None,
) -> float:
    assert latitude_a_deg is not None and longitude_a_deg is not None
    assert latitude_b_deg is not None and longitude_b_deg is not None
    lat_a = math.radians(latitude_a_deg)
    lat_b = math.radians(latitude_b_deg)
    delta_lat = lat_b - lat_a
    delta_lon = math.radians(longitude_b_deg - longitude_a_deg)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, haversine)))


def _initial_bearing_deg(
    latitude_a_deg: float | None,
    longitude_a_deg: float | None,
    latitude_b_deg: float | None,
    longitude_b_deg: float | None,
) -> float:
    assert latitude_a_deg is not None and longitude_a_deg is not None
    assert latitude_b_deg is not None and longitude_b_deg is not None
    lat_a = math.radians(latitude_a_deg)
    lat_b = math.radians(latitude_b_deg)
    delta_lon = math.radians(longitude_b_deg - longitude_a_deg)
    y = math.sin(delta_lon) * math.cos(lat_b)
    x = math.cos(lat_a) * math.sin(lat_b) - math.sin(lat_a) * math.cos(lat_b) * math.cos(delta_lon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


__all__ = ["GroundTrackDeriver"]
