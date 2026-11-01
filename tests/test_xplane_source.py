from datetime import datetime, timezone
import unittest

from xplane_api.models.state import FieldStatus, StateSource
from xplane_api.sources.rref_client import TimedDatarefValue
from xplane_api.sources.xplane import (
    NUMERIC_DATAREFS,
    XPlaneStateAdapter,
    _BOOLEAN_DATAREFS,
    _STRING_ARRAY_DATAREFS,
    _STRING_ARRAY_LENGTHS,
)


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


class XPlaneStateAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = StubRrefClient()
        self.adapter = XPlaneStateAdapter(
            self.client,
            frequency_hz=2,
            clock=lambda: 100.0,
            utc_now=lambda: datetime(2026, 10, 13, tzinfo=timezone.utc),
        )

    def test_builds_normalized_state_with_units_and_identity_metadata(self) -> None:
        raw_values = {
            "latitude_deg": 38.863,
            "longitude_deg": -76.945,
            "elevation_msl_m": 305.0,
            "barometric_altitude_ft": 998.0,
            "indicated_airspeed_kt": 113.4,
            "ground_speed_mps": 59.2,
            "vertical_speed_fpm": 179.2,
            "true_heading_deg": 88.4,
            "magnetic_heading_deg": 99.1,
            "flight_path_heading_deg": 75.2,
            "pitch_deg": 0.14,
            "roll_deg": 0.0,
            "turn_indicator_deflection_deg": -0.45,
            "slip_indicator_deflection_deg": -0.23,
            "barometer_setting_inhg": 29.921,
            "on_ground": 0.0,
        }
        path_values = {
            NUMERIC_DATAREFS[field_name]: value
            for field_name, value in raw_values.items()
            if field_name in NUMERIC_DATAREFS
        }
        path_values.update(
            {
                _BOOLEAN_DATAREFS["on_ground"]: 0.0,
                **{
                    f"{_STRING_ARRAY_DATAREFS['aircraft_type_code']}[{offset}]": ord(char)
                    for offset, char in enumerate("C172\0")
                },
                **{
                    f"{_STRING_ARRAY_DATAREFS['tail_number']}[{offset}]": ord(char)
                    for offset, char in enumerate("N172SP\0")
                },
                **{
                    f"{_STRING_ARRAY_DATAREFS['flight_id']}[{offset}]": ord(char)
                    for offset, char in enumerate("N172SP\0")
                },
            }
        )
        for path, value in path_values.items():
            self.client.latest_values[path] = TimedDatarefValue(path, value, 99.5)

        state = self.adapter.read_state(timeout_seconds=0, stale_after_seconds=2)

        self.assertEqual(state.source, StateSource.LIVE_XPLANE)
        self.assertEqual(state.latitude_deg, 38.863)
        self.assertEqual(state.elevation_msl_m, 305.0)
        self.assertEqual(state.barometric_altitude_ft, 998.0)
        self.assertEqual(state.indicated_airspeed_kt, 113.4)
        self.assertEqual(state.flight_path_heading_deg, 75.2)
        self.assertFalse(state.on_ground)
        self.assertEqual(state.aircraft_type_code, "C172")
        self.assertEqual(state.tail_number, "N172SP")
        self.assertEqual(state.flight_id, "N172SP")
        self.assertTrue(state.is_usable("latitude_deg"))
        self.assertEqual(state.field_status["tail_number"], FieldStatus.VALID)
        self.assertIsNone(state.field_status.get("sequence"))

    def test_marks_missing_stale_and_non_finite_fields(self) -> None:
        latitude_path = NUMERIC_DATAREFS["latitude_deg"]
        longitude_path = NUMERIC_DATAREFS["longitude_deg"]
        altitude_path = NUMERIC_DATAREFS["elevation_msl_m"]
        self.client.latest_values[latitude_path] = TimedDatarefValue(
            latitude_path, 38.0, 90.0
        )
        self.client.latest_values[longitude_path] = TimedDatarefValue(
            longitude_path, float("nan"), 99.5
        )
        self.client.latest_values[altitude_path] = TimedDatarefValue(
            altitude_path, 200.0, 99.5
        )

        state = self.adapter.read_state(timeout_seconds=0, stale_after_seconds=2)

        self.assertEqual(state.latitude_deg, 38.0)
        self.assertEqual(state.field_status["latitude_deg"], FieldStatus.STALE)
        self.assertIsNone(state.longitude_deg)
        self.assertEqual(state.field_status["longitude_deg"], FieldStatus.INVALID)
        self.assertEqual(state.field_status["elevation_msl_m"], FieldStatus.VALID)
        self.assertEqual(state.field_status["indicated_airspeed_kt"], FieldStatus.UNAVAILABLE)

    def test_subscribes_each_identity_array_element_and_closes_client(self) -> None:
        self.assertIn(
            f"{_STRING_ARRAY_DATAREFS['aircraft_type_code']}[0]",
            self.client.subscriptions,
        )
        self.assertIn(
            f"{_STRING_ARRAY_DATAREFS['tail_number']}[{_STRING_ARRAY_LENGTHS['tail_number'] - 1}]",
            self.client.subscriptions,
        )
        self.assertIn(
            f"{_STRING_ARRAY_DATAREFS['flight_id']}[{_STRING_ARRAY_LENGTHS['flight_id'] - 1}]",
            self.client.subscriptions,
        )
        self.adapter.close()
        self.assertTrue(self.client.closed)


if __name__ == "__main__":
    unittest.main()
