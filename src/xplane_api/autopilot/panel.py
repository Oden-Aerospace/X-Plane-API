"""Autopilot mode-controller panel: button presses, selectors, and read-back.

The panel mimics how a pilot uses a modern small-aircraft AFCS mode
controller: momentary button presses request mode changes, selector knobs set
target values, and the only feedback is the annunciated state read back from
the simulator. Input methods therefore never return a resulting mode — a
press is a request, not a guaranteed transition. Confirm every input by
reading state, directly or through :meth:`AutopilotPanel.wait_for`.

Button-to-command and selector-to-dataref mappings come from the installed
X-Plane 12.4.3 catalogs. Which buttons the included C172 G1000 AFCS actually
supports is established by live verification; see the agent API reference.
This is simulation tooling, not certified avionics.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Callable

from xplane_api.models.autopilot import AfcsButton, AfcsSelector, AutopilotState
from xplane_api.sources.autopilot import AutopilotStateAdapter
from xplane_api.sources.control_client import XPlaneControlClient


# Momentary button -> X-Plane command path (XP12.4.3 Commands.txt catalog).
BUTTON_COMMANDS: dict[AfcsButton, str] = {
    AfcsButton.AP: "sim/autopilot/servos_toggle",
    # The yoke AP DISC / TRIM INT button: drops the servos deterministically
    # and leaves the flight director on (verified live, 2026-11-01;
    # servos_off_any had no effect on the C172 G1000).
    AfcsButton.AP_DISC: "sim/autopilot/ap_disc_trim_interrupt",
    AfcsButton.FD: "sim/autopilot/fdir_toggle",
    AfcsButton.YD: "sim/systems/yaw_damper_toggle",
    AfcsButton.HDG: "sim/autopilot/heading",
    AfcsButton.ROL: "sim/autopilot/wing_leveler",
    AfcsButton.NAV: "sim/autopilot/NAV",
    AfcsButton.APR: "sim/autopilot/approach",
    AfcsButton.BC: "sim/autopilot/back_course",
    AfcsButton.ALT: "sim/autopilot/altitude_hold",
    AfcsButton.VS: "sim/autopilot/vertical_speed",
    AfcsButton.FLC: "sim/autopilot/level_change",
    AfcsButton.VNV: "sim/autopilot/vnav",
    AfcsButton.GA: "sim/autopilot/take_off_go_around",
}

# Selector -> (writable dataref, minimum, maximum, maximum is exclusive).
# Ranges are explicit API transmission guards, not aircraft performance
# claims; out-of-range values are rejected, never clamped.
SELECTOR_RANGES: dict[AfcsSelector, tuple[str, float, float, bool]] = {
    AfcsSelector.HEADING: (
        "sim/cockpit2/autopilot/heading_dial_deg_mag_pilot",
        0.0,
        360.0,
        True,
    ),
    AfcsSelector.ALTITUDE: (
        "sim/cockpit2/autopilot/altitude_dial_ft",
        -1000.0,
        60000.0,
        False,
    ),
    AfcsSelector.VERTICAL_SPEED: (
        "sim/cockpit2/autopilot/vvi_dial_fpm",
        -8000.0,
        8000.0,
        False,
    ),
    AfcsSelector.AIRSPEED: (
        "sim/cockpit2/autopilot/airspeed_dial_kts",
        0.0,
        400.0,
        False,
    ),
    AfcsSelector.COURSE: (
        "sim/cockpit2/radios/actuators/hsi_obs_deg_mag_pilot",
        0.0,
        360.0,
        True,
    ),
}


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    """Outcome of waiting for an annunciated-state condition.

    ``satisfied`` reports whether the condition held before the timeout.
    ``state`` is the final observed snapshot either way — on timeout it shows
    what the autopilot actually annunciated instead of what was requested.
    """

    satisfied: bool
    state: AutopilotState


class AutopilotPanel:
    """Operate the simulated autopilot like its mode-controller panel.

    Composes a read adapter (annunciated state) with a control client
    (inputs). Constructing the control client is the explicit opt-in to
    sending control traffic; the panel computes no autoflight logic.
    """

    def __init__(
        self,
        adapter: AutopilotStateAdapter,
        control: XPlaneControlClient,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.adapter = adapter
        self.control = control
        self._clock = clock
        self._sleep = sleep

    # -- Momentary buttons (requests; confirm via read-back) ---------------

    def press_button(self, button: AfcsButton | str) -> None:
        """Press one mode-controller button once.

        Accepts an :class:`AfcsButton` or its string value. Raises
        ``ValueError`` for unknown buttons. Successful return means only
        that the request was transmitted.
        """
        try:
            resolved = AfcsButton(button)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown AFCS button: {button!r}") from exc
        self.control.send_command(BUTTON_COMMANDS[resolved])

    def press_ap(self) -> None:
        """Toggle autopilot servo engagement (AP button)."""
        self.press_button(AfcsButton.AP)

    def press_ap_disconnect(self) -> None:
        """Disconnect the autopilot servos (dedicated AP DISC input)."""
        self.press_button(AfcsButton.AP_DISC)

    def press_fd(self) -> None:
        """Toggle the flight director (FD button)."""
        self.press_button(AfcsButton.FD)

    def press_yd(self) -> None:
        """Toggle the yaw damper (YD button)."""
        self.press_button(AfcsButton.YD)

    def press_hdg(self) -> None:
        """Select heading mode (HDG button)."""
        self.press_button(AfcsButton.HDG)

    def press_rol(self) -> None:
        """Select wings-level roll mode (ROL button)."""
        self.press_button(AfcsButton.ROL)

    def press_nav(self) -> None:
        """Arm or select navigation tracking (NAV button)."""
        self.press_button(AfcsButton.NAV)

    def press_apr(self) -> None:
        """Arm or select approach mode (APR button)."""
        self.press_button(AfcsButton.APR)

    def press_bc(self) -> None:
        """Arm or select localizer back-course mode (BC button)."""
        self.press_button(AfcsButton.BC)

    def press_alt(self) -> None:
        """Select altitude hold (ALT button)."""
        self.press_button(AfcsButton.ALT)

    def press_vs(self) -> None:
        """Select vertical-speed mode (VS button)."""
        self.press_button(AfcsButton.VS)

    def press_flc(self) -> None:
        """Select flight-level-change speed mode (FLC button)."""
        self.press_button(AfcsButton.FLC)

    def press_vnv(self) -> None:
        """Arm or select VNAV path mode (VNV button)."""
        self.press_button(AfcsButton.VNV)

    def press_ga(self) -> None:
        """Select takeoff/go-around mode (GA/TOGA input)."""
        self.press_button(AfcsButton.GA)

    # -- Target selectors (value inputs with explicit ranges) --------------

    def set_selector(self, selector: AfcsSelector | str, value: float) -> None:
        """Turn one target selector to ``value``.

        Accepts an :class:`AfcsSelector` or its string value. Validates the
        documented range before transmission and raises ``ValueError``
        without sending anything for out-of-range or non-finite values;
        values are never clamped.
        """
        try:
            resolved = AfcsSelector(selector)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown AFCS selector: {selector!r}") from exc

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{resolved.value} value must be a real number")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"{resolved.value} value must be finite")

        path, minimum, maximum, max_exclusive = SELECTOR_RANGES[resolved]
        below = numeric < minimum
        above = numeric >= maximum if max_exclusive else numeric > maximum
        if below or above:
            bound = "<" if max_exclusive else "<="
            raise ValueError(
                f"{resolved.value} value {numeric} outside "
                f"{minimum} <= value {bound} {maximum}"
            )
        self.control.write_dataref(path, numeric)

    def set_heading_bug_deg(self, heading_deg_mag: float) -> None:
        """Set the heading bug in magnetic degrees, 0 <= value < 360."""
        self.set_selector(AfcsSelector.HEADING, heading_deg_mag)

    def set_altitude_selector_ft(self, altitude_ft: float) -> None:
        """Set the altitude selector in feet, -1000 <= value <= 60000."""
        self.set_selector(AfcsSelector.ALTITUDE, altitude_ft)

    def set_vertical_speed_target_fpm(self, vertical_speed_fpm: float) -> None:
        """Set the vertical-speed target in ft/min, -8000 <= value <= 8000."""
        self.set_selector(AfcsSelector.VERTICAL_SPEED, vertical_speed_fpm)

    def set_airspeed_target_kt(self, airspeed_kt: float) -> None:
        """Set the airspeed target in knots, 0 <= value <= 400."""
        self.set_selector(AfcsSelector.AIRSPEED, airspeed_kt)

    def set_course_deg(self, course_deg_mag: float) -> None:
        """Set the selected course in magnetic degrees, 0 <= value < 360."""
        self.set_selector(AfcsSelector.COURSE, course_deg_mag)

    # -- Annunciated state (the only truth) ---------------------------------

    def read_state(
        self,
        *,
        timeout_seconds: float,
        stale_after_seconds: float,
    ) -> AutopilotState:
        """Read the current annunciated autopilot state snapshot."""
        return self.adapter.read_state(
            timeout_seconds=timeout_seconds,
            stale_after_seconds=stale_after_seconds,
        )

    def wait_for(
        self,
        predicate: Callable[[AutopilotState], bool],
        *,
        timeout_seconds: float,
        poll_interval_seconds: float,
        stale_after_seconds: float,
    ) -> ConfirmationResult:
        """Poll annunciated state until ``predicate`` holds or time runs out.

        Returns a :class:`ConfirmationResult` with the final observed state
        either way; a timeout is reported, never raised, so callers can see
        what the autopilot actually annunciated. This helper only reads — it
        sends no input.
        """
        for name, value in (
            ("timeout_seconds", timeout_seconds),
            ("poll_interval_seconds", poll_interval_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a finite non-negative number")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")

        deadline = self._clock() + timeout_seconds
        while True:
            state = self.read_state(
                timeout_seconds=min(poll_interval_seconds, timeout_seconds),
                stale_after_seconds=stale_after_seconds,
            )
            if predicate(state):
                return ConfirmationResult(satisfied=True, state=state)
            if self._clock() >= deadline:
                return ConfirmationResult(satisfied=False, state=state)
            self._sleep(poll_interval_seconds)

    def close(self) -> None:
        """Close the read adapter and the control client."""
        try:
            self.adapter.close()
        finally:
            self.control.close()

    def __enter__(self) -> "AutopilotPanel":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
