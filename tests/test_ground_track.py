from datetime import datetime, timedelta, timezone
import unittest

from xplane_api.models.ground_track import GroundTrackDeriver
from xplane_api.models.state import FieldStatus, FlightState


class GroundTrackDeriverTests(unittest.TestCase):
    def make_state(self, seconds: int, latitude: float, longitude: float) -> FlightState:
        return FlightState(
            timestamp_utc=datetime(2026, 10, 13, tzinfo=timezone.utc)
            + timedelta(seconds=seconds),
            latitude_deg=latitude,
            longitude_deg=longitude,
        )

    def test_derives_eastbound_course_after_two_positions(self) -> None:
        deriver = GroundTrackDeriver(minimum_movement_m=10.0)
        first = deriver.update(self.make_state(0, 0.0, 0.0))
        second = deriver.update(self.make_state(10, 0.0, 0.01))
        self.assertIsNone(first.ground_track_deg)
        self.assertEqual(first.field_status["ground_track_deg"], FieldStatus.UNAVAILABLE)
        self.assertAlmostEqual(second.ground_track_deg, 90.0, places=2)
        self.assertEqual(second.field_status["ground_track_deg"], FieldStatus.VALID)

    def test_insufficient_movement_keeps_baseline_until_threshold_is_met(self) -> None:
        deriver = GroundTrackDeriver(minimum_movement_m=200.0)
        deriver.update(self.make_state(0, 0.0, 0.0))
        small_move = deriver.update(self.make_state(5, 0.0, 0.001))
        enough_move = deriver.update(self.make_state(10, 0.0, 0.003))
        self.assertIsNone(small_move.ground_track_deg)
        self.assertEqual(small_move.field_status["ground_track_deg"], FieldStatus.UNAVAILABLE)
        self.assertAlmostEqual(enough_move.ground_track_deg, 90.0, places=2)

    def test_missing_position_and_non_monotonic_time_are_not_valid(self) -> None:
        deriver = GroundTrackDeriver(minimum_movement_m=0.0)
        missing = deriver.update(FlightState(timestamp_utc=datetime.now(timezone.utc)))
        self.assertEqual(missing.field_status["ground_track_deg"], FieldStatus.UNAVAILABLE)

        first = self.make_state(10, 38.0, -76.0)
        deriver.update(first)
        out_of_order = deriver.update(self.make_state(9, 38.0, -75.99))
        self.assertIsNone(out_of_order.ground_track_deg)
        self.assertEqual(out_of_order.field_status["ground_track_deg"], FieldStatus.INVALID)

    def test_wraps_northbound_course_to_zero_through_360(self) -> None:
        deriver = GroundTrackDeriver(minimum_movement_m=10.0)
        deriver.update(self.make_state(0, 10.0, 0.0))
        north = deriver.update(self.make_state(10, 10.01, 0.0))
        self.assertAlmostEqual(north.ground_track_deg, 0.0, places=2)

    def test_requires_explicit_threshold(self) -> None:
        with self.assertRaises(TypeError):
            GroundTrackDeriver()  # type: ignore[call-arg]
        with self.assertRaises(ValueError):
            GroundTrackDeriver(minimum_movement_m=-1)


if __name__ == "__main__":
    unittest.main()
