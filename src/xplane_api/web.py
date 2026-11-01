"""Local-only HTTP harness for live XP12 telemetry and JSONL replay."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from io import StringIO
import json
import logging
import math
import os
import socketserver
import tempfile
import threading
from typing import Any
from urllib.parse import parse_qs, urlsplit

from xplane_api.aviation.core import AviationManager, LAYERS, range_geometry
from xplane_api.aviation.providers import create_manager

from xplane_api.autopilot.panel import AutopilotPanel
from xplane_api.models.autopilot import AfcsButton, AfcsSelector
from xplane_api.models.ground_track import GroundTrackDeriver
from xplane_api.recording.jsonl import (
    AutopilotPlaybackEvent,
    JsonlPlayback,
    PairedJsonlPlayback,
    PlaybackStatus,
    TelemetryPlaybackEvent,
    iter_autopilot_jsonl,
    iter_jsonl,
)
from xplane_api.sources import (
    AutopilotStateAdapter,
    RrefClient,
    XPlaneControlClient,
    XPlaneStateAdapter,
    discover_xplane,
)


_LOG = logging.getLogger("xplane_api.web")
_MAX_RECORDING_BYTES = 4 * 1024 * 1024
_MAX_PAIRED_RECORDING_BYTES = 10 * 1024 * 1024
_DEFAULT_SOURCE_CONFIG_PATH = (
    Path.home() / ".config" / "xplane-api" / "live-source.json"
)
_SOURCE_CONFIG_VERSION = 1


class _LocalThreadingHTTPServer(ThreadingHTTPServer):
    """HTTP server that avoids blocking reverse DNS during startup."""

    aviation: AviationManager

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


class HarnessController:
    """Own a single active live or replay session and its latest sample."""

    def __init__(
        self,
        *,
        allow_autopilot_control: bool = False,
        source_config_path: Path | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._source_start_lock = threading.Lock()
        self._session: dict[str, Any] | None = None
        self._generation = 0
        self._state: dict[str, Any] | None = None
        self._autopilot_state: dict[str, Any] | None = None
        self._allow_autopilot_control = allow_autopilot_control
        self._source_config_path = source_config_path
        self._source_config: tuple[int, float] | None = None
        self._source_config_error: str | None = None
        self._load_source_config()

    @property
    def allow_autopilot_control(self) -> bool:
        return self._allow_autopilot_control

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            session = self._session
            if session is None:
                return {
                    "mode": "idle",
                    "status": "idle",
                    "message": "No source selected",
                    "state": self._state,
                    "autopilot_state": self._autopilot_state,
                    "control_available": False,
                }
            playback = session.get("playback")
            status = (
                playback.status.value
                if playback is not None
                else session.get("status", "connecting")
            )
            message = session.get("message", "")
            return {
                "mode": session["mode"],
                "status": status,
                "message": message,
                "state": self._state,
                "autopilot_state": self._autopilot_state,
                "control_available": (
                    self._allow_autopilot_control
                    and session["mode"] == "live"
                    and session.get("autopilot_panel") is not None
                    and status == "connected"
                ),
                "sample_count": playback.sample_count if playback is not None else None,
                "position": playback.position if playback is not None else None,
            }

    def start_live(self, frequency_hz: int, stale_after_seconds: float) -> None:
        frequency_hz, stale_after_seconds = self._validate_live_settings(
            frequency_hz, stale_after_seconds
        )
        with self._source_start_lock:
            with self._lock:
                self._save_source_config(frequency_hz, stale_after_seconds)
                self._source_config = (frequency_hz, stale_after_seconds)
                self._source_config_error = None
            self._start_live_validated(frequency_hz, stale_after_seconds)

    def start_saved_live(self) -> bool:
        with self._source_start_lock:
            with self._lock:
                if self._source_config is None:
                    return False
                settings = self._source_config
            self._start_live_validated(*settings)
            return True

    def ensure_saved_live(self) -> bool:
        with self._source_start_lock:
            with self._lock:
                session = self._session
                can_start = session is None or (
                    session["mode"] == "live" and session.get("status") == "error"
                )
                if self._source_config is None or not can_start:
                    return False
                settings = self._source_config
            self._start_live_validated(*settings)
            return True

    def source_config_snapshot(self) -> dict[str, Any]:
        with self._lock:
            settings = self._source_config
            return {
                "settings": (
                    {
                        "frequency_hz": settings[0],
                        "stale_after_seconds": settings[1],
                    }
                    if settings is not None
                    else None
                ),
                "error": self._source_config_error,
            }

    @staticmethod
    def _validate_live_settings(
        frequency_hz: Any, stale_after_seconds: Any
    ) -> tuple[int, float]:
        if (
            isinstance(frequency_hz, bool)
            or not isinstance(frequency_hz, int)
            or frequency_hz < 1
        ):
            raise ValueError("frequency_hz must be a positive integer")
        if isinstance(stale_after_seconds, bool) or not isinstance(
            stale_after_seconds, (int, float)
        ):
            raise ValueError("stale_after_seconds must be a positive number")
        if not math.isfinite(stale_after_seconds) or stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be a finite positive number")
        return frequency_hz, float(stale_after_seconds)

    def _load_source_config(self) -> None:
        if self._source_config_path is None:
            return
        try:
            data = json.loads(self._source_config_path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or set(data)
                != {"version", "frequency_hz", "stale_after_seconds"}
                or isinstance(data.get("version"), bool)
                or data.get("version") != _SOURCE_CONFIG_VERSION
            ):
                raise ValueError("unsupported live source settings format")
            self._source_config = self._validate_live_settings(
                data["frequency_hz"], data["stale_after_seconds"]
            )
        except FileNotFoundError:
            return
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            self._source_config_error = f"Could not load live source settings: {exc}"
            _LOG.error("%s", self._source_config_error)

    def _save_source_config(
        self, frequency_hz: int, stale_after_seconds: float
    ) -> None:
        path = self._source_config_path
        if path is None:
            return
        temporary_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                json.dump(
                    {
                        "version": _SOURCE_CONFIG_VERSION,
                        "frequency_hz": frequency_hz,
                        "stale_after_seconds": stale_after_seconds,
                    },
                    stream,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    _LOG.exception("Could not remove temporary source settings file")

    def _start_live_validated(
        self, frequency_hz: int, stale_after_seconds: float
    ) -> None:
        self._stop_current()
        session = self._new_session("live", status="connecting", message="Discovering X-Plane beacon")
        session["frequency_hz"] = frequency_hz
        session["stale_after_seconds"] = stale_after_seconds
        session["ground_track"] = GroundTrackDeriver.for_adsb_test_output()
        worker = threading.Thread(
            target=self._run_live,
            args=(session,),
            name="xplane-live-source",
            daemon=True,
        )
        session["worker"] = worker
        worker.start()

    def load_recording(self, content: str) -> int:
        playback = JsonlPlayback.from_stream(StringIO(content))
        if playback.sample_count == 0:
            raise ValueError("recording contains no state samples")
        return self._start_recording(playback)

    def load_paired_recording(self, telemetry: str, autopilot: str) -> int:
        telemetry_samples = list(iter_jsonl(StringIO(telemetry)))
        if not telemetry_samples:
            raise ValueError("recording contains no telemetry state samples")
        autopilot_samples = list(iter_autopilot_jsonl(StringIO(autopilot)))
        playback = PairedJsonlPlayback(telemetry_samples, autopilot_samples)
        return self._start_recording(playback)

    def _start_recording(self, playback: JsonlPlayback | PairedJsonlPlayback) -> int:
        with self._source_start_lock:
            self._stop_current()
            session = self._new_session(
                "recording", status=PlaybackStatus.STOPPED.value, message="Recording loaded"
            )
            session["playback"] = playback
            session["ground_track"] = GroundTrackDeriver.for_adsb_test_output()
            worker = threading.Thread(
                target=self._run_playback,
                args=(session,),
                name="xplane-jsonl-playback",
                daemon=True,
            )
            session["worker"] = worker
            worker.start()
            return playback.sample_count

    def press_autopilot_button(self, button: AfcsButton | str) -> None:
        panel = self._control_panel()
        panel.press_button(button)

    def set_autopilot_selector(self, selector: AfcsSelector | str, value: float) -> None:
        panel = self._control_panel()
        panel.set_selector(selector, value)

    def _control_panel(self) -> AutopilotPanel:
        with self._lock:
            if not self._allow_autopilot_control:
                raise PermissionError("autopilot control is disabled; relaunch with --allow-autopilot-control")
            session = self._session
            if (
                session is None
                or session["mode"] != "live"
                or session.get("autopilot_panel") is None
                or session.get("status") != "connected"
            ):
                raise RuntimeError("autopilot control requires a connected live source")
            return session["autopilot_panel"]

    def playback_action(self, action: str) -> None:
        with self._lock:
            session = self._session
            if session is None or session["mode"] != "recording":
                raise ValueError("no recording is loaded")
            playback: JsonlPlayback | PairedJsonlPlayback = session["playback"]
            if action == "play":
                if playback.status is PlaybackStatus.ENDED:
                    session["ground_track"].reset()
                playback.play()
            elif action == "pause":
                playback.pause()
            elif action == "stop":
                playback.stop()
                session["ground_track"].reset()
                self._state = None
                self._autopilot_state = None
            else:
                raise ValueError("action must be play, pause, or stop")

    def close(self) -> None:
        self._stop_current()

    def _new_session(self, mode: str, *, status: str, message: str) -> dict[str, Any]:
        with self._lock:
            self._generation += 1
            session: dict[str, Any] = {
                "generation": self._generation,
                "mode": mode,
                "status": status,
                "message": message,
                "stop": threading.Event(),
            }
            self._session = session
            self._state = None
            self._autopilot_state = None
            return session

    def _is_current(self, session: dict[str, Any]) -> bool:
        with self._lock:
            return self._session is session and not session["stop"].is_set()

    def _publish(
        self,
        session: dict[str, Any],
        state: dict[str, Any] | None,
        autopilot_state: dict[str, Any] | None = None,
        *,
        update_autopilot: bool = False,
    ) -> None:
        with self._lock:
            if self._session is session and not session["stop"].is_set():
                if state is not None:
                    self._state = state
                if update_autopilot:
                    self._autopilot_state = autopilot_state
                if session["mode"] == "live":
                    session["status"] = "connected"
                    session["message"] = "Live X-Plane telemetry"

    def _run_live(self, session: dict[str, Any]) -> None:
        client: RrefClient | None = None
        control: XPlaneControlClient | None = None
        try:
            beacon = discover_xplane(timeout_seconds=5.0)
            if not self._is_current(session):
                return
            client = RrefClient(beacon.host, beacon.port)
            session["client"] = client
            telemetry_adapter = XPlaneStateAdapter(
                client, frequency_hz=session["frequency_hz"]
            )
            autopilot_adapter = AutopilotStateAdapter(
                client, frequency_hz=session["frequency_hz"]
            )
            session["adapter"] = telemetry_adapter
            session["autopilot_adapter"] = autopilot_adapter
            if self._allow_autopilot_control:
                control = XPlaneControlClient(beacon.host, beacon.port)
                session["control_client"] = control
                session["autopilot_panel"] = AutopilotPanel(
                    autopilot_adapter, control
                )
            with self._lock:
                if self._session is session:
                    session["message"] = (
                        f"X-Plane {beacon.version} · {beacon.hostname} · "
                        f"{session['frequency_hz']} Hz"
                    )
            wait_seconds = 1.0 / session["frequency_hz"]
            while self._is_current(session):
                state = telemetry_adapter.read_state(
                    timeout_seconds=wait_seconds,
                    stale_after_seconds=session["stale_after_seconds"],
                )
                state = session["ground_track"].update(state)
                autopilot_state = autopilot_adapter.read_state(
                    timeout_seconds=0.0,
                    stale_after_seconds=session["stale_after_seconds"],
                )
                self._publish(
                    session,
                    state.to_dict(),
                    autopilot_state.to_json_dict(),
                    update_autopilot=True,
                )
        except Exception as exc:
            _LOG.exception("Live X-Plane source failed")
            with self._lock:
                if self._session is session and not session["stop"].is_set():
                    session["status"] = "error"
                    session["message"] = str(exc)
                    self._state = None
                    self._autopilot_state = None
        finally:
            if client is None:
                client = session.get("client")
            if client is not None:
                client.close()
            if control is None:
                control = session.get("control_client")
            if control is not None:
                control.close()
            with self._lock:
                if self._session is session and session["stop"].is_set():
                    session["status"] = "stopped"
                    session["message"] = "Live source stopped"

    def _run_playback(self, session: dict[str, Any]) -> None:
        playback: JsonlPlayback | PairedJsonlPlayback = session["playback"]
        stop_event: threading.Event = session["stop"]
        while not stop_event.is_set():
            if playback.status is PlaybackStatus.PLAYING:
                sample = playback.next_sample_realtime()
                if isinstance(sample, TelemetryPlaybackEvent):
                    state = session["ground_track"].update(sample.state)
                    self._publish(session, state.to_dict())
                elif isinstance(sample, AutopilotPlaybackEvent):
                    self._publish(
                        session,
                        None,
                        sample.state.to_json_dict(),
                        update_autopilot=True,
                    )
                elif sample is not None:
                    state = session["ground_track"].update(sample)
                    self._publish(session, state.to_dict())
            else:
                stop_event.wait(0.05)
        with self._lock:
            if self._session is session:
                session["message"] = "Recording stopped"

    def _stop_current(self) -> None:
        with self._lock:
            session = self._session
            if session is None:
                return
            session["stop"].set()
            playback = session.get("playback")
            if playback is not None:
                playback.stop()
            client = session.get("client")
            worker = session.get("worker")
            self._session = None
            self._state = None
            self._autopilot_state = None
        if client is not None:
            client.close()
        control_client = session.get("control_client")
        if control_client is not None:
            control_client.close()
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1.0)


class HarnessHandler(BaseHTTPRequestHandler):
    controller: HarnessController
    aviation: AviationManager

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlsplit(self.path).path
        if path in ("/", "/index.html", "/glass", "/sixpack", "/autopilot"):
            content = (
                Path(__file__).with_name("web_static") / "index.html"
            ).read_bytes()
            body_attributes = []
            if path in ("/glass", "/sixpack"):
                body_attributes.append(f'data-display="{path[1:]}"')
            elif path == "/autopilot":
                body_attributes.append('data-page="autopilot"')
            if self.controller.allow_autopilot_control:
                body_attributes.append('data-control-opt-in="true"')
            if body_attributes:
                content = content.replace(
                    b"<body>", f'<body {" ".join(body_attributes)}>'.encode("ascii"), 1
                )
            self._send(200, content, "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(200, self.controller.snapshot())
        elif path == "/api/live/config":
            self._json(200, self.controller.source_config_snapshot())
        elif path == "/mfd":
            content = (Path(__file__).with_name("web_static") / "mfd.html").read_bytes()
            self._send(200, content, "text/html; charset=utf-8")
        elif path.startswith("/api/aviation/"):
            try:
                self._aviation_get(path, parse_qs(urlsplit(self.path).query))
            except (ValueError, KeyError, TypeError) as exc:
                self._json(400, {"error": str(exc)})
        elif path.startswith("/static/leaflet/"):
            name = path.removeprefix("/static/leaflet/")
            types = {"leaflet.js": "application/javascript", "leaflet.css": "text/css",
                     "leaflet.js.map": "application/json", "LICENSE": "text/plain"}
            images = {"layers.png", "layers-2x.png", "marker-icon.png", "marker-icon-2x.png", "marker-shadow.png"}
            if name in types or (name.startswith("images/") and name[7:] in images):
                content = (Path(__file__).with_name("web_static") / "vendor" / name).read_bytes()
                self._send(200, content, types.get(name, "image/png"))
            else:
                self._json(404, {"error": "not found"})
        else:
            self._json(404, {"error": "not found"})

    def _aviation_get(self, path: str, query: dict) -> None:
        if path == "/api/aviation/status":
            self._json(200, self.aviation.status())
            return
        if path == "/api/aviation/range":
            self._json(200, range_geometry(float(query["lat"][0]), float(query["lon"][0]), float(query["nm"][0])))
            return
        if path not in ("/api/aviation/features", "/api/aviation/nearest", "/api/aviation/detail"):
            self._json(404, {"error": "not found"})
            return
        dataset = self.aviation.active()
        if path == "/api/aviation/features":
            bounds = [float(value) for value in query["bounds"][0].split(",")]
            layers = query.get("layers", [",".join(LAYERS)])[0].split(",")
            if dataset:
                self._json(200, dataset.query(bounds, layers))
            else:
                self._json(200, {"type": "FeatureCollection", "features": [], "available": False})
        elif path == "/api/aviation/nearest":
            latitude, longitude = float(query["lat"][0]), float(query["lon"][0])
            airports = dataset.nearest(latitude, longitude) if dataset else []
            self._json(200, {"airports": airports, "available": bool(dataset),
                             "provider": dataset.provider if dataset else None,
                             "imported_at": dataset.imported_at if dataset else None})
        else:
            feature = dataset.detail(query["id"][0]) if dataset else None
            self._json(200 if feature else 404, {"feature": feature,
                "provider": dataset.provider if dataset else None,
                "imported_at": dataset.imported_at if dataset else None})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            request_limit = (
                _MAX_PAIRED_RECORDING_BYTES
                if self.path == "/api/paired-recording"
                else _MAX_RECORDING_BYTES
            )
            if content_length < 0 or content_length > request_limit:
                self._json(
                    413,
                    {
                        "error": (
                            "request body exceeds the "
                            f"{request_limit // (1024 * 1024)} MiB limit"
                        )
                    },
                )
                return
            body = self.rfile.read(content_length)

            if self.path == "/api/aviation/reload":
                if json.loads(body or b"{}") != {}:
                    raise ValueError("reload reads only the server-configured aviation file")
                status = self.aviation.reload()
                self._json(400 if status["error"] else 200, status)
            elif self.path == "/api/live":
                payload = json.loads(body or b"{}")
                self.controller.start_live(
                    frequency_hz=payload.get("frequency_hz"),
                    stale_after_seconds=payload.get("stale_after_seconds"),
                )
                self._json(202, {"status": "connecting"})
            elif self.path == "/api/live/ensure":
                if json.loads(body or b"{}") != {}:
                    raise ValueError("live connection ensure accepts an empty object")
                started = self.controller.ensure_saved_live()
                self._json(
                    202 if started else 200,
                    {"status": "connecting" if started else "unchanged"},
                )
            elif self.path == "/api/recording":
                content = body.decode("utf-8-sig")
                count = self.controller.load_recording(content)
                self._json(200, {"status": "stopped", "sample_count": count})
            elif self.path == "/api/paired-recording":
                payload = json.loads(body or b"{}")
                telemetry = payload.get("telemetry_jsonl")
                autopilot = payload.get("autopilot_jsonl")
                if not isinstance(telemetry, str) or not isinstance(autopilot, str):
                    raise ValueError("telemetry_jsonl and autopilot_jsonl must be strings")
                if (
                    len(telemetry.encode("utf-8")) > _MAX_RECORDING_BYTES
                    or len(autopilot.encode("utf-8")) > _MAX_RECORDING_BYTES
                ):
                    self._json(
                        413,
                        {"error": "each JSONL recording must not exceed 4 MiB"},
                    )
                    return
                count = self.controller.load_paired_recording(telemetry, autopilot)
                self._json(200, {"status": "stopped", "sample_count": count})
            elif self.path == "/api/playback":
                payload = json.loads(body or b"{}")
                self.controller.playback_action(payload.get("action", ""))
                self._json(200, self.controller.snapshot())
            elif self.path == "/api/autopilot/button":
                if not self.controller.allow_autopilot_control:
                    self._json(
                        403,
                        {
                            "error": (
                                "autopilot control is disabled; relaunch with "
                                "--allow-autopilot-control"
                            )
                        },
                    )
                    return
                payload = json.loads(body or b"{}")
                self.controller.press_autopilot_button(payload.get("button"))
                self._json(200, {"status": "request_sent"})
            elif self.path == "/api/autopilot/selector":
                if not self.controller.allow_autopilot_control:
                    self._json(
                        403,
                        {
                            "error": (
                                "autopilot control is disabled; relaunch with "
                                "--allow-autopilot-control"
                            )
                        },
                    )
                    return
                payload = json.loads(body or b"{}")
                self.controller.set_autopilot_selector(
                    payload.get("selector"), payload.get("value")
                )
                self._json(200, {"status": "request_sent"})
            elif self.path == "/api/stop":
                self.controller.close()
                self._json(200, self.controller.snapshot())
            else:
                self._json(404, {"error": "not found"})
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
        except PermissionError as exc:
            self._json(403, {"error": str(exc)})
        except RuntimeError as exc:
            self._json(409, {"error": str(exc)})
        except Exception as exc:
            _LOG.exception("Harness request failed")
            self._json(500, {"error": str(exc)})

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self._send(status, content, "application/json; charset=utf-8")

    def _send(self, status: int, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        message = format % args
        if args and str(args[0]).startswith(("GET /api/state ", "GET /api/aviation/")):
            _LOG.debug("%s - %s", self.address_string(), message)
        else:
            _LOG.info("%s - %s", self.address_string(), message)


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    aviation_config: str | None = None,
    allow_autopilot_control: bool = False,
    source_config_path: Path | None = _DEFAULT_SOURCE_CONFIG_PATH,
) -> _LocalThreadingHTTPServer:
    """Create a loopback-only-capable local harness HTTP server."""
    controller = HarnessController(
        allow_autopilot_control=allow_autopilot_control,
        source_config_path=source_config_path,
    )

    class BoundHarnessHandler(HarnessHandler):
        pass

    BoundHarnessHandler.controller = controller
    BoundHarnessHandler.aviation = create_manager(aviation_config)
    server = _LocalThreadingHTTPServer((host, port), BoundHarnessHandler)
    server.daemon_threads = True
    server.controller = controller  # type: ignore[attr-defined]
    server.aviation = BoundHarnessHandler.aviation
    controller.start_saved_live()
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local X-Plane telemetry harness")
    parser.add_argument("--host", default="127.0.0.1", help="bind address; default is loopback only")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--aviation-config", help="local JSON aviation-provider configuration")
    parser.add_argument(
        "--source-config",
        type=Path,
        default=_DEFAULT_SOURCE_CONFIG_PATH,
        help=f"live source settings file (default: {_DEFAULT_SOURCE_CONFIG_PATH})",
    )
    parser.add_argument(
        "--allow-autopilot-control",
        action="store_true",
        help="enable live autopilot control endpoints and UI affordances",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = create_server(
        args.host,
        args.port,
        aviation_config=args.aviation_config,
        allow_autopilot_control=args.allow_autopilot_control,
        source_config_path=args.source_config,
    )
    print(f"X-Plane telemetry harness: http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.controller.close()  # type: ignore[attr-defined]
        server.server_close()


if __name__ == "__main__":
    main()
