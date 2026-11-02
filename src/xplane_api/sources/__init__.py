"""Live and recorded flight-state sources."""

from xplane_api.sources.beacon import XPlaneBeacon, discover_xplane, parse_beacon
from xplane_api.sources.rref_client import RrefClient, TimedDatarefValue
from xplane_api.sources.autopilot import AutopilotStateAdapter
from xplane_api.sources.control_client import XPlaneControlClient
from xplane_api.sources.xplane import XPlaneStateAdapter
from xplane_api.sources.flight_plan import (
    FlightPlanClient,
    FlightPlanCommandError,
    FlightPlanControlDisabledError,
    FlightPlanError,
    FlightPlanProtocolError,
    FlightPlanReadbackError,
    FlightPlanStateError,
    FlightPlanTargetError,
    FlightPlanUnavailableError,
)

__all__ = [
	"AutopilotStateAdapter",
	"FlightPlanClient",
	"FlightPlanCommandError",
	"FlightPlanControlDisabledError",
	"FlightPlanError",
	"FlightPlanProtocolError",
	"FlightPlanReadbackError",
	"FlightPlanStateError",
	"FlightPlanTargetError",
	"FlightPlanUnavailableError",
	"RrefClient",
	"TimedDatarefValue",
	"XPlaneBeacon",
	"XPlaneControlClient",
	"discover_xplane",
	"parse_beacon",
	"XPlaneStateAdapter",
]
