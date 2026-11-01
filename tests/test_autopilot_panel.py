import math
import unittest
from datetime import datetime, timezone

from xplane_api.autopilot.panel import (
    BUTTON_COMMANDS,
    SELECTOR_RANGES,
    AutopilotPanel,
    ConfirmationResult,
)
from xplane_api.models.autopilot import (
    AfcsButton,
    AfcsSelector,
    AutopilotState,
    LateralMode,
)


def _state(**kwargs) -> AutopilotState:
    return AutopilotState(
        timestamp_utc=datetime(2026, 11, 1, tzinfo=timezone.utc), **kwargs
    )


class FakeAdapter:
    def __init__(self, states: list[AutopilotState]) -> None:
        self.states = list(states)
        self.read_calls: list[dict[str, float]] = []
        self.closed = False

    def read_state(self, *, timeout_seconds: float, stale_after_seconds: float):
        self.read_calls.append(
            {
                "timeout_seconds": timeout_seconds,
                "stale_after_seconds": stale_after_seconds,
            }
        )
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    def close(self) -> None:
        self.closed = True


class FakeControl:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.dataref_writes: list[tuple[str, float]] = []
        self.closed = False

    def send_command(self, command_path: str) -> None:
        self.commands.append(command_path)

    def write_dataref(self, dataref_path: str, value: float) -> None:
        self.dataref_writes.append((dataref_path, value))

    def close(self) -> None:
        self.closed = True


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _panel(
    states: list[AutopilotState] | None = None,
) -> tuple[AutopilotPanel, FakeAdapter, FakeControl, FakeClock]:
    adapter = FakeAdapter(states or [_state()])
    control = FakeControl()
    clock = FakeClock()
    panel = AutopilotPanel(adapter, control, clock=clock, sleep=clock.sleep)  # type: ignore[arg-type]
    return panel, adapter, control, clock


class ButtonTests(unittest.TestCase):
    def test_every_button_sends_exactly_its_mapped_command(self) -> None:
        panel, _, control, _ = _panel()
        method_by_button = {
            AfcsButton.AP: panel.press_ap,
            AfcsButton.AP_DISC: panel.press_ap_disconnect,
            AfcsButton.FD: panel.press_fd,
            AfcsButton.YD: panel.press_yd,
            AfcsButton.HDG: panel.press_hdg,
            AfcsButton.ROL: panel.press_rol,
            AfcsButton.NAV: panel.press_nav,
            AfcsButton.APR: panel.press_apr,
            AfcsButton.BC: panel.press_bc,
            AfcsButton.ALT: panel.press_alt,
            AfcsButton.VS: panel.press_vs,
            AfcsButton.FLC: panel.press_flc,
            AfcsButton.VNV: panel.press_vnv,
            AfcsButton.GA: panel.press_ga,
            AfcsButton.GS: panel.press_gs,
        }
        self.assertEqual(set(method_by_button), set(AfcsButton))

        for button, method in method_by_button.items():
            control.commands.clear()
            method()
            self.assertEqual(control.commands, [BUTTON_COMMANDS[button]])

    def test_press_button_accepts_string_values(self) -> None:
        panel, _, control, _ = _panel()
        panel.press_button("HDG")
        self.assertEqual(control.commands, [BUTTON_COMMANDS[AfcsButton.HDG]])

    def test_press_button_rejects_unknown_names(self) -> None:
        panel, _, control, _ = _panel()
        with self.assertRaises(ValueError):
            panel.press_button("WARP")
        self.assertEqual(control.commands, [])

    def test_every_button_has_a_command_mapping(self) -> None:
        self.assertEqual(set(BUTTON_COMMANDS), set(AfcsButton))


