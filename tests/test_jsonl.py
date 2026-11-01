from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import threading
import time
import unittest

from xplane_api.models.state import FlightState, StateSource
from xplane_api.recording.jsonl import (
    JsonlPlayback,
    PlaybackStatus,
    iter_jsonl,
    write_jsonl,
)


def make_sample(second: int, source: StateSource = StateSource.TEST) -> FlightState:
    return FlightState(
        timestamp_utc=datetime(2026, 10, 13, tzinfo=timezone.utc)
        + timedelta(seconds=second),
        source=source,
        sequence=second,
        latitude_deg=38.0 + second / 10,
    )


class JsonlRecordingTests(unittest.TestCase):
    def test_write_and_read_round_trip_with_blank_lines(self) -> None:
        stream = StringIO()
        first = make_sample(0, StateSource.LIVE_XPLANE)
        second = make_sample(1, StateSource.LIVE_XPLANE)
        write_jsonl(stream, first)
        write_jsonl(stream, second)

        content = stream.getvalue()
        self.assertEqual(len(content.strip().splitlines()), 2)
        restored = list(iter_jsonl(StringIO("\n" + content + "  \n")))
        self.assertEqual(restored, [first, second])

    def test_writer_emits_one_json_object_per_line(self) -> None:
        stream = StringIO()
        write_jsonl(stream, make_sample(0))
        record = json.loads(stream.getvalue())
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["latitude_deg"], 38.0)

    def test_reports_line_number_for_invalid_json_or_schema(self) -> None:
        with self.assertRaisesRegex(ValueError, "line 2"):
            list(iter_jsonl(StringIO('\n{"schema_version":99}\n')))
        with self.assertRaisesRegex(ValueError, "line 1"):
            list(iter_jsonl(StringIO("not-json\n")))


class JsonlPlaybackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = [make_sample(0, StateSource.LIVE_XPLANE), make_sample(2)]
        stream = StringIO()
        for sample in self.original:
            write_jsonl(stream, sample)
        stream.seek(0)
        self.playback = JsonlPlayback.from_stream(stream)

    def test_play_emits_in_order_and_relabels_source(self) -> None:
        self.assertEqual(self.playback.status, PlaybackStatus.STOPPED)
        self.assertIsNone(self.playback.next_sample())
        self.playback.play()
        first = self.playback.next_sample()
        second = self.playback.next_sample()
        self.assertEqual(first.sequence, 0)
        self.assertEqual(second.sequence, 2)
        self.assertEqual(first.source, StateSource.RECORDING)
        self.assertEqual(second.timestamp_utc, self.original[1].timestamp_utc)
        self.assertEqual(self.playback.status, PlaybackStatus.ENDED)
        self.assertIsNone(self.playback.next_sample())

    def test_pause_preserves_position_and_resume_continues(self) -> None:
        self.playback.play()
        first = self.playback.next_sample()
        self.playback.pause()
        self.assertEqual(self.playback.status, PlaybackStatus.PAUSED)
        self.assertIsNone(self.playback.next_sample())
        self.playback.play()
        second = self.playback.next_sample()
        self.assertEqual(first.sequence, 0)
        self.assertEqual(second.sequence, 2)

    def test_stop_rewinds_and_play_after_end_restarts(self) -> None:
        self.playback.play()
        self.playback.next_sample()
        self.playback.stop()
        self.assertEqual(self.playback.position, 0)
        self.assertEqual(self.playback.status, PlaybackStatus.STOPPED)
        self.playback.play()
        self.assertEqual(self.playback.next_sample().sequence, 0)

        self.playback.play()
        self.playback.next_sample()
        self.playback.next_sample()
        self.assertEqual(self.playback.status, PlaybackStatus.ENDED)
        self.playback.play()
        self.assertEqual(self.playback.position, 0)
        self.assertEqual(self.playback.next_sample().sequence, 0)

    def test_realtime_cursor_honors_recorded_interval(self) -> None:
        samples = [make_sample(0), make_sample(0), make_sample(0)]
        samples[1] = FlightState(
            timestamp_utc=samples[0].timestamp_utc + timedelta(milliseconds=40),
            sequence=1,
        )
        samples[2] = FlightState(
            timestamp_utc=samples[0].timestamp_utc + timedelta(milliseconds=80),
            sequence=2,
        )
        playback = JsonlPlayback(samples)
        playback.play()
        start = time.monotonic()
        self.assertEqual(playback.next_sample_realtime().sequence, 0)
        self.assertEqual(playback.next_sample_realtime().sequence, 1)
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.025)
        self.assertLess(elapsed, 0.5)

    def test_realtime_wait_is_interruptible_by_pause(self) -> None:
        first = make_sample(0)
        second = FlightState(
            timestamp_utc=first.timestamp_utc + timedelta(seconds=5), sequence=1
        )
        playback = JsonlPlayback([first, second])
        playback.play()
        self.assertEqual(playback.next_sample_realtime().sequence, 0)

        result: list[FlightState | None] = []
        worker = threading.Thread(
            target=lambda: result.append(playback.next_sample_realtime())
        )
        worker.start()
        time.sleep(0.03)
        playback.pause()
        worker.join(timeout=0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [None])
        self.assertEqual(playback.status, PlaybackStatus.PAUSED)
        self.assertEqual(playback.position, 1)


if __name__ == "__main__":
    unittest.main()
