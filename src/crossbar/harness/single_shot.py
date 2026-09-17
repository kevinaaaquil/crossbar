"""One call, no tools.

The floor of the comparison. If a wrapped agent cannot beat this on your task
pack, the wrapper is not earning its tokens.
"""

from __future__ import annotations

import time
from typing import Callable

from crossbar.env import Environment
from crossbar.providers import CompletionRequest, Message, Provider, ProviderError
from crossbar.tasks import Task
from crossbar.trace import RunStatus, StepEvent, Trajectory

SYSTEM_PROMPT = "Answer the user's request as completely as you can."


class SingleShotHarness:
    id = "single-shot"

    def __init__(
        self,
        system_prompt: str = SYSTEM_PROMPT,
        max_tokens: int = 2048,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        self.clock = clock

    def run(
        self,
        task: Task,
        env: Environment,
        provider: Provider,
        repeat: int = 0,
        agent_id: str = "",
    ) -> Trajectory:
        traj = Trajectory(task_id=task.id, agent_id=agent_id or self.id, repeat=repeat)
        started = self.clock()
        try:
            response = provider.complete(
                CompletionRequest(
                    messages=[
                        Message(role="system", content=self.system_prompt),
                        Message(role="user", content=task.prompt),
                    ],
                    tools=[],
                    max_tokens=self.max_tokens,
                )
            )
        except ProviderError as exc:
            traj.finish(RunStatus.ERROR, "", error=str(exc))
            return traj

        traj.record_step(
            StepEvent(
                index=0,
                text=response.text,
                usage=response.usage,
                duration_ms=int((self.clock() - started) * 1000),
            )
        )
        traj.finish(RunStatus.COMPLETED, response.text)
        return traj
