"""Harnesses: everything wrapped around a model that turns text into actions.

A score belongs to the pair (model, harness), never to the model alone, so the
harness is a first-class axis of the sweep rather than a hidden constant.
"""

from crossbar.harness.base import Harness, HarnessError
from crossbar.harness.claude_code import ClaudeCodeHarness
from crossbar.harness.react import ReactHarness
from crossbar.harness.single_shot import SingleShotHarness

__all__ = [
    "ClaudeCodeHarness",
    "Harness",
    "HarnessError",
    "ReactHarness",
    "SingleShotHarness",
]
