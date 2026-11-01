"""Autopilot mode-controller facade mimicking a modern small-aircraft AFCS."""

from xplane_api.autopilot.panel import (
    BUTTON_COMMANDS,
    SELECTOR_RANGES,
    AutopilotPanel,
    ConfirmationResult,
)

__all__ = [
    "AutopilotPanel",
    "BUTTON_COMMANDS",
    "ConfirmationResult",
    "SELECTOR_RANGES",
]
