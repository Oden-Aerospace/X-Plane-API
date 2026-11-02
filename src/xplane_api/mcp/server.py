"""Autopilot MCP server: annunciated-state tools over stdio.

Requires the optional ``mcp`` extra (``pip install xplane-api[mcp]``). The
server is a thin facade over :class:`AutopilotPanel`: read tools are always
registered; control tools (``press_button``, ``set_selector``) only when the
server is launched with ``--allow-control``. A read-only launch constructs
no write-capable client, so it structurally cannot transmit control traffic.

This is simulation tooling for X-Plane 12, not certified avionics, and does
not model any specific real AFCS product.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Literal

try:
    from mcp.server.mcpserver import MCPServer
except ModuleNotFoundError as _mcp_import_error:  # pragma: no cover
    MCPServer = None  # type: ignore[assignment]
    _MCP_IMPORT_ERROR: ModuleNotFoundError | None = _mcp_import_error
else:
    _MCP_IMPORT_ERROR = None

from xplane_api.autopilot.panel import AutopilotPanel
from xplane_api.mcp.backend import (
    CONDITION_FIELDS,
    AutopilotToolBackend,
    ControlDisabled,
)
from xplane_api.models.autopilot import AfcsButton, AfcsSelector
from xplane_api.sources.autopilot import AutopilotStateAdapter
from xplane_api.sources.beacon import discover_xplane
from xplane_api.sources.control_client import XPlaneControlClient
from xplane_api.sources.flight_plan import FlightPlanClient
from xplane_api.sources.rref_client import RrefClient


BUTTON_VALUES = tuple(button.value for button in AfcsButton)
SELECTOR_VALUES = tuple(selector.value for selector in AfcsSelector)

_ButtonName = Literal[BUTTON_VALUES]  # type: ignore[valid-type]
_SelectorName = Literal[SELECTOR_VALUES]  # type: ignore[valid-type]

_CONTRACT = (
    "Inputs are requests over unacknowledged UDP: a successful result means "
    "only that the packet was transmitted. The ONLY truth is the annunciated "
    "state read back afterwards (read_autopilot_state or wait_for_state); a "
    "wait_for_state timeout means the request did not take effect — report "
    "the observed state, do not retry blindly. Fields marked stale or "
    "unavailable must never be treated as current data."
)

_SAFETY = (
    "Session safety: if you disengage the autopilot (AP, AP_DISC, FD, or GA "
    "can), you are responsible for the aircraft. Always end a control "
    "session by re-engaging and confirming the autopilot (press AP, then "
    "wait_for_state with ap_engaged=true plus your intended modes) and by "
    "confirming a stable vertical speed/altitude target. This is X-Plane 12 "
    "simulation tooling, not certified avionics."
)

_VERIFIED_CAVEATS = (
    "Verified on X-Plane 12.4.3 with the included C172 G1000: course writes "
    "are ineffective when the HSI source is GPS (course is slaved to the "
    "flight plan); NAV and BC need a usable nav source and VNV needs a VNAV "
    "flight plan to produce a transition; pressing a button whose mode is "
    "already active toggles it off."
)

INSTRUCTIONS = (
    "Tools for operating the X-Plane 12 autopilot like its mode-controller "
    "panel, plus pilot-side flight-plan read and explicitly gated direct-to. "
    "For an explicitly requested direct-to plus navigation activation, use "
    "direct_to_and_activate when it is available; direct_to_waypoint alone "
    "changes only the route. "
    + _CONTRACT
    + " "
    + _SAFETY
)


def build_server(
    backend: AutopilotToolBackend,
    *,
    allow_control: bool,
    allow_flight_plan_control: bool = False,
) -> "MCPServer":
    """Register tools for ``backend`` on a new MCP server instance."""
    if MCPServer is None:  # pragma: no cover
        raise SystemExit(
            "The MCP SDK is not installed. Install the optional extra: "
            "pip install 'xplane-api[mcp]'"
        )
    server = MCPServer(
        name="xplane-autopilot",
        instructions=INSTRUCTIONS,
    )

    @server.tool(
        description=(
            "Read the current annunciated autopilot state as JSON: AP/FD/YD "
            "engagement, active and armed lateral/vertical modes, selected "
            "targets (heading, altitude, vertical speed, airspeed, course), "
            "flight-director command bars, per-field status "
            "(valid/unavailable/stale/invalid), and raw simulator status "
            "values. timeout_seconds bounds this call's wait for fresh "
            "packets (1.0 is typical); stale_after_seconds is the maximum "
            "data age still reported as valid (2.0 is typical). "
            + _CONTRACT
        )
    )
    def read_autopilot_state(
        timeout_seconds: float, stale_after_seconds: float
    ) -> dict[str, Any]:
        return backend.read_state(
            timeout_seconds=timeout_seconds,
            stale_after_seconds=stale_after_seconds,
        )

    @server.tool(
        description=(
            "Read the pilot-side primary flight plan as ordered entries with "
            "identifier/type, position, SDK-reported altitude, active index, "
            "timestamp, and per-field availability. This operation is "
            "read-only; it does not change the simulator's route."
        )
    )
    def read_flight_plan() -> dict[str, Any]:
        return backend.read_flight_plan()

    if allow_flight_plan_control:

        @server.tool(
            description=(
                "Request direct-to a unique navigation-database identifier "
                "on the pilot-side primary plan. This changes the simulator's "
                "navigation route and may change the aircraft path when NAV/"
                "GPSS is coupled; it does not change autopilot modes. The "
                "result is returned only after a plan read-back confirms the "
                "requested target is active. Use only for an explicitly "
                "requested route change."
            )
        )
        def direct_to_waypoint(identifier: str) -> dict[str, Any]:
            return backend.direct_to_waypoint(identifier)

    if allow_control and allow_flight_plan_control:

        @server.tool(
            description=(
                "For an explicitly requested direct-to with autopilot "
                "navigation activation, confirm the route target first, then "
                "engage AP only if it is not already engaged and select NAV "
                "only if neither NAV nor GPSS is active or armed. Returns "
                "confirmed only after read-back verifies the requested target "
                "is active in the flight plan, AP servos are engaged, and "
                "NAV or GPSS is active. If the route is confirmed but "
                "activation is not, returns status=partial_failure with the "
                "final observed state. Inputs are never retried blindly. "
                "This does not change vertical modes or targets. Requires "
                "both autopilot and flight-plan control opt-ins. "
                + _SAFETY
            )
        )
        def direct_to_and_activate(
            identifier: str,
            timeout_seconds: float,
            poll_interval_seconds: float,
            stale_after_seconds: float,
        ) -> dict[str, Any]:
            return backend.direct_to_and_activate(
                identifier,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
                stale_after_seconds=stale_after_seconds,
            )

    @server.tool(
        description=(
            "Poll annunciated autopilot state until ALL expectations hold or "
            "timeout_seconds elapses; returns {satisfied, state} either way "
            "— on timeout, state shows what the autopilot actually "
            "annunciated. Supported expectation fields: "
            + ", ".join(CONDITION_FIELDS)
            + ". Mode fields take mode-name strings (e.g. 'HDG', 'ALT', "
            "'ALTS', 'NONE'); engagement fields take booleans; selected_* "
            "fields take numbers and require numeric_tolerance. "
            "poll_interval_seconds of 0.25 and stale_after_seconds of 2.0 "
            "are typical. This tool only reads; it sends no input. "
            + _CONTRACT
        )
    )
    def wait_for_state(
        expected: dict[str, Any],
        timeout_seconds: float,
        poll_interval_seconds: float,
        stale_after_seconds: float,
        numeric_tolerance: float | None = None,
    ) -> dict[str, Any]:
        return backend.wait_for_state(
            expected=expected,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            stale_after_seconds=stale_after_seconds,
            numeric_tolerance=numeric_tolerance,
        )

    if allow_control:

        @server.tool(
            description=(
                "Press one autopilot mode-controller button once (momentary "
                "press, like the physical panel). AP toggles the servos; "
                "AP_DISC disconnects them deterministically and leaves the "
                "flight director on. The result reports transmission only — "
                "confirm the outcome with wait_for_state. "
                + _VERIFIED_CAVEATS
                + " "
                + _SAFETY
            )
        )
        def press_button(button: _ButtonName) -> dict[str, Any]:
            return backend.press_button(button)

        @server.tool(
            description=(
                "Turn one autopilot target selector to a value. Units and "
                "accepted ranges: HEADING degrees magnetic [0, 360); "
                "ALTITUDE feet [-1000, 60000]; VERTICAL_SPEED feet/minute "
                "[-8000, 8000]; AIRSPEED knots [0, 400]; COURSE degrees "
                "magnetic [0, 360). Out-of-range or non-finite values are "
                "rejected without transmitting; values are never clamped. "
                "Confirm the simulator applied the value by reading state "
                "back. " + _VERIFIED_CAVEATS
            )
        )
        def set_selector(selector: _SelectorName, value: float) -> dict[str, Any]:
            return backend.set_selector(selector, value)

    return server


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="xplane-api-mcp",
        description=(
            "Autopilot MCP server for X-Plane 12 (stdio transport). "
            "Read-only unless --allow-control is given."
        ),
    )
    parser.add_argument("--host", help="Simulator UDP host")
    parser.add_argument("--port", type=int, help="Simulator UDP port")
    parser.add_argument(
        "--discover-timeout-seconds",
        type=float,
        help="Discover the simulator via its beacon within this timeout",
    )
    parser.add_argument(
        "--frequency-hz",
        type=int,
        required=True,
        help="Autopilot dataref subscription frequency (explicit; no default)",
    )
    parser.add_argument(
        "--allow-control",
        action="store_true",
        help=(
            "Register control tools (press_button, set_selector) and "
            "construct the write-capable client. Off by default."
        ),
    )
    parser.add_argument(
        "--allow-flight-plan-control",
        action="store_true",
        help=(
            "Register direct_to_waypoint and enable its separate flight-plan "
            "write client; off by default."
        ),
    )
    args = parser.parse_args(argv)

    direct = args.host is not None or args.port is not None
    if direct and (args.host is None or args.port is None):
        parser.error("--host and --port must be provided together")
    if direct and args.discover_timeout_seconds is not None:
        parser.error("use either --host/--port or --discover-timeout-seconds")
    if not direct and args.discover_timeout_seconds is None:
        parser.error(
            "provide --host/--port or --discover-timeout-seconds for beacon "
            "discovery"
        )
    return args


def main(argv: list[str] | None = None) -> None:
    """Connect to the simulator, then serve autopilot tools over stdio."""
    if MCPServer is None:
        raise SystemExit(
            "The MCP SDK is not installed. Install the optional extra: "
            "pip install 'xplane-api[mcp]'"
            + (f" (import error: {_MCP_IMPORT_ERROR})" if _MCP_IMPORT_ERROR else "")
        )
    args = _parse_args(argv)

    if args.host is not None:
        host, port = args.host, args.port
    else:
        beacon = discover_xplane(timeout_seconds=args.discover_timeout_seconds)
        host, port = beacon.host, beacon.port
        print(
            f"discovered X-Plane {beacon.version} at {host}:{port}",
            file=sys.stderr,
        )

    adapter = AutopilotStateAdapter(
        RrefClient(host, port), frequency_hz=args.frequency_hz
    )
    if args.allow_control:
        control: Any = XPlaneControlClient(host, port)
    else:
        control = ControlDisabled()
    panel = AutopilotPanel(adapter, control)
    backend = AutopilotToolBackend(
        panel,
        flight_plan_client=FlightPlanClient(
            allow_control=args.allow_flight_plan_control
        ),
    )
    server = build_server(
        backend,
        allow_control=args.allow_control,
        allow_flight_plan_control=args.allow_flight_plan_control,
    )
    mode = "control-enabled" if args.allow_control else "read-only"
    if args.allow_flight_plan_control:
        mode += ", flight-plan-control-enabled"
    print(f"xplane-autopilot MCP server starting ({mode}, stdio)", file=sys.stderr)
    try:
        server.run("stdio")
    finally:
        panel.close()


if __name__ == "__main__":
    main()
