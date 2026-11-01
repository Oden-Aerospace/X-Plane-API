"""Python API for X-Plane telemetry and test tooling."""

from xplane_api.autopilot import AutopilotPanel, ConfirmationResult
from xplane_api.models import (
	AfcsButton,
	AfcsSelector,
	AutopilotState,
	FieldStatus,
	FlightState,
	GroundTrackDeriver,
	LateralMode,
	StateSource,
	VerticalMode,
)
from xplane_api.recording import JsonlPlayback, PlaybackStatus, iter_jsonl, write_jsonl
from xplane_api.sources import (
	AutopilotStateAdapter,
	RrefClient,
	TimedDatarefValue,
	XPlaneBeacon,
	XPlaneControlClient,
	XPlaneStateAdapter,
	discover_xplane,
	parse_beacon,
)

__all__ = [
	"AfcsButton",
	"AfcsSelector",
	"AutopilotPanel",
	"AutopilotState",
	"AutopilotStateAdapter",
	"ConfirmationResult",
	"FieldStatus",
	"FlightState",
	"GroundTrackDeriver",
	"JsonlPlayback",
	"LateralMode",
	"PlaybackStatus",
	"RrefClient",
	"StateSource",
	"TimedDatarefValue",
	"VerticalMode",
	"XPlaneBeacon",
	"XPlaneControlClient",
	"XPlaneStateAdapter",
	"discover_xplane",
	"iter_jsonl",
	"parse_beacon",
	"write_jsonl",
]
