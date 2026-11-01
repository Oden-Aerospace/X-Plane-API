"""Completeness check tying the agent API reference to the public surface.

The agent-facing documentation must mention every public panel method, every
mode/button/selector enum member, and every ``AutopilotState`` field, so the
document cannot silently drift from the API (spec AC-7).
"""

import dataclasses
import pathlib
import unittest

from xplane_api.autopilot.panel import AutopilotPanel, ConfirmationResult
from xplane_api.models.autopilot import (
    AfcsButton,
    AfcsSelector,
    AutopilotState,
    LateralMode,
    VerticalMode,
)
from xplane_api.models.state import FieldStatus

_DOC_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "docs"
    / "autopilot-agent-api.md"
)


class AgentDocumentationCompletenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = _DOC_PATH.read_text(encoding="utf-8")

    def assert_documented(self, name: str, kind: str) -> None:
        self.assertIn(
            name,
            self.doc,
            f"{kind} {name!r} is missing from docs/autopilot-agent-api.md",
        )

    def test_every_public_panel_method_is_documented(self) -> None:
        methods = [
            name
            for name in vars(AutopilotPanel)
            if not name.startswith("_")
            and callable(getattr(AutopilotPanel, name))
        ]
        self.assertTrue(methods)
        for name in methods:
            self.assert_documented(name, "panel method")

    def test_every_autopilot_state_field_is_documented(self) -> None:
        for field in dataclasses.fields(AutopilotState):
            self.assert_documented(field.name, "AutopilotState field")

    def test_every_enum_member_is_documented(self) -> None:
        for enum_type in (
            LateralMode,
            VerticalMode,
            AfcsButton,
            AfcsSelector,
            FieldStatus,
        ):
            for member in enum_type:
                self.assert_documented(
                    member.value, f"{enum_type.__name__} member"
                )

    def test_confirmation_result_fields_are_documented(self) -> None:
        for field in dataclasses.fields(ConfirmationResult):
            self.assert_documented(field.name, "ConfirmationResult field")

    def test_core_contract_statements_are_present(self) -> None:
        for required in (
            "annunciated state is truth",
            "timeout_seconds",
            "stale_after_seconds",
            "poll_interval_seconds",
            "frequency_hz",
            "not certified avionics",
        ):
            self.assert_documented(required, "contract statement")


if __name__ == "__main__":
    unittest.main()
