import base64
from datetime import datetime, timezone
import json
import math
import unittest
from typing import Any
from unittest.mock import patch

from xplane_api.models.flight_plan import (
    FlightPlanState,
    FlightPlanStatus,
    NavigationEntryType,
    PlanFieldStatus,
)
from xplane_api.sources.flight_plan import (
    FlightPlanClient,
    FlightPlanControlDisabledError,
    FlightPlanProtocolError,
    FlightPlanReadbackError,
    FlightPlanTargetError,
    FlightPlanUnavailableError,
)


def _snapshot() -> dict:
    return {
        "schema_version": 1,
        "bridge_version": 3,
        "source": "pilot_primary",
        "generated_at_unix_ms": 1_798_761_600_000,
        "available": True,
        "status": "available",
        "active_index": 1,
        "active_index_status": "available",
        "entries": [
            {
                "index": 0,
                "identifier": "KSEA",
                "type": "airport",
                "latitude_deg": 47.4489,
                "longitude_deg": -122.3094,
                "altitude_ft": 0,
                "field_status": {
                    "identifier": "available",
                    "type": "available",
                    "position": "available",
                    "altitude": "reported",
                },
            },
            {
                "index": 1,
                "identifier": "SEA",
                "type": "fix",
                "latitude_deg": 47.45,
                "longitude_deg": -122.3,
                "altitude_ft": 5000,
                "field_status": {
                    "identifier": "available",
                    "type": "available",
                    "position": "available",
                    "altitude": "reported",
                },
            },
            {
                "index": 2,
                "identifier": None,
                "type": "lat_lon",
                "latitude_deg": 45.0,
                "longitude_deg": -120.0,
                "altitude_ft": 3000,
                "field_status": {
                    "identifier": "unavailable",
                    "type": "available",
                    "position": "available",
                    "altitude": "reported",
                },
            },
        ],
    }


def _client_responses(
    payload: dict | None = None,
    *,
    version: int = 3,
    snapshot_value: str | None = None,
    version_id: int = 11,
    snapshot_id: int = 22,
) -> list[dict]:
    if snapshot_value is None:
        snapshot_value = base64.b64encode(
            json.dumps(payload or _snapshot()).encode("utf-8")
        ).decode("ascii")
    return [
        {
            "data": [
                {
                    "id": version_id,
                    "name": "xplane_api/flight_plan/bridge_version",
                    "value_type": "int",
                }
            ]
        },
        {"data": version},
        {
            "data": [
                {
                    "id": snapshot_id,
                    "name": "xplane_api/flight_plan/bridge_snapshot",
                    "value_type": "data",
                }
            ]
        },
        {"data": snapshot_value},
    ]


def _direct_to_responses(
    request_id: str, status: str, target: str, error: str | None = None
) -> list[Any]:
    result = {
        "request_id": request_id,
        "status": status,
        "target_identifier": target,
        "error": error if error is not None else (
            None if status == "applied" else "synthetic direct-to error"
        ),
    }
    encoded_result = base64.b64encode(json.dumps(result).encode()).decode()
    return [
        {
            "data": [
                {
                    "id": 11,
                    "name": "xplane_api/flight_plan/bridge_version",
                    "value_type": "int",
                }
            ]
        },
        {"data": 3},
        {
            "data": [
                {
                    "id": 31,
                    "name": "xplane_api/flight_plan/direct_to_request",
                    "value_type": "data",
                }
            ]
        },
        {
            "data": [
                {
                    "id": 32,
                    "name": "xplane_api/flight_plan/direct_to_result",
                    "value_type": "data",
                }
            ]
        },
        {"data": [{"id": 33, "name": "xplane_api/flight_plan/direct_to"}]},
        {},
        {},
        {"data": encoded_result},
    ]


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self.content = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        del _args
        return None

    def read(self, limit: int) -> bytes:
        return self.content[:limit]


class SequenceOpener:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, *, timeout):
        assert timeout > 0
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected extra Web API request")
        return FakeResponse(self.responses.pop(0))


