from datetime import datetime, timedelta, timezone
from dataclasses import replace
from io import StringIO
import json
import threading
import time
import unittest

from xplane_api import (
    AutopilotJsonlPlayback,
    AutopilotPlaybackEvent,
    AutopilotState,
    FieldStatus,
    FlightState,
    LateralMode,
    PairedJsonlPlayback,
    PlaybackStatus,
    StateSource,
    TelemetryPlaybackEvent,
    VerticalMode,
    iter_autopilot_jsonl,
    write_autopilot_jsonl,
    write_jsonl,
)


ORIGIN = datetime(2026, 11, 1, tzinfo=timezone.utc)


def autopilot(seconds: float) -> AutopilotState:
    return AutopilotState(
        timestamp_utc=ORIGIN + timedelta(seconds=seconds),
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
        field_status={
            "fd_roll_command_deg": FieldStatus.STALE,
            "selected_course_deg_mag": FieldStatus.INVALID,
            "yaw_damper_on": FieldStatus.UNAVAILABLE,
        },
    )


def flight(seconds: float) -> FlightState:
    return FlightState(
        timestamp_utc=ORIGIN + timedelta(seconds=seconds),
        source=StateSource.LIVE_XPLANE,
        latitude_deg=38.0,
    )


class AutopilotRecordingTests(unittest.TestCase):
    def test_round_trip_preserves_all_fields_and_duplicates(self) -> None:
        state = autopilot(0)
        stream = StringIO()
        write_autopilot_jsonl(stream, state)
        write_autopilot_jsonl(stream, state)
        content = stream.getvalue()
        self.assertEqual(len(content.splitlines()), 2)
        self.assertEqual(json.loads(content.splitlines()[0]), state.to_json_dict())
        self.assertEqual(list(iter_autopilot_jsonl(StringIO("\n" + content))), [state, state])

    def test_invalid_records_report_line_number(self) -> None:
        records = [
            "not json",
            "[]",
            '{"schema_version":99}',
            '{"schema_version":1}',
            '{"schema_version":1,"timestamp_utc":"2026-11-01T00:00:00+00:00","field_status":[]}',
        ]
        for record in records:
            with self.subTest(record=record), self.assertRaisesRegex(ValueError, "line 2"):
                list(iter_autopilot_jsonl(StringIO("\n" + record + "\n")))

    def test_autopilot_cursor_rejects_backwards_timestamps(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-decreasing"):
            AutopilotJsonlPlayback([autopilot(2), autopilot(1)])

    def test_autopilot_cursor_preserves_snapshot_and_controls(self) -> None:
        stream = StringIO()
        for sample in (autopilot(0), autopilot(1)):
            write_autopilot_jsonl(stream, sample)
        stream.seek(0)
        cursor = AutopilotJsonlPlayback.from_stream(stream)
        self.assertIsNone(cursor.next_sample())
        cursor.play()
        self.assertEqual(cursor.next_sample(), autopilot(0))
        cursor.pause()
        self.assertIsNone(cursor.next_sample())
        cursor.play()
        self.assertEqual(cursor.next_sample(), autopilot(1))
        self.assertEqual(cursor.status, PlaybackStatus.ENDED)
        cursor.play()
        self.assertEqual(cursor.next_sample(), autopilot(0))
        cursor.stop()
        self.assertEqual(cursor.position, 0)


class PairedPlaybackTests(unittest.TestCase):
    def test_merge_ties_and_preserve_order_with_tagged_events(self) -> None:
        flights = [flight(0), flight(2), replace(flight(2), latitude_deg=39), flight(4)]
        autopilots = [
            autopilot(1), autopilot(2),
            replace(autopilot(2), selected_altitude_ft=6000), autopilot(3),
        ]
        cursor = PairedJsonlPlayback(flights, autopilots)
        cursor.play()
        events = [cursor.next_sample() for _ in range(8)]
        self.assertEqual(
            [event.kind for event in events],
            ["telemetry", "autopilot", "telemetry", "telemetry",
             "autopilot", "autopilot", "autopilot", "telemetry"],
        )
        self.assertEqual([event.timestamp_utc for event in events], sorted(
            event.timestamp_utc for event in events
        ))
        self.assertIsInstance(events[0], TelemetryPlaybackEvent)
        self.assertEqual(events[0].state.source, StateSource.RECORDING)
        self.assertIsInstance(events[1], AutopilotPlaybackEvent)
        self.assertEqual(events[1].state, autopilots[0])
        self.assertEqual([events[2].state.latitude_deg, events[3].state.latitude_deg], [38, 39])
        self.assertEqual(
            [events[4].state.selected_altitude_ft, events[5].state.selected_altitude_ft],
            [5500, 6000],
        )

    def test_streams_are_parsed_and_first_autopilot_offset_is_preserved(self) -> None:
        flights, autopilots = StringIO(), StringIO()
        write_jsonl(flights, flight(3))
        write_autopilot_jsonl(autopilots, autopilot(0))
        flights.seek(0)
        autopilots.seek(0)
        cursor = PairedJsonlPlayback.from_streams(flights, autopilots)
        cursor.play()
        self.assertEqual(cursor.next_sample().kind, "autopilot")
        self.assertEqual(cursor.next_sample().timestamp_utc, ORIGIN + timedelta(seconds=3))

    def test_empty_and_unequal_streams_emit_all_records(self) -> None:
        cases = [
            ([], [], []),
            ([flight(1)], [], ["telemetry"]),
            ([], [autopilot(1)], ["autopilot"]),
            ([flight(0)], [autopilot(1), autopilot(2)], ["telemetry", "autopilot", "autopilot"]),
            ([flight(1), flight(2)], [autopilot(0)], ["autopilot", "telemetry", "telemetry"]),
        ]
        for flights, autopilots, expected in cases:
            with self.subTest(expected=expected):
                cursor = PairedJsonlPlayback(flights, autopilots)
                cursor.play()
                actual = []
                while (event := cursor.next_sample()) is not None:
                    actual.append(event.kind)
                self.assertEqual(actual, expected)
                self.assertEqual(cursor.status, PlaybackStatus.ENDED)

    def test_decreasing_timestamps_rejected_before_merge(self) -> None:
        with self.assertRaisesRegex(ValueError, "telemetry.*non-decreasing"):
            PairedJsonlPlayback([flight(2), flight(1)], [])
        with self.assertRaisesRegex(ValueError, "autopilot.*non-decreasing"):
            PairedJsonlPlayback([], [autopilot(2), autopilot(1)])

    def test_realtime_uses_one_origin_for_both_start_orders(self) -> None:
        for flights, autopilots in (
            ([flight(0)], [autopilot(0.06)]),
            ([flight(0.06)], [autopilot(0)]),
        ):
            with self.subTest(flights=flights, autopilots=autopilots):
                cursor = PairedJsonlPlayback(flights, autopilots)
                cursor.play()
                start = time.monotonic()
                first = cursor.next_sample_realtime()
                second = cursor.next_sample_realtime()
                elapsed = time.monotonic() - start
                self.assertNotEqual(first.kind, second.kind)
                self.assertGreaterEqual(elapsed, 0.045)
                self.assertLess(elapsed, 0.8)

    def test_pause_interrupt_resume_excludes_pause_and_stop_rewinds(self) -> None:
        cursor = PairedJsonlPlayback([flight(0)], [autopilot(0.18)])
        cursor.play()
        self.assertEqual(cursor.next_sample_realtime().kind, "telemetry")
        result = []
        worker = threading.Thread(target=lambda: result.append(cursor.next_sample_realtime()))
        worker.start()
        time.sleep(0.03)
        cursor.pause()
        worker.join(timeout=0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [None])
        self.assertEqual(cursor.position, 1)
        time.sleep(0.2)
        start = time.monotonic()
        cursor.play()
        self.assertEqual(cursor.next_sample_realtime().kind, "autopilot")
        self.assertGreaterEqual(time.monotonic() - start, 0.1)
        self.assertEqual(cursor.status, PlaybackStatus.ENDED)
        cursor.play()
        self.assertEqual(cursor.next_sample().kind, "telemetry")
        cursor.stop()
        self.assertEqual(cursor.position, 0)
        cursor.play()
        self.assertEqual(cursor.next_sample().kind, "telemetry")

    def test_stop_interrupts_realtime_wait_and_rewinds(self) -> None:
        cursor = PairedJsonlPlayback([flight(0)], [autopilot(5)])
        cursor.play()
        self.assertEqual(cursor.next_sample_realtime().kind, "telemetry")
        result = []
        worker = threading.Thread(target=lambda: result.append(cursor.next_sample_realtime()))
        worker.start()
        time.sleep(0.03)
        cursor.stop()
        worker.join(timeout=0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [None])
        self.assertEqual(cursor.status, PlaybackStatus.STOPPED)
        self.assertEqual(cursor.position, 0)


if __name__ == "__main__":
    unittest.main()
