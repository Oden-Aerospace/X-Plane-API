"""Read-only X-Plane 12 RREF source mapped to normalized autopilot state.

Dataref candidates were verified by read-only RREF against XP12.4.3 and the
included C172 G1000 on Intel macOS (2026-11-01 probe): the aircraft drives
the generic ``sim/cockpit2/autopilot/*`` family, whose mode-status integers
follow a ``0 = off, 1 = armed, 2 = active/captured`` convention. Raw status
values are retained on every snapshot; unmapped raw values are reported as
``UNKNOWN`` modes, never guessed.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
import time
from typing import Callable, Mapping

from xplane_api.models.autopilot import AutopilotState, LateralMode, VerticalMode
from xplane_api.models.state import FieldStatus
from xplane_api.sources.rref_client import RrefClient, TimedDatarefValue


_BOOLEAN_DATAREFS: dict[str, str] = {
    # servos_on is the servo-engagement signal; autopilot_on stays 1 while
    # only the flight director is flying (verified live, 2026-11-01).
    "ap_engaged": "sim/cockpit2/autopilot/servos_on",
    "yaw_damper_on": "sim/cockpit2/switches/yaw_damper_on",
    "ap_disconnect_annunciated": "sim/cockpit2/autopilot/autopilot_disconnect",
}

_NUMERIC_DATAREFS: dict[str, str] = {
    "selected_heading_deg_mag": "sim/cockpit2/autopilot/heading_dial_deg_mag_pilot",
    "selected_altitude_ft": "sim/cockpit2/autopilot/altitude_dial_ft",
    "selected_vertical_speed_fpm": "sim/cockpit2/autopilot/vvi_dial_fpm",
    "selected_airspeed_kt": "sim/cockpit2/autopilot/airspeed_dial_kts",
    "selected_course_deg_mag": "sim/cockpit2/radios/actuators/hsi_obs_deg_mag_pilot",
    "fd_pitch_command_deg": "sim/cockpit2/autopilot/flight_director_pitch_deg",
    "fd_roll_command_deg": "sim/cockpit2/autopilot/flight_director_roll_deg",
}

# 0 = off, 1 = flight director only, 2 = autopilot with servos.
_FLIGHT_DIRECTOR_MODE = "sim/cockpit2/autopilot/flight_director_mode"

# Mode-status datarefs: 0 = off, 1 = armed, 2 = active/captured.
_LATERAL_STATUS_MODES: dict[str, LateralMode] = {
    "sim/cockpit2/autopilot/roll_status": LateralMode.ROL,
    "sim/cockpit2/autopilot/heading_status": LateralMode.HDG,
    "sim/cockpit2/autopilot/nav_status": LateralMode.NAV,
    "sim/cockpit2/autopilot/gpss_status": LateralMode.GPSS,
    "sim/cockpit2/autopilot/approach_status": LateralMode.APR,
    "sim/cockpit2/autopilot/backcourse_status": LateralMode.BC,
    "sim/cockpit2/autopilot/TOGA_lateral_status": LateralMode.GA,
}

_VERTICAL_STATUS_MODES: dict[str, VerticalMode] = {
    "sim/cockpit2/autopilot/pitch_status": VerticalMode.PIT,
    "sim/cockpit2/autopilot/vvi_status": VerticalMode.VS,
    "sim/cockpit2/autopilot/speed_status": VerticalMode.FLC,
    "sim/cockpit2/autopilot/altitude_hold_status": VerticalMode.ALT,
    "sim/cockpit2/autopilot/glideslope_status": VerticalMode.GS,
    "sim/cockpit2/autopilot/vnav_status": VerticalMode.VNV,
    "sim/cockpit2/autopilot/TOGA_status": VerticalMode.GA,
}

# Boolean arm flags that annunciate armed vertical capture modes.
_VERTICAL_ARMED_FLAGS: dict[str, VerticalMode] = {
    "sim/cockpit2/autopilot/alts_armed": VerticalMode.ALTS,
    "sim/cockpit2/autopilot/altv_armed": VerticalMode.ALTV,
}

# When several vertical modes are simultaneously armed (a normal AFCS
# condition, e.g. ALTS with GS armed), the single armed field reports the
# highest-precedence entry below; raw values always retain the full picture.
_VERTICAL_ARMED_PRECEDENCE: tuple[VerticalMode, ...] = (
    VerticalMode.GS,
    VerticalMode.VNV,
    VerticalMode.ALTV,
    VerticalMode.ALTS,
    VerticalMode.ALT,
    VerticalMode.VS,
    VerticalMode.FLC,
    VerticalMode.PIT,
    VerticalMode.GA,
)

_LATERAL_ARMED_PRECEDENCE: tuple[LateralMode, ...] = (
    LateralMode.APR,
    LateralMode.BC,
    LateralMode.NAV,
    LateralMode.GPSS,
    LateralMode.HDG,
    LateralMode.ROL,
    LateralMode.GA,
)


class AutopilotStateAdapter:
    """Subscribe to autopilot datarefs and build AutopilotState snapshots.

    The subscription frequency and staleness threshold are explicit arguments
    because acceptable update/staleness policies remain product decisions.
    Reading autopilot state transmits only RREF subscription requests; this
    adapter cannot send command or dataref-write traffic.
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
        self._status_paths: tuple[str, ...] = (
            _FLIGHT_DIRECTOR_MODE,
            *(_LATERAL_STATUS_MODES.keys()),
            *(_VERTICAL_STATUS_MODES.keys()),
            *(_VERTICAL_ARMED_FLAGS.keys()),
        )
        try:
            for path in self._status_paths:
                client.subscribe(path, frequency_hz)
            for path in _BOOLEAN_DATAREFS.values():
                client.subscribe(path, frequency_hz)
            for path in _NUMERIC_DATAREFS.values():
                client.subscribe(path, frequency_hz)
        except Exception:
            client.close()
            raise

    def read_state(
        self,
        *,
        timeout_seconds: float,
        stale_after_seconds: float,
    ) -> AutopilotState:
        """Read packets up to the timeout and return the latest snapshot.

        ``timeout_seconds`` controls this call's wait for packets; zero
        returns immediately from the client cache. ``stale_after_seconds``
        is applied per field using local monotonic receive times. Mode
        fields become stale or unavailable when any contributing status
        dataref is; they never silently retain a previous annunciation.
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
        now = self._clock()
        values: dict[str, object] = {}
        statuses: dict[str, FieldStatus] = {}

        for field_name, path in _BOOLEAN_DATAREFS.items():
            raw, status = self._get_value(latest.get(path), now, stale_after_seconds)
            values[field_name] = None if raw is None else raw >= 0.5
            statuses[field_name] = status

        fd_raw, fd_status = self._get_value(
            latest.get(_FLIGHT_DIRECTOR_MODE), now, stale_after_seconds
        )
        values["flight_director_on"] = None if fd_raw is None else fd_raw >= 0.5
        statuses["flight_director_on"] = fd_status

        for field_name, path in _NUMERIC_DATAREFS.items():
            raw, status = self._get_value(latest.get(path), now, stale_after_seconds)
            values[field_name] = raw
            statuses[field_name] = status

        raw_status_values: dict[str, float] = {}
        for path in self._status_paths:
            sample = latest.get(path)
            if sample is not None and math.isfinite(sample.value):
                raw_status_values[path] = sample.value

        lateral_active, lateral_armed, lateral_status = self._derive_modes(
            latest,
            now,
            stale_after_seconds,
            _LATERAL_STATUS_MODES,
            {},
            _LATERAL_ARMED_PRECEDENCE,
            LateralMode.NONE,
            LateralMode.UNKNOWN,
        )
        vertical_active, vertical_armed, vertical_status = self._derive_modes(
            latest,
            now,
            stale_after_seconds,
            _VERTICAL_STATUS_MODES,
            _VERTICAL_ARMED_FLAGS,
            _VERTICAL_ARMED_PRECEDENCE,
            VerticalMode.NONE,
            VerticalMode.UNKNOWN,
        )
        values["lateral_mode_active"] = lateral_active
        values["lateral_mode_armed"] = lateral_armed
        statuses["lateral_mode_active"] = lateral_status
        statuses["lateral_mode_armed"] = lateral_status
        values["vertical_mode_active"] = vertical_active
        values["vertical_mode_armed"] = vertical_armed
        statuses["vertical_mode_active"] = vertical_status
        statuses["vertical_mode_armed"] = vertical_status

        return AutopilotState(
            timestamp_utc=self._utc_now(),
            raw_status_values=raw_status_values,
            field_status=statuses,
            **values,  # type: ignore[arg-type]
        )

    def close(self) -> None:
        """Close the underlying client, which unsubscribes its active paths."""
        self.client.close()

    def resubscribe(self) -> int:
        """Restore active dataref subscriptions after the sim is reachable."""
        return self.client.resubscribe_all()

    def __enter__(self) -> "AutopilotStateAdapter":
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
    def _derive_modes(
        cls,
        latest: Mapping[str, TimedDatarefValue],
        now: float,
        stale_after_seconds: float,
        status_modes: Mapping[str, object],
        armed_flags: Mapping[str, object],
        armed_precedence: tuple,
        none_mode: object,
        unknown_mode: object,
    ) -> tuple[object, object, FieldStatus]:
        """Derive (active, armed, shared status) from one mode-status family.

        Any missing contributing dataref makes the modes unavailable; any
        stale one makes them stale. An out-of-vocabulary raw status (not
        0/1/2) or more than one simultaneously active mode reports UNKNOWN
        rather than guessing; raw values remain on the snapshot.
        """
        worst = FieldStatus.VALID
        active: list[object] = []
        armed: list[object] = []
        unknown_raw = False

        for path, mode in status_modes.items():
            raw, status = cls._get_value(latest.get(path), now, stale_after_seconds)
            if raw is None:
                return None, None, status
            if status is FieldStatus.STALE:
                worst = FieldStatus.STALE
            code = int(round(raw))
            if code == 2:
                active.append(mode)
            elif code == 1:
                armed.append(mode)
            elif code != 0:
                unknown_raw = True

        for path, mode in armed_flags.items():
            raw, status = cls._get_value(latest.get(path), now, stale_after_seconds)
            if raw is None:
                return None, None, status
            if status is FieldStatus.STALE:
                worst = FieldStatus.STALE
            if raw >= 0.5:
                armed.append(mode)

        if unknown_raw or len(active) > 1:
            active_mode = unknown_mode
        elif active:
            active_mode = active[0]
        else:
            active_mode = none_mode

        if unknown_raw:
            armed_mode = unknown_mode
        elif armed:
            armed_mode = next(
                (mode for mode in armed_precedence if mode in armed),
                unknown_mode,
            )
        else:
            armed_mode = none_mode

        return active_mode, armed_mode, worst
