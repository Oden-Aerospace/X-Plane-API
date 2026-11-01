"""Read-only X-Plane 12 RREF source mapped to normalized flight state."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import time
from typing import Callable

from xplane_api.models.state import FieldStatus, FlightState, StateSource
from xplane_api.sources.rref_client import RrefClient, TimedDatarefValue


# Candidate mappings verified by read-only RREF against XP12.4.3 and the
# included C172 G1000 on Intel macOS. The fields keep units in their names.
NUMERIC_DATAREFS: dict[str, str] = {
    "latitude_deg": "sim/flightmodel/position/latitude",
    "longitude_deg": "sim/flightmodel/position/longitude",
    "elevation_msl_m": "sim/flightmodel/position/elevation",
    "barometric_altitude_ft": "sim/cockpit2/gauges/indicators/altitude_ft_pilot",
    "indicated_airspeed_kt": "sim/cockpit2/gauges/indicators/airspeed_kts_pilot",
    "ground_speed_mps": "sim/flightmodel/position/groundspeed",
    "vertical_speed_fpm": "sim/cockpit2/gauges/indicators/vvi_fpm_pilot",
    "true_heading_deg": "sim/flightmodel/position/true_psi",
    "magnetic_heading_deg": "sim/cockpit/gyros/psi_ind_ahars_pilot_degm",
    "flight_path_heading_deg": "sim/flightmodel/position/hpath",
    "flight_path_heading_deg": "sim/flightmodel/position/hpath",
    "pitch_deg": "sim/cockpit2/gauges/indicators/pitch_AHARS_deg_pilot",
    "roll_deg": "sim/cockpit2/gauges/indicators/roll_AHARS_deg_pilot",
    "turn_indicator_deflection_deg": (
        "sim/cockpit2/gauges/indicators/turn_rate_heading_deg_pilot"
    ),
    "slip_indicator_deflection_deg": "sim/cockpit2/gauges/indicators/slip_deg",
    "barometer_setting_inhg": "sim/cockpit/misc/barometer_setting",
}

_BOOLEAN_DATAREFS: dict[str, str] = {
    "on_ground": "sim/flightmodel/failures/onground_any",
}

_STRING_ARRAY_DATAREFS: dict[str, str] = {
    "aircraft_type_code": "sim/aircraft/view/acf_ICAO",
    "tail_number": "sim/aircraft/view/acf_tailnum",
    "flight_id": "sim/cockpit2/radios/actuators/flight_id",
}
_STRING_ARRAY_LENGTHS = {
    "aircraft_type_code": 40,
    "tail_number": 40,
    "flight_id": 8,
}


class XPlaneStateAdapter:
    """Subscribe to configured X-Plane datarefs and build FlightState snapshots.

    The source frequency and stale threshold are explicit arguments because
    acceptable update/staleness policies remain product decisions. The live
    X-Plane aircraft code and tail number are surfaced as metadata only; neither
    is treated as the ADS-B callsign or assigned 24-bit Mode S address.
    """

    def __init__(
        self,
        client: RrefClient,
        *,
        frequency_hz: int,
        clock: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.client = client
        self._clock = clock
        self._utc_now = utc_now
        self._sequence = 0
        self._numeric_paths: dict[str, str] = {}
        self._boolean_paths: dict[str, str] = {}
        self._string_paths: dict[str, tuple[str, ...]] = {}
        try:
            for field_name, path in NUMERIC_DATAREFS.items():
                client.subscribe(path, frequency_hz)
                self._numeric_paths[field_name] = path
            for field_name, path in _BOOLEAN_DATAREFS.items():
                client.subscribe(path, frequency_hz)
                self._boolean_paths[field_name] = path
            for field_name, base_path in _STRING_ARRAY_DATAREFS.items():
                paths = tuple(
                    f"{base_path}[{offset}]"
                    for offset in range(_STRING_ARRAY_LENGTHS[field_name])
                )
                for path in paths:
                    client.subscribe(path, frequency_hz)
                self._string_paths[field_name] = paths
        except Exception:
            client.close()
            raise

    def read_state(
        self,
        *,
        timeout_seconds: float,
        stale_after_seconds: float,
    ) -> FlightState:
        """Read packets up to the timeout and return the latest state snapshot.

        ``timeout_seconds`` controls this call's wait for packets. A value of
        zero returns immediately from the client cache. ``stale_after_seconds``
        is applied per field using local monotonic receive times.
        """
        for name, value in (
            ("timeout_seconds", timeout_seconds),
            ("stale_after_seconds", stale_after_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a finite non-negative number")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")

        deadline = self._clock() + timeout_seconds
        while True:
            remaining = max(0.0, deadline - self._clock())
            self.client.poll(remaining)
            if remaining <= 0:
                break
            if self._clock() >= deadline:
                break

        latest = self.client.latest_values
        values: dict[str, object] = {}
        statuses: dict[str, FieldStatus] = {}
        now = self._clock()

        for field_name, path in self._numeric_paths.items():
            sample = latest.get(path)
            value, status = self._get_value(sample, now, stale_after_seconds)
            values[field_name] = value
            statuses[field_name] = status

        for field_name, path in self._boolean_paths.items():
            sample = latest.get(path)
            raw_value, status = self._get_value(sample, now, stale_after_seconds)
            values[field_name] = None if raw_value is None else raw_value >= 0.5
            statuses[field_name] = status

        for field_name, paths in self._string_paths.items():
            value, status = self._decode_string_array(
                paths, latest, now, stale_after_seconds
            )
            values[field_name] = value
            statuses[field_name] = status

        self._sequence += 1
        return FlightState(
            timestamp_utc=self._utc_now(),
            source=StateSource.LIVE_XPLANE,
            sequence=self._sequence,
            field_status=statuses,
            **values,
        )

    def close(self) -> None:
        """Close the underlying client, which unsubscribes its active paths."""
        self.client.close()

    def resubscribe(self) -> int:
        """Restore active dataref subscriptions after the sim is reachable."""
        return self.client.resubscribe_all()

    def __enter__(self) -> XPlaneStateAdapter:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @staticmethod
    def _get_value(
        sample: TimedDatarefValue | None,
        now: float,
        stale_after_seconds: float,
    ) -> tuple[float | None, FieldStatus]:
        if sample is None:
            return None, FieldStatus.UNAVAILABLE
        if not math.isfinite(sample.value):
            return None, FieldStatus.INVALID
        if now - sample.received_at_monotonic > stale_after_seconds:
            return sample.value, FieldStatus.STALE
        return sample.value, FieldStatus.VALID

    @classmethod
    def _decode_string_array(
        cls,
        paths: tuple[str, ...],
        latest: dict[str, TimedDatarefValue],
        now: float,
        stale_after_seconds: float,
    ) -> tuple[str | None, FieldStatus]:
        characters: list[str] = []
        for path in paths:
            sample = latest.get(path)
            if sample is None:
                if not characters:
                    return None, FieldStatus.UNAVAILABLE
                return "".join(characters), FieldStatus.INVALID
            if now - sample.received_at_monotonic > stale_after_seconds:
                return "".join(characters) or None, FieldStatus.STALE
            if not math.isfinite(sample.value):
                return "".join(characters) or None, FieldStatus.INVALID
            codepoint = int(round(sample.value))
            if codepoint == 0:
                return "".join(characters), FieldStatus.VALID
            if codepoint < 0 or codepoint > 255:
                return "".join(characters) or None, FieldStatus.INVALID
            characters.append(chr(codepoint))
        return "".join(characters), FieldStatus.VALID


__all__ = ["NUMERIC_DATAREFS", "XPlaneStateAdapter"]
