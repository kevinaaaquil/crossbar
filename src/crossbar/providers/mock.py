"""A deterministic stand-in model, so the bench runs before any key is set.

It replays the task's own ``demo`` script at a configurable skill level. That is
enough to show the thing the product exists to show - a cheaper configuration
crossing over a dearer one, with an interval attached - without a network call.
Real sweeps never use it; it is a demo and test instrument.
"""

from __future__ import annotations

import random
import zlib
from typing import Any

from crossbar.providers.base import (
    CompletionRequest,
    CompletionResponse,
    ToolCall,
    Usage,
)


def _asked_to_verify(request: CompletionRequest) -> bool:
    """Did the harness just tell us to check our work?"""
    for message in reversed(list(request.messages)):
        if message.role == "user":
            return "verify" in message.content.lower()
    return False


class MockProvider:
    def __init__(
        self,
        model: str,
        task: Any = None,
        skill: float = 1.0,
        seed: int = 0,
        recover: float = 0.8,
    ) -> None:
        self.model = model
        self.task = task
        self.skill = min(1.0, max(0.0, float(skill)))
        self.recover = min(1.0, max(0.0, float(recover)))
        self.seed = seed
        # crc32, not hash(): Python salts string hashing per process, and a
        # sweep that cannot be replayed in a fresh interpreter is not reproducible.
        key = f"{seed}|{model}|{getattr(task, 'id', '')}".encode()
        self._rng = random.Random(zlib.crc32(key))
        self._step = 0
        self._call_id = 0
        self._skipped: list[Any] = []
        self._verifying = False

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        demo = getattr(self.task, "demo", None)
        steps = list(demo.steps) if demo else []
        final_text = demo.final if demo else "Nothing to do."

        if _asked_to_verify(request):
            self._verifying = True

        # A verify turn is a second chance at the steps that were skipped.
        # That is the whole mechanism by which a harness lifts a weak model.
        while self._verifying and self._skipped:
            step = self._skipped.pop(0)
            if self._rng.random() <= self.recover:
                return self._call(step, "Re-checking: this step was missing.")

        while self._step < len(steps):
            step = steps[self._step]
            self._step += 1
            if self._rng.random() <= self.skill:
                return self._call(step, step.thought)
            # A weaker model skips a step and reports success anyway: the
            # artifact-commitment failure mode, which is what we want to measure.
            self._skipped.append(step)
        return CompletionResponse(text=final_text, usage=Usage(300, 40), stop_reason="stop")

    def _call(self, step: Any, thought: str) -> CompletionResponse:
        self._call_id += 1
        return CompletionResponse(
            text=thought,
            tool_calls=[
                ToolCall(id=f"mock_{self._call_id}", name=step.tool, arguments=dict(step.args))
            ],
            usage=Usage(400 + 20 * self._call_id, 60),
            stop_reason="tool_calls",
        )
