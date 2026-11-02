"""Dependency-free tool backend for the autopilot and flight-plan MCP server.

Implements tool behaviors over an :class:`AutopilotPanel` and
:class:`FlightPlanClient` as plain methods with JSON-compatible inputs and
outputs, so they are unit-testable without the MCP SDK. Validation happens
before anything is transmitted; the MCP layer converts errors into
structured tool errors.

The contract mirrors the autopilot API: inputs are requests over
unacknowledged UDP, and the only truth is the annunciated state read back
from the simulator.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from xplane_api.autopilot.panel import AutopilotPanel
from xplane_api.models.autopilot import (
    AutopilotState,
    LateralMode,
    VerticalMode,
)
from xplane_api.sources.flight_plan import FlightPlanClient


_BOOL_CONDITION_FIELDS = (
    "ap_engaged",
    "flight_director_on",
    "yaw_damper_on",
)
_LATERAL_CONDITION_FIELDS = ("lateral_mode_active", "lateral_mode_armed")
_VERTICAL_CONDITION_FIELDS = ("vertical_mode_active", "vertical_mode_armed")
_NUMERIC_CONDITION_FIELDS = (
    "selected_heading_deg_mag",
    "selected_altitude_ft",
    "selected_vertical_speed_fpm",
    "selected_airspeed_kt",
    "selected_course_deg_mag",
)
CONDITION_FIELDS = (
    _BOOL_CONDITION_FIELDS
    + _LATERAL_CONDITION_FIELDS
    + _VERTICAL_CONDITION_FIELDS
    + _NUMERIC_CONDITION_FIELDS
)
_NAVIGATION_MODES = frozenset({LateralMode.NAV, LateralMode.GPSS})


def _validate_non_negative(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite non-negative number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return numeric


def _validate_positive(name: str, value: Any) -> float:
    numeric = _validate_non_negative(name, value)
    if numeric == 0:
        raise ValueError(f"{name} must be finite and positive")
    return numeric


def parse_condition(
    expected: Mapping[str, Any],
    numeric_tolerance: float | None,
) -> dict[str, Any]:
    """Validate a wait_for_state condition, returning normalized values.

    Enum fields must be valid mode names, boolean fields booleans, and
    numeric fields finite numbers. ``numeric_tolerance`` is required when
    any numeric expectation is present and must be a finite non-negative
    number. Unknown keys and empty conditions are rejected.
    """
    if not isinstance(expected, Mapping) or not expected:
        raise ValueError(
            "expected must be a non-empty object of state expectations"
        )
    unknown = set(expected) - set(CONDITION_FIELDS)
    if unknown:
        raise ValueError(
            f"unknown expectation fields: {sorted(unknown)}; "
            f"supported: {list(CONDITION_FIELDS)}"
        )

    normalized: dict[str, Any] = {}
    has_numeric = False
    for name, value in expected.items():
        if name in _BOOL_CONDITION_FIELDS:
            if not isinstance(value, bool):
                raise ValueError(f"{name} expectation must be a boolean")
            normalized[name] = value
        elif name in _LATERAL_CONDITION_FIELDS:
            try:
                normalized[name] = LateralMode(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{name} expectation must be one of "
                    f"{[m.value for m in LateralMode]}"
                ) from exc
        elif name in _VERTICAL_CONDITION_FIELDS:
            try:
                normalized[name] = VerticalMode(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{name} expectation must be one of "
                    f"{[m.value for m in VerticalMode]}"
                ) from exc
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} expectation must be a number")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError(f"{name} expectation must be finite")
            normalized[name] = numeric
            has_numeric = True

    if has_numeric:
        if numeric_tolerance is None:
            raise ValueError(
                "numeric_tolerance is required when numeric expectations "
                "are present"
            )
        tolerance = _validate_non_negative("numeric_tolerance", numeric_tolerance)
        normalized["__numeric_tolerance__"] = tolerance
    elif numeric_tolerance is not None:
        _validate_non_negative("numeric_tolerance", numeric_tolerance)

    return normalized


def condition_holds(state: AutopilotState, normalized: Mapping[str, Any]) -> bool:
    """Evaluate a normalized condition against one state (AND semantics).

    Numeric comparisons require the field to be present; enum and boolean
    comparisons are identity checks. Fields that are ``None`` in the state
    satisfy nothing.
    """
    tolerance = normalized.get("__numeric_tolerance__", 0.0)
    for name, expected_value in normalized.items():
        if name == "__numeric_tolerance__":
            continue
        actual = getattr(state, name)
        if actual is None:
            return False
        if name in _NUMERIC_CONDITION_FIELDS:
            if abs(actual - expected_value) > tolerance:
                return False
        elif actual != expected_value:
            return False
    return True


class ControlDisabled:
    """Stand-in for the control client when control is not enabled.

    Not an ``XPlaneControlClient``: it owns no socket and transmits nothing.
    Any attempt to send raises ``RuntimeError``, as defense in depth behind
    the server's tool-registration gating.
    """

    def send_command(self, command_path: str) -> None:
        raise RuntimeError("autopilot control is not enabled on this server")

    def write_dataref(self, dataref_path: str, value: float) -> None:
        raise RuntimeError("autopilot control is not enabled on this server")

    def close(self) -> None:
        return None


class AutopilotToolBackend:
    """Tool behaviors over one panel, with validation-first semantics."""

    def __init__(
        self,
        panel: AutopilotPanel,
        *,
        flight_plan_client: FlightPlanClient | None = None,
    ) -> None:
        self.panel = panel
        self.flight_plan_client = (
            flight_plan_client
            if flight_plan_client is not None
            else FlightPlanClient()
        )

    def read_flight_plan(self) -> dict[str, Any]:
        """Read the pilot-side active plan through the shared Web API client."""
        return self.flight_plan_client.read_plan().to_json_dict()

    def direct_to_waypoint(self, identifier: str) -> dict[str, Any]:
        """Request a direct-to and require simulator plan read-back."""
        return self.flight_plan_client.direct_to(identifier)

    def direct_to_and_activate(
        self,
        identifier: str,
        *,
        timeout_seconds: float,
        poll_interval_seconds: float,
        stale_after_seconds: float,
    ) -> dict[str, Any]:
        """Confirm a direct-to, then engage AP navigation and verify read-back."""
        timeout = _validate_positive("timeout_seconds", timeout_seconds)
        poll_interval = _validate_positive(
            "poll_interval_seconds", poll_interval_seconds
        )
        stale_after = _validate_non_negative(
            "stale_after_seconds", stale_after_seconds
        )

        route = self.flight_plan_client.direct_to(
            identifier,
            timeout_seconds=timeout,
            poll_interval_seconds=poll_interval,
        )
        ap_button_transmitted = False
        nav_button_transmitted = False

        def partial_failure(
            reason: str, state: AutopilotState | None
        ) -> dict[str, Any]:
            return self._direct_to_activation_result(
                status="partial_failure",
                route=route,
                state=state,
                ap_button_transmitted=ap_button_transmitted,
                nav_button_transmitted=nav_button_transmitted,
                reason=reason,
            )

        try:
            state = self.panel.read_state(
                timeout_seconds=poll_interval,
                stale_after_seconds=stale_after,
            )
        except (OSError, RuntimeError) as exc:
            return partial_failure(f"could not read autopilot state: {exc}", None)

        if not self._navigation_state_is_usable(state):
            return partial_failure(
                "AP engagement or lateral mode state is unavailable or stale",
                state,
            )

        if not state.ap_engaged:
            try:
                self.panel.press_button("AP")
                ap_button_transmitted = True
                ap_result = self.panel.wait_for(
                    lambda observed: (
                        observed.is_usable("ap_engaged")
                        and observed.ap_engaged is True
                    ),
                    timeout_seconds=timeout,
                    poll_interval_seconds=poll_interval,
                    stale_after_seconds=stale_after,
                )
            except (OSError, RuntimeError) as exc:
                return partial_failure(
                    f"could not confirm AP engagement: {exc}", state
                )
            state = ap_result.state
            if not ap_result.satisfied:
                return partial_failure("AP engagement was not confirmed", state)

        if not self._navigation_state_is_usable(state):
            return partial_failure(
                "AP engagement was confirmed, but lateral mode state is "
                "unavailable or stale",
                state,
            )

        if state.lateral_mode_active not in _NAVIGATION_MODES:
            if state.lateral_mode_armed not in _NAVIGATION_MODES:
                try:
                    self.panel.press_button("NAV")
                    nav_button_transmitted = True
                except (OSError, RuntimeError) as exc:
                    return partial_failure(
                        f"could not transmit NAV selection: {exc}", state
                    )

            try:
                navigation_result = self.panel.wait_for(
                    lambda observed: (
                        observed.is_usable("ap_engaged")
                        and observed.ap_engaged is True
                        and observed.is_usable("lateral_mode_active")
                        and observed.lateral_mode_active in _NAVIGATION_MODES
                    ),
                    timeout_seconds=timeout,
                    poll_interval_seconds=poll_interval,
                    stale_after_seconds=stale_after,
                )
            except (OSError, RuntimeError) as exc:
                return partial_failure(
                    f"could not confirm NAV/GPSS activation: {exc}", state
                )
            state = navigation_result.state
            if not navigation_result.satisfied:
                return partial_failure(
                    "NAV or GPSS did not become active before the timeout",
                    state,
                )

        if not self._navigation_state_is_usable(state) or not state.ap_engaged:
            return partial_failure(
                "final AP or lateral mode state is unavailable or not engaged",
                state,
            )
        if state.lateral_mode_active not in _NAVIGATION_MODES:
            return partial_failure(
                "final active lateral mode is neither NAV nor GPSS", state
            )

        return self._direct_to_activation_result(
            status="confirmed",
            route=route,
            state=state,
            ap_button_transmitted=ap_button_transmitted,
            nav_button_transmitted=nav_button_transmitted,
        )

    @staticmethod
    def _navigation_state_is_usable(state: AutopilotState) -> bool:
        return all(
            state.is_usable(field_name)
            for field_name in (
                "ap_engaged",
                "lateral_mode_active",
                "lateral_mode_armed",
            )
        )

    @staticmethod
    def _direct_to_activation_result(
        *,
        status: str,
        route: dict[str, Any],
        state: AutopilotState | None,
        ap_button_transmitted: bool,
        nav_button_transmitted: bool,
        reason: str | None = None,
    ) -> dict[str, Any]:
        activation: dict[str, Any] = {
            "status": "confirmed" if status == "confirmed" else "failed",
            "ap_button_transmitted": ap_button_transmitted,
            "nav_button_transmitted": nav_button_transmitted,
        }
        if reason is not None:
            activation["reason"] = reason
        return {
            "status": status,
            "route": route,
            "activation": activation,
            "state": None if state is None else state.to_json_dict(),
        }

    def read_state(
        self, *, timeout_seconds: float, stale_after_seconds: float
    ) -> dict[str, Any]:
        """Read one annunciated autopilot state snapshot as JSON."""
        state = self.panel.read_state(
            timeout_seconds=timeout_seconds,
            stale_after_seconds=stale_after_seconds,
        )
        return state.to_json_dict()

    def press_button(self, button: str) -> dict[str, Any]:
        """Press one mode-controller button; returns transmission-only result."""
        self.panel.press_button(button)
        return {
            "transmitted": True,
            "button": button,
            "note": (
                "The press was transmitted, not confirmed. Inputs are "
                "requests; read state back (read_autopilot_state or "
                "wait_for_state) to learn the annunciated result."
            ),
        }

    def set_selector(self, selector: str, value: float) -> dict[str, Any]:
        """Set one target selector; returns transmission-only result."""
        self.panel.set_selector(selector, value)
        return {
            "transmitted": True,
            "selector": selector,
            "value": float(value),
            "note": (
                "The selector write was transmitted, not confirmed. Read "
                "state back to verify the simulator applied it."
            ),
        }

    def wait_for_state(
        self,
        *,
        expected: Mapping[str, Any],
        timeout_seconds: float,
        poll_interval_seconds: float,
        stale_after_seconds: float,
        numeric_tolerance: float | None = None,
    ) -> dict[str, Any]:
        """Poll until all expectations hold or the timeout elapses.

        Returns ``satisfied`` plus the final observed state either way; a
        timeout means the expected annunciation did not appear and the
        returned state shows what the autopilot actually annunciated.
        """
        normalized = parse_condition(expected, numeric_tolerance)
        result = self.panel.wait_for(
            lambda state: condition_holds(state, normalized),
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            stale_after_seconds=stale_after_seconds,
        )
        return {
            "satisfied": result.satisfied,
            "state": result.state.to_json_dict(),
        }
