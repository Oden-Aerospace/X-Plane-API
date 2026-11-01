from datetime import datetime, timezone
from http.client import HTTPConnection
from io import StringIO
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from xplane_api.autopilot.panel import AutopilotPanel
from xplane_api.models.autopilot import AutopilotState, LateralMode, VerticalMode
from xplane_api.models.state import FlightState, StateSource
from xplane_api.recording.jsonl import write_autopilot_jsonl, write_jsonl
from xplane_api.web import HarnessController, create_server


def _flight_sample() -> FlightState:
    return FlightState(
        timestamp_utc=datetime(2026, 11, 1, tzinfo=timezone.utc),
        source=StateSource.LIVE_XPLANE,
        sequence=1,
        latitude_deg=38.0,
    )


def _autopilot_sample() -> AutopilotState:
    return AutopilotState(
        timestamp_utc=datetime(2026, 11, 1, tzinfo=timezone.utc),
        ap_engaged=True,
        flight_director_on=True,
        yaw_damper_on=True,
        lateral_mode_active=LateralMode.HDG,
        vertical_mode_active=VerticalMode.VS,
        selected_vertical_speed_fpm=500,
    )


def _telemetry_jsonl() -> str:
    stream = StringIO()
    write_jsonl(stream, _flight_sample())
    return stream.getvalue()


def _autopilot_jsonl() -> str:
    stream = StringIO()
    write_autopilot_jsonl(stream, _autopilot_sample())
    return stream.getvalue()


class _FakePanelAdapter:
    def __init__(self) -> None:
        self.read_arguments = []

    def read_state(
        self, *, timeout_seconds: float, stale_after_seconds: float
    ) -> AutopilotState:
        self.read_arguments.append((timeout_seconds, stale_after_seconds))
        return _autopilot_sample()


class AutopilotHarnessHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._source_config_directory = tempfile.TemporaryDirectory()
        self.source_config_path = Path(self._source_config_directory.name) / "live-source.json"
        self.server = create_server(
            "127.0.0.1", 0, source_config_path=self.source_config_path
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.connection = HTTPConnection(host, port, timeout=2)

    def tearDown(self) -> None:
        self.server.controller.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.connection.close()
        self._source_config_directory.cleanup()

    def request(
        self, method: str, path: str, payload: dict | bytes | None = None
    ) -> tuple[int, bytes]:
        if isinstance(payload, dict):
            body = json.dumps(payload).encode()
            headers = {"Content-Type": "application/json"}
        else:
            body = payload or b""
            headers = {}
        self.connection.request(method, path, body=body, headers=headers)
        response = self.connection.getresponse()
        return response.status, response.read()

    def test_api_exposes_autopilot_and_control_state_and_default_is_read_only(self) -> None:
        status, body = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIsNone(payload["autopilot_state"])
        self.assertFalse(payload["control_available"])
        for path in ("/api/autopilot/button", "/api/autopilot/selector"):
            status, body = self.request("POST", path, {})
            self.assertEqual(status, 403)
            self.assertIn(b"allow-autopilot-control", body)

    def test_autopilot_page_and_display_routes_are_served(self) -> None:
        status, body = self.request("GET", "/autopilot")
        self.assertEqual(status, 200)
        self.assertIn(b'data-page="autopilot"', body)
        self.assertNotIn(b'data-control-opt-in="true"', body)
        for path, marker in (("/glass", b'data-display="glass"'), ("/sixpack", b'data-display="sixpack"')):
            with self.subTest(path=path):
                status, body = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertIn(marker, body)

    def test_disabled_controls_cannot_be_enabled_by_browser_requests(self) -> None:
        status, body = self.request(
            "POST", "/api/autopilot/button", {"button": "AP"}
        )
        self.assertEqual(status, 403)
        self.assertIn(b"disabled", body)

    def test_control_opt_in_without_live_source_is_still_unavailable(self) -> None:
        self.server.controller._allow_autopilot_control = True
        status, body = self.request("GET", "/autopilot")
        self.assertEqual(status, 200)
        self.assertIn(b'data-control-opt-in="true"', body)
        status, body = self.request(
            "POST", "/api/autopilot/button", {"button": "AP"}
        )
        self.assertEqual(status, 409)
        self.assertIn(b"connected live source", body)

    def test_control_routes_delegate_and_reject_invalid_selectors_before_send(self) -> None:
        self.server.controller._allow_autopilot_control = True
        session = self.server.controller._new_session(
            "live", status="connected", message="test source"
        )
        control = _FakeControlClient()
        session["autopilot_panel"] = AutopilotPanel(_FakePanelAdapter(), control)

        status, body = self.request(
            "POST", "/api/autopilot/button", {"button": "HDG"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "request_sent")
        self.assertEqual(control.commands, ["sim/autopilot/heading"])

        status, body = self.request(
            "POST", "/api/autopilot/selector",
            {"selector": "HEADING", "value": 270},
        )
        self.assertEqual(status, 200)
        self.assertEqual(control.datarefs[0][1], 270)
        status, body = self.request(
            "POST", "/api/autopilot/selector",
            {"selector": "HEADING", "value": 360},
        )
        self.assertEqual(status, 400)
        self.assertEqual(len(control.datarefs), 1)

    def test_pairing_errors_do_not_replace_current_session(self) -> None:
        status, _ = self.request(
            "POST", "/api/recording", _telemetry_jsonl().encode()
        )
        self.assertEqual(status, 200)
        session = self.server.controller._session
        status, body = self.request("POST", "/api/paired-recording", {
            "telemetry_jsonl": _telemetry_jsonl(),
            "autopilot_jsonl": '{"schema_version":99}\n',
        })
        self.assertEqual(status, 400)
        self.assertIn(b"line 1", body)
        self.assertIs(self.server.controller._session, session)

    def test_paired_recording_publishes_autopilot_readback(self) -> None:
        published = threading.Event()
        controller = self.server.controller
        original_publish = controller._publish

        def publish(session, state, autopilot_state=None, *, update_autopilot=False):
            original_publish(
                session, state, autopilot_state, update_autopilot=update_autopilot
            )
            if update_autopilot and autopilot_state is not None:
                published.set()

        with patch.object(controller, "_publish", side_effect=publish):
            status, body = self.request("POST", "/api/paired-recording", {
                "telemetry_jsonl": _telemetry_jsonl(),
                "autopilot_jsonl": _autopilot_jsonl(),
            })
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["sample_count"], 2)
            status, _ = self.request("POST", "/api/playback", {"action": "play"})
            self.assertEqual(status, 200)
            self.assertTrue(published.wait(timeout=1))
        status, body = self.request("GET", "/api/state")
        snapshot = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(snapshot["state"]["sequence"], 1)
        self.assertEqual(snapshot["autopilot_state"]["lateral_mode_active"], "HDG")
        self.assertFalse(snapshot["control_available"])

    def test_live_settings_persist_and_failed_save_preserves_previous_settings(self) -> None:
        status, body = self.request("GET", "/api/live/config")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"settings": None, "error": None})

        with patch("xplane_api.web.discover_xplane", side_effect=OSError("offline")):
            status, body = self.request(
                "POST",
                "/api/live",
                {"frequency_hz": 5, "stale_after_seconds": 1.75},
            )
            self.assertEqual(status, 202)
            deadline = time.monotonic() + 1
            while (
                self.server.controller.snapshot()["status"] != "error"
                and time.monotonic() < deadline
            ):
                time.sleep(0.005)

        saved = self.source_config_path.read_text(encoding="utf-8")
        self.assertEqual(
            json.loads(saved),
            {
                "version": 1,
                "frequency_hz": 5,
                "stale_after_seconds": 1.75,
            },
        )
        status, body = self.request("GET", "/api/live/config")
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body)["settings"],
            {"frequency_hz": 5, "stale_after_seconds": 1.75},
        )
        with patch("xplane_api.web.os.replace", side_effect=OSError("disk full")):
            status, body = self.request(
                "POST",
                "/api/live",
                {"frequency_hz": 10, "stale_after_seconds": 2.0},
            )
        self.assertEqual(status, 500)
        self.assertEqual(self.source_config_path.read_text(encoding="utf-8"), saved)

    def test_invalid_live_settings_do_not_overwrite_saved_settings(self) -> None:
        self.source_config_path.write_text(
            json.dumps({
                "version": 1,
                "frequency_hz": 5,
                "stale_after_seconds": 1.75,
            }),
            encoding="utf-8",
        )
        status, body = self.request(
            "POST",
            "/api/live",
            {"frequency_hz": 0, "stale_after_seconds": 1.0},
        )
        self.assertEqual(status, 400)
        self.assertIn(b"positive integer", body)
        self.assertEqual(
            json.loads(self.source_config_path.read_text(encoding="utf-8"))[
                "frequency_hz"
            ],
            5,
        )

    def test_live_ensure_keeps_recording_active(self) -> None:
        with patch("xplane_api.web.discover_xplane", side_effect=OSError("offline")):
            status, _ = self.request(
                "POST",
                "/api/live",
                {"frequency_hz": 5, "stale_after_seconds": 1.75},
            )
            self.assertEqual(status, 202)
            deadline = time.monotonic() + 1
            while (
                self.server.controller.snapshot()["status"] != "error"
                and time.monotonic() < deadline
            ):
                time.sleep(0.005)

        status, _ = self.request("POST", "/api/recording", _telemetry_jsonl().encode())
        self.assertEqual(status, 200)
        status, body = self.request("POST", "/api/live/ensure", {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "unchanged")
        self.assertEqual(self.server.controller.snapshot()["mode"], "recording")

    def test_malformed_saved_settings_are_reported_without_startup_connection(self) -> None:
        config_path = self.source_config_path.parent / "malformed.json"
        config_path.write_text('{"version":99}\n', encoding="utf-8")
        server = None
        try:
            with patch("xplane_api.web.discover_xplane") as discover:
                server = create_server("127.0.0.1", 0, source_config_path=config_path)
                self.assertEqual(server.controller.snapshot()["mode"], "idle")
                self.assertIn(
                    "unsupported live source settings format",
                    server.controller.source_config_snapshot()["error"],
                )
                discover.assert_not_called()
        finally:
            if server is not None:
                server.controller.close()
                server.server_close()

    def test_server_startup_uses_saved_source_settings(self) -> None:
        config_path = self.source_config_path.parent / "startup.json"
        config_path.write_text(
            json.dumps({
                "version": 1,
                "frequency_hz": 2,
                "stale_after_seconds": 2.5,
            }),
            encoding="utf-8",
        )
        server = None
        try:
            with patch(
                "xplane_api.web.discover_xplane", side_effect=OSError("offline")
            ) as discover:
                server = create_server("127.0.0.1", 0, source_config_path=config_path)
                deadline = time.monotonic() + 1
                while (
                    server.controller.snapshot()["status"] != "error"
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.005)
                self.assertEqual(server.controller.snapshot()["mode"], "live")
                self.assertEqual(server.controller.snapshot()["status"], "error")
                self.assertEqual(discover.call_count, 1)
                self.assertEqual(
                    server.controller.source_config_snapshot()["settings"],
                    {"frequency_hz": 2, "stale_after_seconds": 2.5},
                )
        finally:
            if server is not None:
                server.controller.close()
                server.server_close()


class _FakeControlClient:
    def __init__(self) -> None:
        self.commands = []
        self.datarefs = []
        self.closed = False

    def send_command(self, command: str) -> None:
        self.commands.append(command)

    def write_dataref(self, path: str, value: float) -> None:
        self.datarefs.append((path, value))

    def close(self) -> None:
        self.closed = True


class _FakeReadClient:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeTelemetryAdapter:
    def __init__(self, client, *, frequency_hz: int) -> None:
        self.client = client
        self.frequency_hz = frequency_hz
        self.read_arguments = []

    def read_state(self, *, timeout_seconds: float, stale_after_seconds: float):
        self.read_arguments.append((timeout_seconds, stale_after_seconds))
        time.sleep(0.005)
        return _flight_sample()


class _FakeAutopilotAdapter:
    def __init__(self, client, *, frequency_hz: int) -> None:
        self.client = client
        self.frequency_hz = frequency_hz
        self.read_arguments = []

    def read_state(self, *, timeout_seconds: float, stale_after_seconds: float):
        self.read_arguments.append((timeout_seconds, stale_after_seconds))
        return _autopilot_sample()


class AutopilotHarnessLiveControllerTests(unittest.TestCase):
    def test_shared_rate_state_and_opt_in_control_lifecycle(self) -> None:
        controller = HarnessController(
            allow_autopilot_control=True, source_config_path=None
        )
        read_client = _FakeReadClient("127.0.0.1", 49000)
        control_client = _FakeControlClient()
        telemetry_adapter = _FakeTelemetryAdapter(None, frequency_hz=5)
        autopilot_adapter = _FakeAutopilotAdapter(None, frequency_hz=5)
        beacon = SimpleNamespace(
            host="127.0.0.1", port=49000, version="12.4.3", hostname="fixture"
        )
        with (
            patch("xplane_api.web.discover_xplane", return_value=beacon),
            patch("xplane_api.web.RrefClient", return_value=read_client),
            patch("xplane_api.web.XPlaneControlClient", return_value=control_client),
            patch("xplane_api.web.XPlaneStateAdapter", return_value=telemetry_adapter),
            patch("xplane_api.web.AutopilotStateAdapter", return_value=autopilot_adapter),
        ):
            controller.start_live(5, 1.75)
            deadline = time.monotonic() + 1
            while not controller.snapshot()["control_available"] and time.monotonic() < deadline:
                time.sleep(0.005)
            snapshot = controller.snapshot()
            self.assertTrue(snapshot["control_available"])
            self.assertEqual(snapshot["autopilot_state"]["ap_engaged"], True)
            self.assertEqual(telemetry_adapter.frequency_hz, 5)
            self.assertEqual(telemetry_adapter.read_arguments[-1], (0.2, 1.75))
            self.assertEqual(autopilot_adapter.frequency_hz, 5)
            self.assertEqual(autopilot_adapter.read_arguments[-1], (0.0, 1.75))

            controller.press_autopilot_button("HDG")
            self.assertEqual(control_client.commands, ["sim/autopilot/heading"])
            controller.set_autopilot_selector("HEADING", 270)
            self.assertEqual(control_client.datarefs[0][1], 270)
            with self.assertRaises(ValueError):
                controller.set_autopilot_selector("HEADING", 360)
            self.assertEqual(len(control_client.datarefs), 1)
            controller.close()
        self.assertTrue(read_client.closed)
        self.assertTrue(control_client.closed)

    def test_control_enabled_but_no_live_source_returns_unavailable(self) -> None:
        controller = HarnessController(
            allow_autopilot_control=True, source_config_path=None
        )
        with self.assertRaisesRegex(RuntimeError, "connected live source"):
            controller.press_autopilot_button("AP")


if __name__ == "__main__":
    unittest.main()
