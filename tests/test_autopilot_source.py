from datetime import datetime, timezone
import unittest

from xplane_api.models.autopilot import LateralMode, VerticalMode
from xplane_api.models.state import FieldStatus
from xplane_api.sources.autopilot import (
    AutopilotStateAdapter,
    _BOOLEAN_DATAREFS,
    _FLIGHT_DIRECTOR_MODE,
    _LATERAL_STATUS_MODES,
    _NUMERIC_DATAREFS,
    _VERTICAL_ARMED_FLAGS,
    _VERTICAL_STATUS_MODES,
)
from xplane_api.sources.rref_client import TimedDatarefValue


class StubRrefClient:
    def __init__(self) -> None:
        self.latest_values: dict[str, TimedDatarefValue] = {}
        self.subscriptions: dict[str, int] = {}
        self.closed = False

    def subscribe(self, path: str, frequency_hz: int) -> int:
        index = len(self.subscriptions) + 1
        self.subscriptions[path] = index
        return index

    def poll(self, timeout: float | None = None) -> tuple[()]:
        return ()

    def close(self) -> None:
        self.closed = True

    def resubscribe_all(self) -> int:
        return len(self.subscriptions)


def _all_paths() -> list[str]:
    return [
        _FLIGHT_DIRECTOR_MODE,
        *list(_LATERAL_STATUS_MODES),
        *list(_VERTICAL_STATUS_MODES),
        *list(_VERTICAL_ARMED_FLAGS),
        *list(_BOOLEAN_DATAREFS.values()),
        *list(_NUMERIC_DATAREFS.values()),
    ]


class AutopilotStateAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = StubRrefClient()
        self.adapter = AutopilotStateAdapter(
            self.client,
            frequency_hz=2,
            clock=lambda: 100.0,
            utc_now=lambda: datetime(2026, 11, 1, tzinfo=timezone.utc),
        )

    def _feed(self, overrides: dict[str, float], received_at: float = 100.0) -> None:
        for path in _all_paths():
            value = overrides.get(path, 0.0)
            self.client.latest_values[path] = TimedDatarefValue(
                path=path, value=value, received_at_monotonic=received_at
            )

    def test_subscribes_every_configured_dataref(self) -> None:
        self.assertEqual(set(self.client.subscriptions), set(_all_paths()))

    def test_maps_engaged_heading_and_alt_hold_snapshot(self) -> None:
        self._feed(
            {
                "sim/cockpit2/autopilot/servos_on": 1.0,
                _FLIGHT_DIRECTOR_MODE: 2.0,
                "sim/cockpit2/autopilot/heading_status": 2.0,
                "sim/cockpit2/autopilot/altitude_hold_status": 2.0,
                "sim/cockpit2/autopilot/alts_armed": 1.0,
                "sim/cockpit2/autopilot/heading_dial_deg_mag_pilot": 23.4,
                "sim/cockpit2/autopilot/altitude_dial_ft": 2000.0,
            }
        )
        state = self.adapter.read_state(
            timeout_seconds=0.0, stale_after_seconds=2.0
        )

        self.assertTrue(state.ap_engaged)
        self.assertTrue(state.flight_director_on)
        self.assertIs(state.lateral_mode_active, LateralMode.HDG)
        self.assertIs(state.lateral_mode_armed, LateralMode.NONE)
        self.assertIs(state.vertical_mode_active, VerticalMode.ALT)
        self.assertIs(state.vertical_mode_armed, VerticalMode.ALTS)
        self.assertEqual(state.selected_heading_deg_mag, 23.4)
        self.assertEqual(state.selected_altitude_ft, 2000.0)
        self.assertTrue(state.is_usable("lateral_mode_active"))
        self.assertEqual(
            state.raw_status_values["sim/cockpit2/autopilot/heading_status"],
            2.0,
        )

    def test_blank_annunciator_reports_none_modes(self) -> None:
        self._feed({})
        state = self.adapter.read_state(
            timeout_seconds=0.0, stale_after_seconds=2.0
        )
        self.assertIs(state.lateral_mode_active, LateralMode.NONE)
        self.assertIs(state.lateral_mode_armed, LateralMode.NONE)
        self.assertIs(state.vertical_mode_active, VerticalMode.NONE)
        self.assertIs(state.vertical_mode_armed, VerticalMode.NONE)
        self.assertFalse(state.ap_engaged)

    def test_armed_statuses_report_armed_modes(self) -> None:
        self._feed(
            {
                "sim/cockpit2/autopilot/nav_status": 1.0,
                "sim/cockpit2/autopilot/glideslope_status": 1.0,
                "sim/cockpit2/autopilot/alts_armed": 1.0,
            }
        )
        state = self.adapter.read_state(
            timeout_seconds=0.0, stale_after_seconds=2.0
        )
        self.assertIs(state.lateral_mode_armed, LateralMode.NAV)
        # GS outranks ALTS in the documented armed precedence.
        self.assertIs(state.vertical_mode_armed, VerticalMode.GS)

    def test_unmapped_status_value_reports_unknown(self) -> None:
        self._feed({"sim/cockpit2/autopilot/heading_status": 3.0})
        state = self.adapter.read_state(
            timeout_seconds=0.0, stale_after_seconds=2.0
        )
        self.assertIs(state.lateral_mode_active, LateralMode.UNKNOWN)
        self.assertIs(state.lateral_mode_armed, LateralMode.UNKNOWN)
        # Vertical family is unaffected.
        self.assertIs(state.vertical_mode_active, VerticalMode.NONE)

    def test_multiple_active_modes_report_unknown(self) -> None:
        self._feed(
            {
                "sim/cockpit2/autopilot/heading_status": 2.0,
                "sim/cockpit2/autopilot/nav_status": 2.0,
            }
        )
        state = self.adapter.read_state(
            timeout_seconds=0.0, stale_after_seconds=2.0
        )
        self.assertIs(state.lateral_mode_active, LateralMode.UNKNOWN)

    def test_missing_status_dataref_reports_unavailable_modes(self) -> None:
        self._feed({})
        del self.client.latest_values["sim/cockpit2/autopilot/heading_status"]
        state = self.adapter.read_state(
            timeout_seconds=0.0, stale_after_seconds=2.0
        )
        self.assertIsNone(state.lateral_mode_active)
        self.assertEqual(
            state.field_status["lateral_mode_active"], FieldStatus.UNAVAILABLE
        )
        # Vertical family still reports normally.
        self.assertIs(state.vertical_mode_active, VerticalMode.NONE)

    def test_stale_status_dataref_marks_modes_stale(self) -> None:
        self._feed(
            {"sim/cockpit2/autopilot/heading_status": 2.0}, received_at=90.0
        )
        state = self.adapter.read_state(
            timeout_seconds=0.0, stale_after_seconds=2.0
        )
        self.assertEqual(
            state.field_status["lateral_mode_active"], FieldStatus.STALE
        )
        self.assertFalse(state.is_usable("lateral_mode_active"))
        self.assertEqual(
            state.field_status["selected_altitude_ft"], FieldStatus.STALE
        )

    def test_no_data_reports_everything_unavailable(self) -> None:
        state = self.adapter.read_state(
            timeout_seconds=0.0, stale_after_seconds=2.0
        )
        self.assertIsNone(state.ap_engaged)
        self.assertIsNone(state.lateral_mode_active)
        self.assertEqual(
            state.field_status["selected_heading_deg_mag"],
            FieldStatus.UNAVAILABLE,
        )

    def test_rejects_invalid_read_arguments(self) -> None:
        for kwargs in (
            {"timeout_seconds": -1.0, "stale_after_seconds": 1.0},
            {"timeout_seconds": 0.0, "stale_after_seconds": float("nan")},
            {"timeout_seconds": True, "stale_after_seconds": 1.0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.adapter.read_state(**kwargs)  # type: ignore[arg-type]

    def test_close_closes_client(self) -> None:
        self.adapter.close()
        self.assertTrue(self.client.closed)


if __name__ == "__main__":
    unittest.main()
