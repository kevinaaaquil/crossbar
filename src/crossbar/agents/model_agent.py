"""A model driven through our own Harness. The controlled case."""

from __future__ import annotations

from typing import Sequence

from crossbar.domain import Task
from crossbar.harness import Harness
from crossbar.providers import Provider
from crossbar.trace import Trajectory


class ModelAgent:
    owns_harness = False

    def __init__(self, model_id: str, provider: Provider, harness: Harness | None = None) -> None:
        self.id = model_id
        self.provider = provider
        self.harness = harness or Harness()

    def run(self, task: Task, connectors: Sequence, repeat: int = 0) -> Trajectory:
        return self.harness.run(
            task, connectors, self.provider, attempt_id=self.id, repeat=repeat
        )
