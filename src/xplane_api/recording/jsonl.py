"""Versioned JSON Lines recording reader/writer and pull-driven playback."""

from __future__ import annotations

from dataclasses import replace
from enum import Enum
import json
import threading
import time
from typing import Callable, Iterable, Iterator, TextIO

from xplane_api.models.state import FlightState, StateSource


class PlaybackStatus(str, Enum):
    """Playback cursor state."""

    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"
    ENDED = "ended"


def iter_jsonl(stream: TextIO) -> Iterator[FlightState]:
    """Yield versioned flight-state records from a UTF-8 JSON Lines stream.

    Blank lines are ignored. Parse and schema errors include the one-based line
    number. Each non-blank line must contain one complete schema-v1 sample.
    """
    for line_number, line in enumerate(stream, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            yield FlightState.from_dict(record)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid JSONL state record at line {line_number}: {exc}") from exc


def write_jsonl(stream: TextIO, state: FlightState) -> None:
    """Write one compact, versioned state object followed by a newline."""
    try:
        line = json.dumps(state.to_dict(), separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"state cannot be represented as JSON: {exc}") from exc
    stream.write(line)
    stream.write("\n")


class JsonlPlayback:
    """Thread-safe playback cursor over versioned JSON Lines samples.

    ``next_sample`` is a non-blocking cursor. ``next_sample_realtime`` honors
    recorded timestamp intervals at real-time 1x, and can be interrupted by
    pause/stop from another thread. Playback requires non-decreasing sample
    timestamps. Paused wall time does not shift the recording timeline.
    """

    def __init__(
        self,
        samples: Iterable[FlightState],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._samples = tuple(samples)
        self._position = 0
        self._status = PlaybackStatus.STOPPED
        self._clock = clock
        self._condition = threading.Condition()
        self._play_started_at: float | None = None
        self._paused_at: float | None = None
        self._total_paused_seconds = 0.0
        if any(
            current.timestamp_utc < previous.timestamp_utc
            for previous, current in zip(self._samples, self._samples[1:])
        ):
            raise ValueError("recording timestamps must be non-decreasing")

    @classmethod
    def from_stream(cls, stream: TextIO) -> JsonlPlayback:
        """Load a playback cursor from a JSON Lines stream."""
        return cls(iter_jsonl(stream))

    @property
    def status(self) -> PlaybackStatus:
        with self._condition:
            return self._status

    @property
    def position(self) -> int:
        """Zero-based index of the next sample, or the sample count at EOF."""
        with self._condition:
            return self._position

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    def play(self) -> None:
        """Start or resume playback. Replaying after EOF starts from the start."""
        with self._condition:
            now = self._clock()
            if self._status in (PlaybackStatus.STOPPED, PlaybackStatus.ENDED):
                self._position = 0
                self._play_started_at = now
                self._paused_at = None
                self._total_paused_seconds = 0.0
            elif self._status is PlaybackStatus.PAUSED and self._paused_at is not None:
                self._total_paused_seconds += now - self._paused_at
                self._paused_at = None
            self._status = PlaybackStatus.PLAYING
            self._condition.notify_all()

    def pause(self) -> None:
        """Pause without changing the next sample position."""
        with self._condition:
            if self._status is PlaybackStatus.PLAYING:
                self._paused_at = self._clock()
                self._status = PlaybackStatus.PAUSED
                self._condition.notify_all()

    def stop(self) -> None:
        """Stop playback and rewind to the first sample."""
        with self._condition:
            self._status = PlaybackStatus.STOPPED
            self._position = 0
            self._play_started_at = None
            self._paused_at = None
            self._total_paused_seconds = 0.0
            self._condition.notify_all()

    def next_sample(self) -> FlightState | None:
        """Return the next sample when playing, otherwise return ``None``."""
        with self._condition:
            if self._status is not PlaybackStatus.PLAYING:
                return None
            return self._take_next_locked()

    def next_sample_realtime(self) -> FlightState | None:
        """Wait until the next sample's recorded time at real-time 1x."""
        with self._condition:
            while True:
                if self._status is not PlaybackStatus.PLAYING:
                    return None
                if self._position >= len(self._samples):
                    self._status = PlaybackStatus.ENDED
                    self._condition.notify_all()
                    return None
                if self._play_started_at is None:
                    self._play_started_at = self._clock()

                recorded_offset = (
                    self._samples[self._position].timestamp_utc
                    - self._samples[0].timestamp_utc
                ).total_seconds()
                due_at = (
                    self._play_started_at
                    + self._total_paused_seconds
                    + recorded_offset
                )
                remaining = due_at - self._clock()
                if remaining <= 0:
                    return self._take_next_locked()
                self._condition.wait(timeout=remaining)

    def _take_next_locked(self) -> FlightState | None:
        if self._position >= len(self._samples):
            self._status = PlaybackStatus.ENDED
            self._condition.notify_all()
            return None
        sample = self._samples[self._position]
        self._position += 1
        if self._position >= len(self._samples):
            self._status = PlaybackStatus.ENDED
            self._condition.notify_all()
        return replace(sample, source=StateSource.RECORDING)
