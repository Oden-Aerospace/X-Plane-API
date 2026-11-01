from datetime import datetime, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from xplane_api.models.state import FlightState
from xplane_api.aviation import AviationDataset, layer_metadata
from xplane_api.recording.jsonl import write_jsonl
from xplane_api.web import create_server


class LocalHarnessHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._source_config_directory = tempfile.TemporaryDirectory()
        self.server = create_server(
            "127.0.0.1",
            0,
            source_config_path=Path(self._source_config_directory.name)
            / "live-source.json",
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

    def request(self, method: str, path: str, body: bytes = b"") -> tuple[int, bytes]:
        self.connection.request(method, path, body=body)
        response = self.connection.getresponse()
        return response.status, response.read()

    def test_serves_local_dashboard_and_idle_state(self) -> None:
        status, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Flight Deck", html)
        status, body = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["mode"], "idle")

    def test_loads_versioned_jsonl_recording(self) -> None:
        from io import StringIO

        stream = StringIO()
        write_jsonl(
            stream,
            FlightState(
                timestamp_utc=datetime(2026, 10, 13, tzinfo=timezone.utc),
                latitude_deg=38.0,
            ),
        )
        status, body = self.request(
            "POST", "/api/recording", stream.getvalue().encode("utf-8")
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["sample_count"], 1)
        status, body = self.request("GET", "/api/state")
        snapshot = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(snapshot["mode"], "recording")
        self.assertEqual(snapshot["status"], "stopped")

    def test_serves_dedicated_displays_and_preserves_dashboard_routes(self) -> None:
        for path, display in (("/glass", "glass"), ("/sixpack", "sixpack")):
            for suffix in ("", "?screen=dedicated"):
                with self.subTest(path=path + suffix):
                    status, html = self.request("GET", path + suffix)
                    self.assertEqual(status, 200)
                    self.assertIn(f'<body data-display="{display}">'.encode(), html)
        for path in ("/", "/index.html", "/?screen=dashboard"):
            with self.subTest(path=path):
                status, html = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertIn(b"<body>", html)
        status, _ = self.request("GET", "/unknown-display")
        self.assertEqual(status, 404)

    def test_display_navigation_preserves_paused_recording_and_sample(self) -> None:
        from io import StringIO

        stream = StringIO()
        write_jsonl(
            stream,
            FlightState(
                timestamp_utc=datetime(2026, 10, 26, tzinfo=timezone.utc),
                sequence=7,
                indicated_airspeed_kt=104,
            ),
        )
        write_jsonl(
            stream,
            FlightState(
                timestamp_utc=datetime(2026, 10, 26, second=5, tzinfo=timezone.utc),
                sequence=8,
                indicated_airspeed_kt=105,
            ),
        )
        status, _ = self.request("POST", "/api/recording", stream.getvalue().encode())
        self.assertEqual(status, 200)
        published = threading.Event()
        publish = self.server.controller._publish

        def publish_sample(session: dict, state: dict) -> None:
            publish(session, state)
            published.set()

        with patch.object(self.server.controller, "_publish", side_effect=publish_sample):
            self.request("POST", "/api/playback", b'{"action":"play"}')
            self.assertTrue(published.wait(timeout=2), "recording did not publish a sample")
            self.request("POST", "/api/playback", b'{"action":"pause"}')
        _, before = self.request("GET", "/api/state")
        snapshot = json.loads(before)
        self.assertEqual(snapshot["status"], "paused")
        self.assertEqual(snapshot["state"]["sequence"], 7)
        self.assertEqual(snapshot["state"]["indicated_airspeed_kt"], 104)
        for path in ("/glass", "/sixpack", "/mfd", "/glass"):
            self.assertEqual(self.request("GET", path)[0], 200)
        _, after = self.request("GET", "/api/state")
        self.assertEqual(json.loads(before), json.loads(after))
        self.assertEqual(json.loads(after)["mode"], "recording")

    def test_rejects_live_connection_without_explicit_rate_and_stale_policy(self) -> None:
        status, body = self.request("POST", "/api/live", b"{}")
        self.assertEqual(status, 400)
        self.assertIn(b"frequency_hz", body)

    def test_failed_live_source_clears_previously_valid_state(self) -> None:
        controller = self.server.controller
        session = controller._new_session("live", status="connected", message="fixture")
        state = FlightState(timestamp_utc=datetime(2026, 10, 27, tzinfo=timezone.utc),
                            latitude_deg=38, longitude_deg=-77)
        controller._publish(session, state.to_dict())
        self.assertIsNotNone(controller.snapshot()["state"])
        with patch("xplane_api.web.discover_xplane", side_effect=OSError("synthetic source failure")):
            with self.assertLogs("xplane_api.web", level="ERROR"):
                controller._run_live(session)
        snapshot = controller.snapshot()
        self.assertEqual(snapshot["status"], "error")
        self.assertIsNone(snapshot["state"])
        self.assertEqual(snapshot["message"], "synthetic source failure")

    def test_aviation_assets_default_status_and_query_validation(self) -> None:
        for path in ("/static/leaflet/leaflet.js", "/static/leaflet/leaflet.css", "/static/leaflet/LICENSE"):
            self.assertEqual(self.request("GET", path)[0], 200)
        self.assertEqual(self.request("GET", "/static/leaflet/../web.py")[0], 404)
        status, body = self.request("GET", "/api/aviation/status")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["requested_provider"], "xplane")
        self.assertIsNone(json.loads(body)["active"])
        status, body = self.request("GET", "/api/aviation/features?bounds=-78,37,-76,39")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["available"])
        self.assertEqual(self.request("GET", "/api/aviation/range?lat=38&lon=-77&nm=20")[0], 200)
        self.assertEqual(self.request("GET", "/api/aviation/range?lat=nan&lon=0&nm=20")[0], 400)
        self.assertEqual(self.request("POST", "/api/aviation/reload", b'{"path":"untrusted"}')[0], 400)

    def test_registered_aviation_queries_do_not_replace_flight_source(self) -> None:
        feature = {"type": "Feature", "id": "airports:TEST", "geometry": {
            "type": "Point", "coordinates": [-77, 38]}, "properties": {
            "layer": "airports", "ident": "TEST", "name": "Synthetic"}}
        self.server.aviation.register("fixture", lambda options: AviationDataset(
            "fixture", [feature], {"airports": layer_metadata("synthetic")}))
        with patch.object(self.server.aviation, "config_path") as config:
            config.read_text.return_value = '{"provider":"fixture"}'
            self.assertIsNone(self.server.aviation.reload()["error"])
        _, before = self.request("GET", "/api/state")
        status, body = self.request("GET", "/api/aviation/features?bounds=-78,37,-76,39&layers=airports")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["features"]), 1)
        _, body = self.request("GET", "/api/aviation/nearest?lat=38&lon=-77")
        self.assertEqual(json.loads(body)["airports"][0]["id"], "airports:TEST")
        _, body = self.request("GET", "/api/aviation/detail?id=airports%3ATEST")
        self.assertEqual(json.loads(body)["feature"]["properties"]["name"], "Synthetic")
        _, after = self.request("GET", "/api/state")
        self.assertEqual(before, after)

    def test_live_connection_and_recording_requirements_are_explicit(self) -> None:
        status, body = self.request(
            "POST",
            "/api/live",
            json.dumps({"frequency_hz": 2, "stale_after_seconds": 2.0}).encode(),
        )
        self.assertEqual(status, 202)
        snapshot_status, snapshot_body = self.request("GET", "/api/state")
        self.assertEqual(snapshot_status, 200)
        self.assertEqual(json.loads(snapshot_body)["mode"], "live")
        self.assertEqual(json.loads(body)["status"], "connecting")

    def test_rejects_malformed_recording_and_oversized_body(self) -> None:
        status, body = self.request("POST", "/api/recording", b"not-jsonl")
        self.assertEqual(status, 400)
        self.assertIn(b"line 1", body)

        self.connection.putrequest("POST", "/api/recording")
        self.connection.putheader("Content-Length", str(4 * 1024 * 1024 + 1))
        self.connection.endheaders()
        response = self.connection.getresponse()
        self.assertEqual(response.status, 413)
        response.read()

    def test_stop_unloads_current_recording(self) -> None:
        from io import StringIO

        stream = StringIO()
        write_jsonl(
            stream,
            FlightState(
                timestamp_utc=datetime(2026, 10, 13, tzinfo=timezone.utc),
                latitude_deg=38.0,
            ),
        )
        status, _ = self.request("POST", "/api/recording", stream.getvalue().encode())
        self.assertEqual(status, 200)
        status, body = self.request("POST", "/api/stop", b"{}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["mode"], "idle")


if __name__ == "__main__":
    unittest.main()
