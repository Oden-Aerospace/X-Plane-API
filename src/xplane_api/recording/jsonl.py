"""Versioned JSON Lines recording reader/writer and pull-driven playback."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
import json
import threading
import time
from typing import Callable, ClassVar, Generic, Iterable, Iterator, Literal, Protocol, TextIO, TypeVar

from xplane_api.models.autopilot import AutopilotState
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


def iter_autopilot_jsonl(stream: TextIO) -> Iterator[AutopilotState]:
    """Yield autopilot records, reporting malformed input with its line number."""
    for line_number, line in enumerate(stream, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, Mapping):
                raise TypeError("autopilot record must be an object")
            if not isinstance(record.get("field_status", {}), Mapping):
                raise TypeError("field_status must be an object")
            yield AutopilotState.from_json_dict(record)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid autopilot JSONL record at line {line_number}: {exc}"
            ) from exc


def write_autopilot_jsonl(stream: TextIO, state: AutopilotState) -> None:
    """Write one complete autopilot snapshot as a JSON Lines record."""
    try:
        line = json.dumps(state.to_json_dict(), separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"autopilot state cannot be represented as JSON: {exc}") from exc
    stream.write(line)
    stream.write("\n")


class _Timestamped(Protocol):
    @property
    def timestamp_utc(self) -> datetime: ...


_Sample = TypeVar("_Sample", bound=_Timestamped)


class _PlaybackCursor(Generic[_Sample]):
    """Shared timing and cursor mechanics for timestamped samples.

    ``next_sample`` is a non-blocking cursor. ``next_sample_realtime`` honors
    recorded timestamp intervals at real-time 1x, and can be interrupted by
    pause/stop from another thread. Playback requires non-decreasing sample
    timestamps. Paused wall time does not shift the recording timeline.
    """

    def __init__(
        self,
        samples: Iterable[_Sample],
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

    def next_sample(self) -> _Sample | None:
        """Return the next sample when playing, otherwise return ``None``."""
        with self._condition:
            if self._status is not PlaybackStatus.PLAYING:
                return None
            return self._take_next_locked()

    def next_sample_realtime(self) -> _Sample | None:
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

    def _take_next_locked(self) -> _Sample | None:
        if self._position >= len(self._samples):
            self._status = PlaybackStatus.ENDED
            self._condition.notify_all()
            return None
        sample = self._samples[self._position]
        self._position += 1
        if self._position >= len(self._samples):
            self._status = PlaybackStatus.ENDED
            self._condition.notify_all()
        return self._prepare_sample(sample)

    def _prepare_sample(self, sample: _Sample) -> _Sample:
        return sample


class JsonlPlayback(_PlaybackCursor[FlightState]):
    """Thread-safe playback cursor over versioned telemetry JSON Lines samples."""

    @classmethod
    def from_stream(cls, stream: TextIO) -> JsonlPlayback:
        """Load a playback cursor from a JSON Lines stream."""
        return cls(iter_jsonl(stream))

    def _prepare_sample(self, sample: FlightState) -> FlightState:
        return replace(sample, source=StateSource.RECORDING)


class AutopilotJsonlPlayback(_PlaybackCursor[AutopilotState]):
    """Thread-safe playback cursor over autopilot snapshots."""

    @classmethod
    def from_stream(cls, stream: TextIO) -> AutopilotJsonlPlayback:
        """Load a playback cursor from an autopilot JSON Lines stream."""
        return cls(iter_autopilot_jsonl(stream))


@dataclass(frozen=True)
class TelemetryPlaybackEvent:
    """One telemetry sample in a paired playback."""

    state: FlightState
    kind: ClassVar[Literal["telemetry"]] = "telemetry"

    @property
    def timestamp_utc(self) -> datetime:
        return self.state.timestamp_utc


@dataclass(frozen=True)
class AutopilotPlaybackEvent:
    """One autopilot sample in a paired playback."""

    state: AutopilotState
    kind: ClassVar[Literal["autopilot"]] = "autopilot"

    @property
    def timestamp_utc(self) -> datetime:
        return self.state.timestamp_utc


PairedPlaybackEvent = TelemetryPlaybackEvent | AutopilotPlaybackEvent


class PairedJsonlPlayback(_PlaybackCursor[PairedPlaybackEvent]):
    """Replay two caller-paired streams on one recorded UTC timeline."""

    def __init__(
        self,
        telemetry: Iterable[FlightState],
        autopilot: Iterable[AutopilotState],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        flight_samples = tuple(telemetry)
        autopilot_samples = tuple(autopilot)
        for name, samples in (("telemetry", flight_samples), ("autopilot", autopilot_samples)):
            if any(
                current.timestamp_utc < previous.timestamp_utc
                for previous, current in zip(samples, samples[1:])
            ):
                raise ValueError(f"{name} recording timestamps must be non-decreasing")
        events: list[PairedPlaybackEvent] = [
            TelemetryPlaybackEvent(sample) for sample in flight_samples
        ]
        events.extend(AutopilotPlaybackEvent(sample) for sample in autopilot_samples)
        events.sort(key=lambda event: event.timestamp_utc)
        super().__init__(events, clock=clock)

    @classmethod
    def from_streams(cls, telemetry: TextIO, autopilot: TextIO) -> PairedJsonlPlayback:
        """Load explicitly paired telemetry and autopilot JSON Lines streams."""
        return cls(iter_jsonl(telemetry), iter_autopilot_jsonl(autopilot))

    def _prepare_sample(self, sample: PairedPlaybackEvent) -> PairedPlaybackEvent:
        if isinstance(sample, TelemetryPlaybackEvent):
            return TelemetryPlaybackEvent(replace(sample.state, source=StateSource.RECORDING))
        return sample