class SelectorTests(unittest.TestCase):
    def test_named_setters_write_mapped_datarefs(self) -> None:
        panel, _, control, _ = _panel()
        panel.set_heading_bug_deg(270.0)
        panel.set_altitude_selector_ft(5500.0)
        panel.set_vertical_speed_target_fpm(-500.0)
        panel.set_airspeed_target_kt(95.0)
        panel.set_course_deg(131.0)

        expected = [
            (SELECTOR_RANGES[AfcsSelector.HEADING][0], 270.0),
            (SELECTOR_RANGES[AfcsSelector.ALTITUDE][0], 5500.0),
            (SELECTOR_RANGES[AfcsSelector.VERTICAL_SPEED][0], -500.0),
            (SELECTOR_RANGES[AfcsSelector.AIRSPEED][0], 95.0),
            (SELECTOR_RANGES[AfcsSelector.COURSE][0], 131.0),
        ]
        self.assertEqual(control.dataref_writes, expected)

    def test_set_selector_accepts_string_values(self) -> None:
        panel, _, control, _ = _panel()
        panel.set_selector("ALTITUDE", 3000.0)
        self.assertEqual(
            control.dataref_writes,
            [(SELECTOR_RANGES[AfcsSelector.ALTITUDE][0], 3000.0)],
        )

    def test_out_of_range_values_raise_and_send_nothing(self) -> None:
        panel, _, control, _ = _panel()
        cases = [
            (panel.set_heading_bug_deg, 360.0),
            (panel.set_heading_bug_deg, -0.1),
            (panel.set_altitude_selector_ft, 60001.0),
            (panel.set_altitude_selector_ft, -1001.0),
            (panel.set_vertical_speed_target_fpm, 8001.0),
            (panel.set_airspeed_target_kt, 400.5),
            (panel.set_course_deg, 400.0),
        ]
        for method, value in cases:
            with self.subTest(method=method.__name__, value=value):
                with self.assertRaises(ValueError):
                    method(value)
        self.assertEqual(control.dataref_writes, [])

    def test_boundary_values_are_accepted(self) -> None:
        panel, _, control, _ = _panel()
        panel.set_heading_bug_deg(0.0)
        panel.set_heading_bug_deg(359.9)
        panel.set_altitude_selector_ft(-1000.0)
        panel.set_altitude_selector_ft(60000.0)
        panel.set_vertical_speed_target_fpm(-8000.0)
        panel.set_airspeed_target_kt(0.0)
        self.assertEqual(len(control.dataref_writes), 6)

    def test_non_finite_and_non_numeric_values_raise(self) -> None:
        panel, _, control, _ = _panel()
        for value in (math.nan, math.inf, "90", None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                panel.set_heading_bug_deg(value)  # type: ignore[arg-type]
        self.assertEqual(control.dataref_writes, [])

    def test_unknown_selector_raises(self) -> None:
        panel, _, control, _ = _panel()
        with self.assertRaises(ValueError):
            panel.set_selector("THRUST", 50.0)
        self.assertEqual(control.dataref_writes, [])


class ReadAndWaitTests(unittest.TestCase):
    def test_read_state_passes_through_explicit_arguments(self) -> None:
        panel, adapter, _, _ = _panel()
        panel.read_state(timeout_seconds=1.5, stale_after_seconds=2.0)
        self.assertEqual(
            adapter.read_calls,
            [{"timeout_seconds": 1.5, "stale_after_seconds": 2.0}],
        )

    def test_wait_for_returns_satisfied_with_final_state(self) -> None:
        target = _state(lateral_mode_active=LateralMode.HDG)
        panel, _, _, _ = _panel([_state(), _state(), target])

        result = panel.wait_for(
            lambda state: state.lateral_mode_active is LateralMode.HDG,
            timeout_seconds=10.0,
            poll_interval_seconds=0.5,
            stale_after_seconds=2.0,
        )

        self.assertIsInstance(result, ConfirmationResult)
        self.assertTrue(result.satisfied)
        self.assertIs(result.state.lateral_mode_active, LateralMode.HDG)

    def test_wait_for_timeout_reports_final_observed_state(self) -> None:
        panel, _, _, clock = _panel([_state()])

        result = panel.wait_for(
            lambda state: state.lateral_mode_active is LateralMode.HDG,
            timeout_seconds=2.0,
            poll_interval_seconds=0.5,
            stale_after_seconds=2.0,
        )

        self.assertFalse(result.satisfied)
        self.assertIsNone(result.state.lateral_mode_active)
        self.assertGreaterEqual(clock.now, 2.0)

    def test_wait_for_sends_no_input(self) -> None:
        panel, _, control, _ = _panel([_state()])
        panel.wait_for(
            lambda state: False,
            timeout_seconds=1.0,
            poll_interval_seconds=0.5,
            stale_after_seconds=2.0,
        )
        self.assertEqual(control.commands, [])
        self.assertEqual(control.dataref_writes, [])

    def test_wait_for_rejects_invalid_arguments(self) -> None:
        panel, _, _, _ = _panel()
        for kwargs in (
            {"timeout_seconds": -1.0, "poll_interval_seconds": 0.5},
            {"timeout_seconds": 1.0, "poll_interval_seconds": math.nan},
            {"timeout_seconds": True, "poll_interval_seconds": 0.5},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                panel.wait_for(
                    lambda state: True,
                    stale_after_seconds=2.0,
                    **kwargs,  # type: ignore[arg-type]
                )

    def test_close_closes_adapter_and_control(self) -> None:
        panel, adapter, control, _ = _panel()
        panel.close()
        self.assertTrue(adapter.closed)
        self.assertTrue(control.closed)


if __name__ == "__main__":
    unittest.main()
