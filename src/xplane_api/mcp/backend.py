"""Dependency-free tool backend for the autopilot MCP server.

Implements the four tool behaviors over an :class:`AutopilotPanel` as plain
methods with JSON-compatible inputs and outputs, so they are unit-testable
without the MCP SDK. All validation happens here and raises ``ValueError``
before anything is transmitted; the MCP layer converts those into structured
tool errors.

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


def _validate_non_negative(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite non-negative number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
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

    def __init__(self, panel: AutopilotPanel) -> None:
        self.panel = panel

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
