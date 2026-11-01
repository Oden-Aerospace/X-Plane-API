"""Server-level tests; skipped when the optional MCP SDK is absent."""

import asyncio
import importlib.util
import unittest
from datetime import datetime, timezone

_HAS_MCP = importlib.util.find_spec("mcp") is not None

if _HAS_MCP:
    from xplane_api.mcp.server import (
        BUTTON_VALUES,
        SELECTOR_VALUES,
        build_server,
    )

from xplane_api.mcp.backend import AutopilotToolBackend
from xplane_api.models.autopilot import AfcsButton, AfcsSelector, AutopilotState


def _state(**kwargs) -> AutopilotState:
    return AutopilotState(
        timestamp_utc=datetime(2026, 11, 1, tzinfo=timezone.utc), **kwargs
    )


class FakePanel:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    def read_state(self, *, timeout_seconds, stale_after_seconds):
        return _state(ap_engaged=True)

    def press_button(self, button):
        AfcsButton(button)
        self.pressed.append(str(button))

    def set_selector(self, selector, value):
        AfcsSelector(selector)

    def wait_for(self, predicate, **kwargs):
        from xplane_api.autopilot.panel import ConfirmationResult

        state = _state(ap_engaged=True)
        return ConfirmationResult(satisfied=predicate(state), state=state)


@unittest.skipUnless(_HAS_MCP, "mcp extra not installed")
class ServerRegistryTests(unittest.TestCase):
    def _tools(self, *, allow_control):
        backend = AutopilotToolBackend(FakePanel())  # type: ignore[arg-type]
        server = build_server(backend, allow_control=allow_control)
        return server, asyncio.run(server.list_tools())

    def test_read_only_registers_only_read_tools(self) -> None:
        _, tools = self._tools(allow_control=False)
        names = {tool.name for tool in tools}
        self.assertEqual(names, {"read_autopilot_state", "wait_for_state"})

    def test_control_mode_registers_all_four_tools(self) -> None:
        _, tools = self._tools(allow_control=True)
        names = {tool.name for tool in tools}
        self.assertEqual(
            names,
            {
                "read_autopilot_state",
                "wait_for_state",
                "press_button",
                "set_selector",
            },
        )

    def test_button_schema_enum_matches_library_enum(self) -> None:
        self.assertEqual(set(BUTTON_VALUES), {b.value for b in AfcsButton})
        self.assertEqual(set(SELECTOR_VALUES), {s.value for s in AfcsSelector})
        _, tools = self._tools(allow_control=True)
        press = next(tool for tool in tools if tool.name == "press_button")
        schema_enum = press.input_schema["properties"]["button"]["enum"]
        self.assertEqual(set(schema_enum), {b.value for b in AfcsButton})
        select = next(tool for tool in tools if tool.name == "set_selector")
        selector_enum = select.input_schema["properties"]["selector"]["enum"]
        self.assertEqual(set(selector_enum), {s.value for s in AfcsSelector})

    def test_tool_descriptions_carry_contract_and_safety(self) -> None:
        _, tools = self._tools(allow_control=True)
        by_name = {tool.name: tool.description or "" for tool in tools}
        for name in ("read_autopilot_state", "wait_for_state"):
            self.assertIn("annunciated", by_name[name])
        self.assertIn("Session safety", by_name["press_button"])
        self.assertIn("never clamped", by_name["set_selector"])

    def test_call_read_tool_returns_state_json(self) -> None:
        server, _ = self._tools(allow_control=False)
        result = asyncio.run(
            server.call_tool(
                "read_autopilot_state",
                {"timeout_seconds": 0.0, "stale_after_seconds": 2.0},
            )
        )
        self.assertFalse(result.is_error)
        self.assertIn('"ap_engaged": true', result.content[0].text)

    def test_invalid_button_is_a_structured_tool_error(self) -> None:
        # In-process call_tool raises ToolError; over the wire the transport
        # layer converts it into an error tool result for the client.
        from mcp.server.mcpserver.exceptions import ToolError

        server, _ = self._tools(allow_control=True)
        with self.assertRaises(ToolError):
            asyncio.run(server.call_tool("press_button", {"button": "WARP"}))

    def test_wait_for_state_round_trips(self) -> None:
        server, _ = self._tools(allow_control=False)
        result = asyncio.run(
            server.call_tool(
                "wait_for_state",
                {
                    "expected": {"ap_engaged": True},
                    "timeout_seconds": 0.5,
                    "poll_interval_seconds": 0.1,
                    "stale_after_seconds": 2.0,
                },
            )
        )
        self.assertFalse(result.is_error)
        self.assertIn('"satisfied": true', result.content[0].text)


class CoreIndependenceTests(unittest.TestCase):
    def test_core_package_never_imports_mcp_sdk(self) -> None:
        import pathlib

        src = pathlib.Path(__file__).resolve().parent.parent / "src" / "xplane_api"
        offenders = []
        for path in src.rglob("*.py"):
            if path.parts[-2] == "mcp" and path.name == "server.py":
                continue  # the one guarded import site
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("import mcp", "from mcp")):
                    offenders.append(str(path))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