class FlightPlanModelTests(unittest.TestCase):
    def test_builds_unit_bearing_plan_and_round_trips_json(self) -> None:
        plan = FlightPlanState.from_bridge_dict(_snapshot())
        self.assertEqual(plan.status, FlightPlanStatus.AVAILABLE)
        self.assertEqual(plan.timestamp_utc.tzinfo, timezone.utc)
        self.assertEqual(plan.active_index, 1)
        self.assertEqual(plan.entries[0].type, NavigationEntryType.AIRPORT)
        self.assertEqual(plan.entries[1].altitude_ft, 5000)
        self.assertEqual(
            plan.entries[0].field_status["altitude"], PlanFieldStatus.REPORTED
        )
        self.assertIsNone(plan.entries[2].identifier)
        self.assertEqual(
            plan.entries[2].field_status["identifier"],
            PlanFieldStatus.UNAVAILABLE,
        )
        self.assertEqual(
            FlightPlanState.from_bridge_dict(_snapshot()).to_json_dict(),
            plan.to_json_dict(),
        )

    def test_no_active_plan_is_explicit_and_not_an_empty_success(self) -> None:
        payload = _snapshot()
        payload.update(
            available=False,
            status="no_active_plan",
            active_index=None,
            active_index_status="unavailable",
            entries=[],
        )
        plan = FlightPlanState.from_bridge_dict(payload)
        self.assertFalse(plan.available)
        self.assertEqual(plan.status, FlightPlanStatus.NO_ACTIVE_PLAN)
        self.assertIsNone(plan.active_index)

    def test_rejects_mismatched_indices_versions_and_field_status(self) -> None:
        cases = []
        mismatched_index = _snapshot()
        mismatched_index["entries"][1]["index"] = 4
        cases.append(mismatched_index)
        wrong_bridge = _snapshot()
        wrong_bridge["bridge_version"] = 2
        cases.append(wrong_bridge)
        bad_field_status = _snapshot()
        bad_field_status["entries"][0]["field_status"]["identifier"] = "available"
        bad_field_status["entries"][0]["identifier"] = None
        cases.append(bad_field_status)
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                FlightPlanState.from_bridge_dict(payload)

    def test_rejects_naive_timestamp(self) -> None:
        with self.assertRaises(ValueError):
            FlightPlanState(
                timestamp_utc=datetime(2026, 11, 1),
                source="pilot_primary",
                available=False,
                status=FlightPlanStatus.NO_ACTIVE_PLAN,
                active_index=None,
                active_index_status=PlanFieldStatus.UNAVAILABLE,
                entries=(),
            )


