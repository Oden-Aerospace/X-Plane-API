import math
import unittest
from datetime import datetime, timezone

from xplane_api.mcp.backend import (
    CONDITION_FIELDS,
    AutopilotToolBackend,
    ControlDisabled,
    condition_holds,
    parse_condition,
)
from xplane_api.models.autopilot import (
    AutopilotState,
    LateralMode,
    VerticalMode,
)
from xplane_api.models.flight_plan import (
    FlightPlanState,
    FlightPlanStatus,
    PlanFieldStatus,
)
from xplane_api.models.state import FieldStatus


def _state(**kwargs) -> AutopilotState:
    return AutopilotState(
        timestamp_utc=datetime(2026, 11, 1, tzinfo=timezone.utc), **kwargs
    )


class FakePanel:
    def __init__(self, states=None) -> None:
        self.states = list(states or [_state()])
        self.pressed: list[str] = []
        self.selectors: list[tuple[str, float]] = []
        self.read_calls: list[dict] = []
        self.events: list[tuple[str, str | None]] = []

    def read_state(self, *, timeout_seconds, stale_after_seconds):
        self.events.append(("read", None))
        self.read_calls.append(
            {
                "timeout_seconds": timeout_seconds,
                "stale_after_seconds": stale_after_seconds,
            }
        )
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    def press_button(self, button):
        from xplane_api.models.autopilot import AfcsButton

        AfcsButton(button)
        self.pressed.append(str(button))
        self.events.append(("press", str(button)))

    def set_selector(self, selector, value):
        from xplane_api.models.autopilot import AfcsSelector

        AfcsSelector(selector)
        if not (0 <= value < 360) and selector == "HEADING":
            raise ValueError("out of range")
        self.selectors.append((str(selector), float(value)))

    def wait_for(self, predicate, *, timeout_seconds, poll_interval_seconds,
                 stale_after_seconds):
        from xplane_api.autopilot.panel import ConfirmationResult

        self.events.append(("wait", None))
        final = self.states[0]
        for state in list(self.states):
            final = state
            if predicate(state):
                return ConfirmationResult(satisfied=True, state=state)
        return ConfirmationResult(satisfied=False, state=final)


class FakeFlightPlanClient:
    def __init__(self, events=None) -> None:
        self.read_calls = 0
        self.direct_to_calls: list[str] = []
        self.events = events if events is not None else []

    def read_plan(self) -> FlightPlanState:
        self.read_calls += 1
        return FlightPlanState(
            timestamp_utc=datetime(2026, 11, 1, tzinfo=timezone.utc),
            source="pilot_primary",
            available=False,
            status=FlightPlanStatus.NO_ACTIVE_PLAN,
            active_index=None,
            active_index_status=PlanFieldStatus.UNAVAILABLE,
            entries=(),
        )

    def direct_to(
        self,
        identifier: str,
        *,
        timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.1,
    ) -> dict:
        del timeout_seconds, poll_interval_seconds
        self.direct_to_calls.append(identifier)
        self.events.append(("route", identifier))
        return {"status": "confirmed", "target_identifier": identifier}


class ParseConditionTests(unittest.TestCase):
    def test_accepts_mixed_condition_with_tolerance(self) -> None:
        normalized = parse_condition(
            {
                "ap_engaged": True,
                "lateral_mode_active": "HDG",
                "vertical_mode_armed": "ALTS",
                "selected_altitude_ft": 5500.0,
            },
            numeric_tolerance=1.0,
        )
        self.assertIs(normalized["lateral_mode_active"], LateralMode.HDG)
        self.assertIs(normalized["vertical_mode_armed"], VerticalMode.ALTS)
        self.assertEqual(normalized["__numeric_tolerance__"], 1.0)

    def test_rejects_empty_unknown_and_bad_values(self) -> None:
        cases = [
            ({}, 1.0),
            ({"warp_mode": True}, 1.0),
            ({"ap_engaged": 1}, 1.0),
            ({"lateral_mode_active": "WARP"}, 1.0),
            ({"selected_altitude_ft": math.nan}, 1.0),
        ]
        for expected, tolerance in cases:
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                parse_condition(expected, tolerance)

    def test_numeric_expectation_requires_tolerance(self) -> None:
        with self.assertRaises(ValueError):
            parse_condition({"selected_altitude_ft": 5500.0}, None)
        for tolerance in (-1.0, math.nan, True):
            with self.subTest(tolerance=tolerance), self.assertRaises(ValueError):
                parse_condition({"selected_altitude_ft": 5500.0}, tolerance)

    def test_enum_only_condition_needs_no_tolerance(self) -> None:
        normalized = parse_condition({"vertical_mode_active": "ALT"}, None)
        self.assertNotIn("__numeric_tolerance__", normalized)


