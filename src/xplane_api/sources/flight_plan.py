"""Read the plugin-owned flight-plan snapshot through X-Plane's local Web API."""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
import threading
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from xplane_api.models.flight_plan import (
    FLIGHT_PLAN_BRIDGE_VERSION,
    FlightPlanState,
)


_BRIDGE_VERSION_DATAREF = "xplane_api/flight_plan/bridge_version"
_SNAPSHOT_DATAREF = "xplane_api/flight_plan/bridge_snapshot"
_DIRECT_TO_REQUEST_DATAREF = "xplane_api/flight_plan/direct_to_request"
_DIRECT_TO_RESULT_DATAREF = "xplane_api/flight_plan/direct_to_result"
_DIRECT_TO_COMMAND = "xplane_api/flight_plan/direct_to"
_MAX_RESPONSE_BYTES = 1_048_576
_IDENTIFIER_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9_-]{0,15}\Z")


class FlightPlanError(RuntimeError):
    """Base class for explicit flight-plan source failures."""


class FlightPlanUnavailableError(FlightPlanError):
    """The local Web API or bridge datarefs are unavailable."""


class FlightPlanProtocolError(FlightPlanError):
    """The bridge returned malformed or unsupported data."""


class FlightPlanControlDisabledError(FlightPlanError):
    """Flight-plan writes were requested without the dedicated opt-in."""


class FlightPlanTargetError(ValueError):
    """The direct-to target is invalid, missing, or ambiguous."""


class FlightPlanStateError(FlightPlanError):
    """The current plan cannot safely accept the requested direct-to."""


class FlightPlanCommandError(FlightPlanError):
    """The plugin could not apply the direct-to request."""


class FlightPlanReadbackError(FlightPlanError):
    """The active plan did not confirm the requested direct-to target."""