class FlightPlanClientTests(unittest.TestCase):
    def test_resolves_session_ids_and_reads_base64_snapshot(self) -> None:
        opener = SequenceOpener(_client_responses())
        plan = FlightPlanClient(opener=opener).read_plan()
        self.assertTrue(plan.available)
        self.assertEqual(plan.active_index, 1)
        self.assertEqual(len(opener.requests), 4)
        self.assertTrue(
            all(request.get_method() == "GET" for request in opener.requests)
        )
        self.assertIn("bridge_version", opener.requests[0].full_url)
        self.assertIn("bridge_snapshot", opener.requests[2].full_url)

    def test_direct_to_writes_request_triggers_command_and_confirms_readback(self) -> None:
        request_id = "0123456789abcdef0123456789abcdef"
        opener = SequenceOpener(
            _direct_to_responses(request_id, "applied", "SEA")
            + _client_responses()
        )
        client = FlightPlanClient(allow_control=True, opener=opener)
        with patch("xplane_api.sources.flight_plan.uuid4") as uuid:
            uuid.return_value.hex = request_id
            result = client.direct_to(" sea ", timeout_seconds=1.0)

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["target_identifier"], "SEA")
        self.assertEqual(result["active_index"], 1)
        self.assertEqual(
            [request.get_method() for request in opener.requests],
            [
                "GET",
                "GET",
                "GET",
                "GET",
                "GET",
                "PATCH",
                "POST",
                "GET",
                "GET",
                "GET",
                "GET",
                "GET",
            ],
        )
        request_body = json.loads(opener.requests[5].data)
        self.assertEqual(
            base64.b64decode(request_body["data"]).decode(),
            f"{request_id}|SEA",
        )
        self.assertEqual(json.loads(opener.requests[6].data), {"duration": 0})

    def test_direct_to_accepts_null_success_responses_for_write_requests(self) -> None:
        request_id = "0123456789abcdef0123456789abcdef"
        responses = _direct_to_responses(request_id, "applied", "KBWI")
        responses[5] = None
        responses[6] = None
        readback = _snapshot()
        readback["entries"][1]["identifier"] = "KBWI"
        opener = SequenceOpener(responses + _client_responses(readback))
        client = FlightPlanClient(allow_control=True, opener=opener)
        with patch("xplane_api.sources.flight_plan.uuid4") as uuid:
            uuid.return_value.hex = request_id
            result = client.direct_to("KBWI", timeout_seconds=1.0)

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["target_identifier"], "KBWI")

    def test_direct_to_is_disabled_by_default_and_validates_before_writing(self) -> None:
        opener = SequenceOpener([])
        client = FlightPlanClient(opener=opener)
        with self.assertRaises(FlightPlanControlDisabledError):
            client.direct_to("KSEA")
        self.assertEqual(opener.requests, [])

        client = FlightPlanClient(allow_control=True, opener=opener)
        for identifier in ("", "bad target", "TOO_LONG_IDENTIFIER_123"):
            with self.subTest(identifier=identifier), self.assertRaises(
                FlightPlanTargetError
            ):
                client.direct_to(identifier)
        self.assertEqual(opener.requests, [])

    def test_direct_to_rejects_bridge_version_mismatch_before_writing(self) -> None:
        opener = SequenceOpener(_client_responses(version=2)[:2])
        client = FlightPlanClient(allow_control=True, opener=opener)
        with self.assertRaises(FlightPlanProtocolError):
            client.direct_to("KSEA")
        self.assertEqual(
            [request.get_method() for request in opener.requests],
            ["GET", "GET"],
        )

    def test_direct_to_reports_native_target_errors_without_readback(self) -> None:
        request_id = "0123456789abcdef0123456789abcdef"
        opener = SequenceOpener(
            _direct_to_responses(
                request_id,
                "ambiguous_target",
                "DUP",
                "target identifier matches multiple navigation entries",
            )
        )
        client = FlightPlanClient(allow_control=True, opener=opener)
        with patch("xplane_api.sources.flight_plan.uuid4") as uuid:
            uuid.return_value.hex = request_id
            with self.assertRaisesRegex(FlightPlanTargetError, "multiple"):
                client.direct_to("DUP")
        self.assertEqual(len(opener.requests), 8)

    def test_direct_to_rejects_result_for_a_different_target(self) -> None:
        request_id = "0123456789abcdef0123456789abcdef"
        opener = SequenceOpener(
            _direct_to_responses(request_id, "applied", "KLAX")
        )
        client = FlightPlanClient(allow_control=True, opener=opener)
        with patch("xplane_api.sources.flight_plan.uuid4") as uuid:
            uuid.return_value.hex = request_id
            with self.assertRaisesRegex(
                FlightPlanProtocolError, "target does not match"
            ):
                client.direct_to("KSEA")
        self.assertEqual(len(opener.requests), 8)

    def test_direct_to_rejects_malformed_result_payload(self) -> None:
        request_id = "0123456789abcdef0123456789abcdef"
        responses = _direct_to_responses(request_id, "applied", "KSEA")
        responses[-1] = {
            "data": base64.b64encode(b"[]").decode("ascii"),
        }
        opener = SequenceOpener(responses)
        client = FlightPlanClient(allow_control=True, opener=opener)
        with patch("xplane_api.sources.flight_plan.uuid4") as uuid:
            uuid.return_value.hex = request_id
            with self.assertRaisesRegex(
                FlightPlanProtocolError, "must decode to an object"
            ):
                client.direct_to("KSEA")
        self.assertEqual(len(opener.requests), 8)

    def test_direct_to_never_reports_success_without_active_target_readback(self) -> None:
        request_id = "0123456789abcdef0123456789abcdef"
        opener = SequenceOpener(
            _direct_to_responses(request_id, "applied", "KSEA")
            + _client_responses()
        )
        client = FlightPlanClient(allow_control=True, opener=opener)
        with patch("xplane_api.sources.flight_plan.uuid4") as uuid:
            uuid.return_value.hex = request_id
            with self.assertRaises(FlightPlanReadbackError):
                client.direct_to("KSEA", timeout_seconds=1.0)

    def test_resolves_dataref_ids_again_for_each_read(self) -> None:
        opener = SequenceOpener(
            _client_responses(version_id=11, snapshot_id=22)
            + _client_responses(version_id=31, snapshot_id=42)
        )
        client = FlightPlanClient(opener=opener)
        client.read_plan()
        client.read_plan()
        self.assertEqual(len(opener.requests), 8)
        self.assertIn("/datarefs/31/value", opener.requests[5].full_url)
        self.assertIn("/datarefs/42/value", opener.requests[7].full_url)

    def test_missing_bridge_is_explicitly_unavailable(self) -> None:
        opener = SequenceOpener([{"data": []}])
        with self.assertRaises(FlightPlanUnavailableError):
            FlightPlanClient(opener=opener).read_plan()

    def test_rejects_unsupported_version_and_malformed_snapshot(self) -> None:
        for responses in (
            _client_responses(version=2)[:2],
            _client_responses(snapshot_value="not base64"),
        ):
            with self.subTest(responses=responses), self.assertRaises(
                FlightPlanProtocolError
            ):
                FlightPlanClient(opener=SequenceOpener(responses)).read_plan()

    def test_timeout_is_reported_as_unavailable(self) -> None:
        def timeout_opener(*_args, **_kwargs):
            del _args, _kwargs
            raise TimeoutError("synthetic timeout")

        with self.assertRaises(FlightPlanUnavailableError):
            FlightPlanClient(opener=timeout_opener).read_plan()

    def test_rejects_invalid_client_settings(self) -> None:
        for timeout in (0, -1, math.nan, True):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                FlightPlanClient(timeout_seconds=timeout)
        for url in ("", "file:///tmp/xplane", "http://localhost/path?x=1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                FlightPlanClient(base_url=url)


if __name__ == "__main__":
    unittest.main()
