import math
import unittest
from datetime import datetime, timezone

from xplane_api.models.autopilot import (
    AUTOPILOT_SCHEMA_VERSION,
    AfcsButton,
    AfcsSelector,
    AutopilotState,
    LateralMode,
    VerticalMode,
)
from xplane_api.models.state import FieldStatus


def _ts() -> datetime:
    return datetime(2026, 11, 1, 19, 0, 0, tzinfo=timezone.utc)


class AutopilotStateConstructionTests(unittest.TestCase):
    def test_minimal_state_defaults_to_unavailable(self) -> None:
        state = AutopilotState(timestamp_utc=_ts())
        self.assertEqual(
            state.field_status["lateral_mode_active"], FieldStatus.UNAVAILABLE
        )
        self.assertEqual(
            state.field_status["selected_altitude_ft"], FieldStatus.UNAVAILABLE
        )
        self.assertFalse(state.is_usable("ap_engaged"))

    def test_present_values_infer_valid_status(self) -> None:
        state = AutopilotState(
            timestamp_utc=_ts(),
            ap_engaged=True,
            lateral_mode_active=LateralMode.HDG,
            vertical_mode_active=VerticalMode.ALT,
            vertical_mode_armed=VerticalMode.ALTS,
            selected_heading_deg_mag=23.4,
            selected_altitude_ft=2000.0,
        )
        self.assertTrue(state.is_usable("ap_engaged"))
        self.assertTrue(state.is_usable("lateral_mode_active"))
        self.assertEqual(state.vertical_mode_armed, VerticalMode.ALTS)

    def test_mode_fields_accept_enum_value_strings(self) -> None:
        state = AutopilotState(
            timestamp_utc=_ts(),
            lateral_mode_active="HDG",
            vertical_mode_active="VS",
        )
        self.assertIs(state.lateral_mode_active, LateralMode.HDG)
        self.assertIs(state.vertical_mode_active, VerticalMode.VS)

    def test_rejects_unknown_mode_strings(self) -> None:
        with self.assertRaises(ValueError):
            AutopilotState(timestamp_utc=_ts(), lateral_mode_active="WARP")

    def test_rejects_naive_timestamp(self) -> None:
        with self.assertRaises(ValueError):
            AutopilotState(timestamp_utc=datetime(2026, 11, 1))

    def test_rejects_non_finite_numeric(self) -> None:
        with self.assertRaises(ValueError):
            AutopilotState(timestamp_utc=_ts(), selected_altitude_ft=math.inf)

    def test_rejects_non_bool_engagement(self) -> None:
        with self.assertRaises(TypeError):
            AutopilotState(timestamp_utc=_ts(), ap_engaged=1)

    def test_rejects_valid_status_for_missing_value(self) -> None:
        with self.assertRaises(ValueError):
            AutopilotState(
                timestamp_utc=_ts(),
                field_status={"ap_engaged": FieldStatus.VALID},
            )

    def test_explicit_stale_status_is_preserved(self) -> None:
        state = AutopilotState(
            timestamp_utc=_ts(),
            selected_heading_deg_mag=180.0,
            field_status={"selected_heading_deg_mag": FieldStatus.STALE},
        )
        self.assertEqual(
            state.field_status["selected_heading_deg_mag"], FieldStatus.STALE
        )
        self.assertFalse(state.is_usable("selected_heading_deg_mag"))

    def test_rejects_unknown_field_status_keys(self) -> None:
        with self.assertRaises(ValueError):
            AutopilotState(
                timestamp_utc=_ts(),
                field_status={"warp_mode": FieldStatus.VALID},
            )

    def test_raw_status_values_are_normalized_floats(self) -> None:
        state = AutopilotState(
            timestamp_utc=_ts(),
            raw_status_values={"sim/cockpit2/autopilot/heading_status": 2},
        )
        self.assertEqual(
            state.raw_status_values["sim/cockpit2/autopilot/heading_status"],
            2.0,
        )


class AutopilotStateJsonTests(unittest.TestCase):
    def test_json_round_trip_preserves_every_field(self) -> None:
        state = AutopilotState(
            timestamp_utc=_ts(),
            ap_engaged=True,
            flight_director_on=True,
            yaw_damper_on=False,
            ap_disconnect_annunciated=False,
            lateral_mode_active=LateralMode.HDG,
            lateral_mode_armed=LateralMode.NAV,
            vertical_mode_active=VerticalMode.VS,
            vertical_mode_armed=VerticalMode.ALTS,
            selected_heading_deg_mag=270.0,
            selected_altitude_ft=5500.0,
            selected_vertical_speed_fpm=500.0,
            selected_airspeed_kt=95.0,
            selected_course_deg_mag=131.0,
            fd_pitch_command_deg=2.5,
            fd_roll_command_deg=-1.0,
            raw_status_values={"sim/cockpit2/autopilot/heading_status": 2.0},
            field_status={"fd_roll_command_deg": FieldStatus.STALE},
        )

        payload = state.to_json_dict()
        self.assertEqual(payload["schema_version"], AUTOPILOT_SCHEMA_VERSION)
        self.assertEqual(payload["lateral_mode_active"], "HDG")
        self.assertEqual(payload["field_status"]["fd_roll_command_deg"], "stale")

        rebuilt = AutopilotState.from_json_dict(payload)
        self.assertEqual(rebuilt, state)

    def test_from_json_rejects_unknown_schema_version(self) -> None:
        payload = AutopilotState(timestamp_utc=_ts()).to_json_dict()
        payload["schema_version"] = 99
        with self.assertRaises(ValueError):
            AutopilotState.from_json_dict(payload)


class ModeVocabularyTests(unittest.TestCase):
    def test_modes_include_blank_and_unknown(self) -> None:
        self.assertIn(LateralMode.NONE, LateralMode)
        self.assertIn(LateralMode.UNKNOWN, LateralMode)
        self.assertIn(VerticalMode.NONE, VerticalMode)
        self.assertIn(VerticalMode.UNKNOWN, VerticalMode)

    def test_buttons_cover_full_mode_controller(self) -> None:
        names = {button.value for button in AfcsButton}
        self.assertEqual(
            names,
            {
                "AP", "AP_DISC", "FD", "YD", "HDG", "ROL", "NAV",
                "APR", "BC", "ALT", "VS", "FLC", "VNV", "GA", "GS",
            },
        )

    def test_selectors_cover_five_dials(self) -> None:
        names = {selector.value for selector in AfcsSelector}
        self.assertEqual(
            names,
            {"HEADING", "ALTITUDE", "VERTICAL_SPEED", "AIRSPEED", "COURSE"},
        )


if __name__ == "__main__":
    unittest.main()
