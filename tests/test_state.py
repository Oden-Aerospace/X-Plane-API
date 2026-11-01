from datetime import datetime, timezone
import unittest

from xplane_api.models.state import FieldStatus, FlightState, StateSource


class FlightStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sample = FlightState(
            timestamp_utc=datetime(2026, 10, 13, 12, 30, tzinfo=timezone.utc),
            source=StateSource.LIVE_XPLANE,
            sequence=5,
            latitude_deg=38.863,
            longitude_deg=-76.945,
            elevation_msl_m=305.0,
            barometric_altitude_ft=998.0,
            indicated_airspeed_kt=113.4,
            ground_speed_mps=59.2,
            vertical_speed_fpm=179.2,
            true_heading_deg=88.4,
            magnetic_heading_deg=99.1,
            flight_path_heading_deg=75.2,
            pitch_deg=0.14,
            roll_deg=0.0,
            turn_indicator_deflection_deg=-0.43,
            slip_indicator_deflection_deg=-0.23,
            on_ground=False,
            aircraft_type_code="C172",
            tail_number="N172SP",
            barometer_setting_inhg=29.921,
        )

    def test_infers_valid_and_unavailable_statuses(self) -> None:
        self.assertTrue(self.sample.is_usable("latitude_deg"))
        self.assertEqual(
            self.sample.field_status["barometric_altitude_ft"], FieldStatus.VALID
        )
        self.assertEqual(
            self.sample.field_status["aircraft_type_code"], FieldStatus.VALID
        )
        missing = FlightState(timestamp_utc=self.sample.timestamp_utc)
        self.assertEqual(
            missing.field_status["latitude_deg"], FieldStatus.UNAVAILABLE
        )
        self.assertFalse(missing.is_usable("latitude_deg"))

    def test_explicit_stale_and_invalid_statuses_are_preserved(self) -> None:
        sample = FlightState(
            timestamp_utc=self.sample.timestamp_utc,
            latitude_deg=38.8,
            indicated_airspeed_kt=100.0,
            field_status={
                "latitude_deg": FieldStatus.STALE,
                "indicated_airspeed_kt": "invalid",
            },
        )
        self.assertFalse(sample.is_usable("latitude_deg"))
        self.assertFalse(sample.is_usable("indicated_airspeed_kt"))
        self.assertEqual(sample.field_status["latitude_deg"], FieldStatus.STALE)
        self.assertEqual(
            sample.field_status["indicated_airspeed_kt"], FieldStatus.INVALID
        )

    def test_valid_status_requires_a_value(self) -> None:
        with self.assertRaises(ValueError):
            FlightState(
                timestamp_utc=self.sample.timestamp_utc,
                field_status={"latitude_deg": FieldStatus.VALID},
            )

    def test_normalizes_timezone_to_utc(self) -> None:
        from datetime import timedelta

        local = datetime(
            2026, 10, 13, 8, 30, tzinfo=timezone(timedelta(hours=-4))
        )
        sample = FlightState(timestamp_utc=local)
        self.assertEqual(sample.timestamp_utc.hour, 12)
        self.assertEqual(sample.timestamp_utc.utcoffset(), timezone.utc.utcoffset(None))

    def test_rejects_naive_timestamp_and_non_finite_values(self) -> None:
        with self.assertRaises(ValueError):
            FlightState(timestamp_utc=datetime(2026, 10, 13))
        with self.assertRaises(ValueError):
            FlightState(
                timestamp_utc=self.sample.timestamp_utc,
                latitude_deg=float("nan"),
            )

    def test_json_round_trip_preserves_values_and_status(self) -> None:
        record = self.sample.to_dict()
        restored = FlightState.from_dict(record)
        self.assertEqual(restored, self.sample)
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["source"], "live_xplane")
        self.assertTrue(record["timestamp_utc"].endswith("Z"))

    def test_rejects_unknown_version_and_fields(self) -> None:
        record = self.sample.to_dict()
        record["schema_version"] = 2
        with self.assertRaises(ValueError):
            FlightState.from_dict(record)

        record = self.sample.to_dict()
        record["mystery"] = 1
        with self.assertRaises(ValueError):
            FlightState.from_dict(record)

    def test_field_status_is_immutable(self) -> None:
        with self.assertRaises(TypeError):
            self.sample.field_status["latitude_deg"] = FieldStatus.STALE


if __name__ == "__main__":
    unittest.main()