class FlightPlanClient:
    """Resolve session-scoped IDs and read or explicitly opt into direct-to."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8086",
        *,
        timeout_seconds: float = 2.0,
        allow_control: bool = False,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        if not isinstance(base_url, str) or not base_url:
            raise ValueError("base_url must be a non-empty HTTP(S) URL")
        parsed_url = urlsplit(base_url)
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            raise ValueError("base_url must be a non-empty HTTP(S) URL")
        if parsed_url.query or parsed_url.fragment:
            raise ValueError("base_url cannot contain a query or fragment")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if not isinstance(allow_control, bool):
            raise TypeError("allow_control must be a boolean")
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = float(timeout_seconds)
        self._allow_control = allow_control
        self._opener = opener
        self._write_lock = threading.Lock()

    def read_plan(self) -> FlightPlanState:
        self._read_bridge_version()
        snapshot_id = self._resolve_dataref(_SNAPSHOT_DATAREF, "data")
        snapshot_payload = self._get_json(f"/api/v3/datarefs/{snapshot_id}/value")
        encoded_snapshot = snapshot_payload.get("data")
        if not isinstance(encoded_snapshot, str):
            raise FlightPlanProtocolError("bridge snapshot value must be base64 text")
        snapshot = self._decode_data_json(encoded_snapshot, "bridge snapshot")
        try:
            return FlightPlanState.from_bridge_dict(snapshot)
        except (TypeError, ValueError) as exc:
            raise FlightPlanProtocolError(f"invalid bridge snapshot: {exc}") from exc

    def _read_bridge_version(self) -> None:
        version_id = self._resolve_dataref(_BRIDGE_VERSION_DATAREF, "int")
        version_payload = self._get_json(f"/api/v3/datarefs/{version_id}/value")
        version = version_payload.get("data")
        if isinstance(version, bool) or not isinstance(version, int):
            raise FlightPlanProtocolError("bridge version value must be an integer")
        if version != FLIGHT_PLAN_BRIDGE_VERSION:
            raise FlightPlanProtocolError(
                f"unsupported flight plan bridge version: {version}"
            )

    def direct_to(
        self,
        identifier: str,
        *,
        timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.1,
    ) -> dict[str, Any]:
        """Request a direct-to and return only after active-plan read-back."""
        if not self._allow_control:
            raise FlightPlanControlDisabledError(
                "flight-plan control is disabled; enable the dedicated opt-in"
            )
        target_identifier = _normalize_identifier(identifier)
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or not math.isfinite(poll_interval_seconds)
            or poll_interval_seconds <= 0
        ):
            raise ValueError("poll_interval_seconds must be finite and positive")
        request_id = uuid4().hex

        with self._write_lock:
            self._read_bridge_version()
            request_id_dataref = self._resolve_dataref(
                _DIRECT_TO_REQUEST_DATAREF, "data"
            )
            result_dataref = self._resolve_dataref(
                _DIRECT_TO_RESULT_DATAREF, "data"
            )
            command_id = self._resolve_command(_DIRECT_TO_COMMAND)
            request_value = f"{request_id}|{target_identifier}".encode("ascii")
            encoded_request = base64.b64encode(request_value).decode("ascii")
            self._request_success(
                "PATCH",
                f"/api/v3/datarefs/{request_id_dataref}/value",
                {"data": encoded_request},
            )
            self._request_success(
                "POST",
                f"/api/v2/command/{command_id}/activate",
                {"duration": 0},
            )

            deadline = time.monotonic() + timeout_seconds
            while True:
                if time.monotonic() >= deadline:
                    raise FlightPlanReadbackError(
                        "timed out waiting for direct-to result and active-plan read-back"
                    )
                result_payload = self._get_json(
                    f"/api/v3/datarefs/{result_dataref}/value"
                )
                encoded_result = result_payload.get("data")
                if not isinstance(encoded_result, str):
                    raise FlightPlanProtocolError(
                        "direct-to result value must be base64 text"
                    )
                result = self._decode_data_json(encoded_result, "direct-to result")
                if result.get("request_id") == request_id:
                    if result.get("target_identifier") != target_identifier:
                        raise FlightPlanProtocolError(
                            "direct-to result target does not match the request"
                        )
                    status = result.get("status")
                    if not isinstance(status, str):
                        raise FlightPlanProtocolError(
                            "direct-to result status must be text"
                        )
                    if status == "applied":
                        plan = self.read_plan()
                        active_index = plan.active_index
                        active_identifier = (
                            plan.entries[active_index].identifier
                            if active_index is not None
                            else None
                        )
                        if (
                            active_index is None
                            or not plan.available
                            or active_identifier is None
                            or active_identifier.strip().upper() != target_identifier
                        ):
                            raise FlightPlanReadbackError(
                                "the simulator did not confirm the requested "
                                "target as active"
                            )
                        return {
                            "status": "confirmed",
                            "target_identifier": target_identifier,
                            "active_index": active_index,
                            "plan": plan.to_json_dict(),
                        }
                    if status == "processing":
                        pass
                    else:
                        self._raise_direct_to_error(status, result.get("error"))

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FlightPlanReadbackError(
                        "timed out waiting for direct-to result and active-plan read-back"
                    )
                time.sleep(min(poll_interval_seconds, remaining))

    def _resolve_command(self, name: str) -> int:
        query = urlencode({"filter[name]": name})
        payload = self._get_json(f"/api/v2/commands?{query}")
        matches = payload.get("data")
        if not isinstance(matches, list):
            raise FlightPlanProtocolError("command lookup response must contain a list")
        exact_matches = [
            item
            for item in matches
            if isinstance(item, Mapping) and item.get("name") == name
        ]
        if not exact_matches:
            raise FlightPlanUnavailableError(f"flight plan bridge command not found: {name}")
        if len(exact_matches) != 1:
            raise FlightPlanProtocolError(f"ambiguous flight plan bridge command: {name}")
        command_id = exact_matches[0].get("id")
        if isinstance(command_id, bool) or not isinstance(command_id, int):
            raise FlightPlanProtocolError(f"invalid command ID for {name}")
        return command_id

    @staticmethod
    def _raise_direct_to_error(status: str, error: Any) -> None:
        message = error if isinstance(error, str) and error else status.replace("_", " ")
        if status in {
            "unknown_target",
            "ambiguous_target",
            "unsupported_target",
            "ambiguous_route_entry",
        }:
            raise FlightPlanTargetError(message)
        if status in {
            "no_active_plan",
            "invalid_entry_count",
            "flight_plan_full",
            "unsupported_route_entry",
        }:
            raise FlightPlanStateError(message)
        if status == "invalid_request":
            raise FlightPlanProtocolError(message)
        raise FlightPlanCommandError(message)

    @staticmethod
    def _decode_data_json(encoded: str, description: str) -> Mapping[str, Any]:
        try:
            decoded = base64.b64decode(encoded, validate=True)
            payload = json.loads(decoded)
        except (
            binascii.Error,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise FlightPlanProtocolError(
                f"{description} is not valid base64-encoded JSON"
            ) from exc
        if not isinstance(payload, Mapping):
            raise FlightPlanProtocolError(f"{description} must decode to an object")
        return payload

    def _resolve_dataref(self, name: str, expected_type: str) -> int:
        query = urlencode({"filter[name]": name})
        payload = self._get_json(f"/api/v3/datarefs?{query}")
        matches = payload.get("data")
        if not isinstance(matches, list):
            raise FlightPlanProtocolError("dataref lookup response must contain a list")
        exact_matches = [
            item
            for item in matches
            if isinstance(item, Mapping) and item.get("name") == name
        ]
        if not exact_matches:
            raise FlightPlanUnavailableError(f"flight plan bridge dataref not found: {name}")
        if len(exact_matches) != 1:
            raise FlightPlanProtocolError(f"ambiguous flight plan bridge dataref: {name}")
        match = exact_matches[0]
        dataref_id = match.get("id")
        if isinstance(dataref_id, bool) or not isinstance(dataref_id, int):
            raise FlightPlanProtocolError(f"invalid dataref ID for {name}")
        if match.get("value_type") != expected_type:
            raise FlightPlanProtocolError(
                f"unexpected value type for {name}: {match.get('value_type')!r}"
            )
        return dataref_id

    def _get_json(self, path: str) -> Mapping[str, Any]:
        return self._request_json("GET", path)

    def _request_json(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        response = self._request_payload(method, path, payload)
        if response is None:
            raise FlightPlanProtocolError(
                "X-Plane Web API response must be an object"
            )
        return response

    def _request_success(
        self, method: str, path: str, payload: Mapping[str, Any]
    ) -> None:
        self._request_payload(method, path, payload)

    def _request_payload(
        self, method: str, path: str, payload: Mapping[str, Any] | None
    ) -> Mapping[str, Any] | None:
        data = (
            json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
            if payload is not None
            else None
        )
        request = Request(
            f"{self._base_url}{path}",
            data=data,
            headers={
                "Accept": "application/json",
                **(
                    {"Content-Type": "application/json"}
                    if data is not None
                    else {}
                ),
            },
            method=method,
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                content = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise FlightPlanUnavailableError(
                f"X-Plane Web API returned HTTP {exc.code}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise FlightPlanUnavailableError(
                f"cannot read flight plan from X-Plane Web API: {exc}"
            ) from exc
        if len(content) > _MAX_RESPONSE_BYTES:
            raise FlightPlanProtocolError("X-Plane Web API response is too large")
        if not content:
            return None
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FlightPlanProtocolError(
                "X-Plane Web API returned invalid JSON"
            ) from exc
        if payload is None:
            return None
        if not isinstance(payload, Mapping):
            raise FlightPlanProtocolError("X-Plane Web API response must be an object")
        return payload


def _normalize_identifier(identifier: Any) -> str:
    if not isinstance(identifier, str):
        raise FlightPlanTargetError("target identifier must be text")
    normalized = identifier.strip().upper()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise FlightPlanTargetError(
            "target identifier must contain 1-16 letters, digits, hyphens, or underscores"
        )
    return normalized
