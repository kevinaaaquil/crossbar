"""The Agent contract."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from crossbar.domain import Task
from crossbar.trace import Trajectory


@runtime_checkable
class Agent(Protocol):
    """Executes a Task against a set of Connectors and records what happened."""

    id: str

    owns_harness: bool
    """True for an external agent CLI that brings its own loop.

    It must reach the report: comparing an agent that owns its harness against a
    model driven by ours is a *product* comparison, not a controlled model
    comparison. A legitimate thing to measure, but a different thing, and the
    number means something other than the user may assume unless it is labelled.
    """

    def run(self, task: Task, connectors: Sequence, repeat: int = 0) -> Trajectory: ...
