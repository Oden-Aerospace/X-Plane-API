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
    "panel. " + _CONTRACT + " " + _SAFETY
)


def build_server(
    backend: AutopilotToolBackend, *, allow_control: bool
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
    backend = AutopilotToolBackend(panel)
    server = build_server(backend, allow_control=args.allow_control)
    mode = "control-enabled" if args.allow_control else "read-only"
    print(f"xplane-autopilot MCP server starting ({mode}, stdio)", file=sys.stderr)
    try:
        server.run("stdio")
    finally:
        panel.close()


if __name__ == "__main__":
    main()