class ConditionHoldsTests(unittest.TestCase):
    def test_and_semantics_with_tolerance(self) -> None:
        state = _state(
            ap_engaged=True,
            lateral_mode_active=LateralMode.HDG,
            selected_altitude_ft=5500.4,
        )
        normalized = parse_condition(
            {
                "ap_engaged": True,
                "lateral_mode_active": "HDG",
                "selected_altitude_ft": 5500.0,
            },
            numeric_tolerance=0.5,
        )
        self.assertTrue(condition_holds(state, normalized))

        tight = parse_condition(
            {"selected_altitude_ft": 5500.0}, numeric_tolerance=0.1
        )
        self.assertFalse(condition_holds(state, tight))

    def test_none_fields_satisfy_nothing(self) -> None:
        state = _state()
        normalized = parse_condition({"ap_engaged": True}, None)
        self.assertFalse(condition_holds(state, normalized))


class BackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = FakePanel()
        self.backend = AutopilotToolBackend(self.panel)  # type: ignore[arg-type]

    def test_read_state_returns_schema_versioned_json(self) -> None:
        payload = self.backend.read_state(
            timeout_seconds=1.0, stale_after_seconds=2.0
        )
        self.assertEqual(payload["schema_version"], 1)
        self.assertIn("field_status", payload)
        self.assertEqual(
            self.panel.read_calls,
            [{"timeout_seconds": 1.0, "stale_after_seconds": 2.0}],
        )

    def test_read_flight_plan_uses_shared_client_and_preserves_unavailable_status(self) -> None:
        client = FakeFlightPlanClient()
        backend = AutopilotToolBackend(
            self.panel,  # type: ignore[arg-type]
            flight_plan_client=client,  # type: ignore[arg-type]
        )
        result = backend.read_flight_plan()
        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "no_active_plan")
        self.assertEqual(client.read_calls, 1)

    def test_direct_to_waypoint_uses_shared_flight_plan_client(self) -> None:
        client = FakeFlightPlanClient()
        backend = AutopilotToolBackend(
            self.panel,  # type: ignore[arg-type]
            flight_plan_client=client,  # type: ignore[arg-type]
        )
        result = backend.direct_to_waypoint("KSEA")
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(client.direct_to_calls, ["KSEA"])

    def test_direct_to_and_activate_confirms_route_before_nav_input(self) -> None:
        events: list[tuple[str, str | None]] = []
        panel = FakePanel(
            [
                _state(
                    ap_engaged=True,
                    lateral_mode_active=LateralMode.HDG,
                    lateral_mode_armed=LateralMode.NONE,
                ),
                _state(
                    ap_engaged=True,
                    lateral_mode_active=LateralMode.GPSS,
                    lateral_mode_armed=LateralMode.NONE,
                ),
            ]
        )
        panel.events = events
        client = FakeFlightPlanClient(events)
        backend = AutopilotToolBackend(
            panel,  # type: ignore[arg-type]
            flight_plan_client=client,  # type: ignore[arg-type]
        )

        result = backend.direct_to_and_activate(
            "KBWI",
            timeout_seconds=5.0,
            poll_interval_seconds=0.1,
            stale_after_seconds=2.0,
        )

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["route"]["target_identifier"], "KBWI")
        self.assertEqual(result["state"]["lateral_mode_active"], "GPSS")
        self.assertTrue(result["state"]["ap_engaged"])
        self.assertEqual(panel.pressed, ["NAV"])
        self.assertEqual(events[0], ("route", "KBWI"))
        self.assertLess(events.index(("route", "KBWI")), events.index(("press", "NAV")))

    def test_direct_to_and_activate_does_not_toggle_satisfied_modes(self) -> None:
        panel = FakePanel(
            [
                _state(
                    ap_engaged=True,
                    lateral_mode_active=LateralMode.NAV,
                    lateral_mode_armed=LateralMode.NONE,
                )
            ]
        )
        backend = AutopilotToolBackend(
            panel,  # type: ignore[arg-type]
            flight_plan_client=FakeFlightPlanClient(),  # type: ignore[arg-type]
        )

        result = backend.direct_to_and_activate(
            "KBWI",
            timeout_seconds=5.0,
            poll_interval_seconds=0.1,
            stale_after_seconds=2.0,
        )

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(panel.pressed, [])

    def test_direct_to_and_activate_engages_ap_then_selects_nav(self) -> None:
        panel = FakePanel(
            [
                _state(
                    ap_engaged=False,
                    lateral_mode_active=LateralMode.HDG,
                    lateral_mode_armed=LateralMode.NONE,
                ),
                _state(
                    ap_engaged=True,
                    lateral_mode_active=LateralMode.HDG,
                    lateral_mode_armed=LateralMode.NONE,
                ),
                _state(
                    ap_engaged=True,
                    lateral_mode_active=LateralMode.NAV,
                    lateral_mode_armed=LateralMode.NONE,
                ),
            ]
        )
        backend = AutopilotToolBackend(
            panel,  # type: ignore[arg-type]
            flight_plan_client=FakeFlightPlanClient(),  # type: ignore[arg-type]
        )

        result = backend.direct_to_and_activate(
            "KBWI",
            timeout_seconds=5.0,
            poll_interval_seconds=0.1,
            stale_after_seconds=2.0,
        )

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(panel.pressed, ["AP", "NAV"])

    def test_direct_to_and_activate_waits_for_armed_navigation_without_repress(self) -> None:
        panel = FakePanel(
            [
                _state(
                    ap_engaged=True,
                    lateral_mode_active=LateralMode.HDG,
                    lateral_mode_armed=LateralMode.NAV,
                ),
                _state(
                    ap_engaged=True,
                    lateral_mode_active=LateralMode.GPSS,
                    lateral_mode_armed=LateralMode.NONE,
                ),
            ]
        )
        backend = AutopilotToolBackend(
            panel,  # type: ignore[arg-type]
            flight_plan_client=FakeFlightPlanClient(),  # type: ignore[arg-type]
        )

        result = backend.direct_to_and_activate(
            "KBWI",
            timeout_seconds=5.0,
            poll_interval_seconds=0.1,
            stale_after_seconds=2.0,
        )

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(panel.pressed, [])

    def test_direct_to_and_activate_returns_partial_when_mode_times_out(self) -> None:
        panel = FakePanel(
            [
                _state(
                    ap_engaged=True,
                    lateral_mode_active=LateralMode.HDG,
                    lateral_mode_armed=LateralMode.NONE,
                )
            ]
        )
        backend = AutopilotToolBackend(
            panel,  # type: ignore[arg-type]
            flight_plan_client=FakeFlightPlanClient(),  # type: ignore[arg-type]
        )

        result = backend.direct_to_and_activate(
            "KBWI",
            timeout_seconds=5.0,
            poll_interval_seconds=0.1,
            stale_after_seconds=2.0,
        )

        self.assertEqual(result["status"], "partial_failure")
        self.assertEqual(result["route"]["target_identifier"], "KBWI")
        self.assertEqual(result["state"]["lateral_mode_active"], "HDG")
        self.assertEqual(panel.pressed, ["NAV"])

    def test_direct_to_and_activate_does_not_control_with_unusable_state(self) -> None:
        panel = FakePanel(
            [
                _state(
                    ap_engaged=True,
                    lateral_mode_active=None,
                    lateral_mode_armed=LateralMode.NONE,
                )
            ]
        )
        backend = AutopilotToolBackend(
            panel,  # type: ignore[arg-type]
            flight_plan_client=FakeFlightPlanClient(),  # type: ignore[arg-type]
        )

        result = backend.direct_to_and_activate(
            "KBWI",
            timeout_seconds=5.0,
            poll_interval_seconds=0.1,
            stale_after_seconds=2.0,
        )

        self.assertEqual(result["status"], "partial_failure")
        self.assertEqual(panel.pressed, [])

    def test_direct_to_and_activate_route_failure_sends_no_autopilot_input(self) -> None:
        class RouteFailure(FakeFlightPlanClient):
            def direct_to(self, identifier: str, **kwargs) -> dict:
                del identifier, kwargs
                raise ValueError("unknown target")

        panel = FakePanel()
        backend = AutopilotToolBackend(
            panel,  # type: ignore[arg-type]
            flight_plan_client=RouteFailure(),  # type: ignore[arg-type]
        )

        with self.assertRaisesRegex(ValueError, "unknown target"):
            backend.direct_to_and_activate(
                "KBWI",
                timeout_seconds=5.0,
                poll_interval_seconds=0.1,
                stale_after_seconds=2.0,
            )

        self.assertEqual(panel.pressed, [])
        self.assertEqual(panel.read_calls, [])

    def test_direct_to_and_activate_validates_timeouts_before_route_write(self) -> None:
        client = FakeFlightPlanClient()
        backend = AutopilotToolBackend(
            self.panel,  # type: ignore[arg-type]
            flight_plan_client=client,  # type: ignore[arg-type]
        )

        with self.assertRaises(ValueError):
            backend.direct_to_and_activate(
                "KBWI",
                timeout_seconds=0.0,
                poll_interval_seconds=0.1,
                stale_after_seconds=2.0,
            )

        self.assertEqual(client.direct_to_calls, [])

    def test_press_button_reports_transmission_only(self) -> None:
        result = self.backend.press_button("HDG")
        self.assertTrue(result["transmitted"])
        self.assertIn("not confirmed", result["note"])
        self.assertEqual(self.panel.pressed, ["HDG"])

    def test_invalid_button_raises_without_effect(self) -> None:
        with self.assertRaises(ValueError):
            self.backend.press_button("WARP")
        self.assertEqual(self.panel.pressed, [])

    def test_set_selector_validates_through_panel(self) -> None:
        result = self.backend.set_selector("ALTITUDE", 5500.0)
        self.assertTrue(result["transmitted"])
        with self.assertRaises(ValueError):
            self.backend.set_selector("HEADING", 400.0)
        self.assertEqual(self.panel.selectors, [("ALTITUDE", 5500.0)])

    def test_wait_for_state_satisfied_and_timeout(self) -> None:
        target = _state(lateral_mode_active=LateralMode.HDG)
        backend = AutopilotToolBackend(  # type: ignore[arg-type]
            FakePanel([_state(), target])
        )
        result = backend.wait_for_state(
            expected={"lateral_mode_active": "HDG"},
            timeout_seconds=2.0,
            poll_interval_seconds=0.1,
            stale_after_seconds=2.0,
        )
        self.assertTrue(result["satisfied"])
        self.assertEqual(result["state"]["lateral_mode_active"], "HDG")

        result = self.backend.wait_for_state(
            expected={"lateral_mode_active": "HDG"},
            timeout_seconds=0.2,
            poll_interval_seconds=0.1,
            stale_after_seconds=2.0,
        )
        self.assertFalse(result["satisfied"])
        self.assertIsNone(result["state"]["lateral_mode_active"])

    def test_wait_for_state_validates_condition_first(self) -> None:
        panel = FakePanel()
        backend = AutopilotToolBackend(panel)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            backend.wait_for_state(
                expected={"selected_altitude_ft": 5500.0},
                timeout_seconds=1.0,
                poll_interval_seconds=0.1,
                stale_after_seconds=2.0,
            )
        self.assertEqual(panel.read_calls, [])

    def test_stale_state_passes_through_unchanged(self) -> None:
        stale = _state(
            selected_altitude_ft=2000.0,
            field_status={"selected_altitude_ft": FieldStatus.STALE},
        )
        backend = AutopilotToolBackend(FakePanel([stale]))  # type: ignore[arg-type]
        payload = backend.read_state(timeout_seconds=0.0, stale_after_seconds=1.0)
        self.assertEqual(payload["field_status"]["selected_altitude_ft"], "stale")


class ControlDisabledTests(unittest.TestCase):
    def test_raises_on_any_send_and_closes_quietly(self) -> None:
        stub = ControlDisabled()
        with self.assertRaises(RuntimeError):
            stub.send_command("sim/autopilot/heading")
        with self.assertRaises(RuntimeError):
            stub.write_dataref("sim/x", 1.0)
        stub.close()

    def test_condition_fields_cover_documented_surface(self) -> None:
        self.assertIn("ap_engaged", CONDITION_FIELDS)
        self.assertIn("selected_altitude_ft", CONDITION_FIELDS)
        self.assertEqual(len(CONDITION_FIELDS), 12)


if __name__ == "__main__":
    unittest.main()
