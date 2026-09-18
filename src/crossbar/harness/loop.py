"""Think, act, observe, repeat.

The agent runtime. Tools are the union of every enabled Connector's tools, and
each call is routed back to whichever Connector owns it. This module must stay
free of any knowledge of *kinds* of connector — a test asserts it, because the
moment it knows, adding a capability stops being a registration and becomes a
rewrite.
"""

from __future__ import annotations

import time
from typing import Callable, Sequence

from crossbar.domain import Limits, Task
from crossbar.providers import (
    CompletionRequest,
    Message,
    ProviderError,
    ToolDef,
)
from crossbar.trace import RunStatus, StepEvent, ToolEvent, Trajectory

SYSTEM_PROMPT = (
    "You are an agent working against a live system through the tools you have "
    "been given. Use them to actually perform the task; do not merely describe "
    "what you would do. When the task is finished, reply with a short summary "
    "and no tool call."
)


class Harness:
    def __init__(
        self,
        system_prompt: str = SYSTEM_PROMPT,
        max_response_tokens: int = 2048,
        temperature: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.system_prompt = system_prompt
        self.max_response_tokens = max_response_tokens
        self.temperature = temperature
        self.clock = clock

    def run(
        self,
        task: Task,
        connectors: Sequence,
        model,
        attempt_id: str = "",
        repeat: int = 0,
    ) -> Trajectory:
        limits = task.limits or Limits()
        traj = Trajectory(task_id=task.id, agent_id=attempt_id or getattr(model, "model", ""),
                          repeat=repeat)
        routes, specs = _routes(connectors)
        tools = [
            ToolDef(name=spec.name, description=spec.description, input_schema=spec.input_schema)
            for spec in specs
        ]
        messages = [
            Message(role="system", content=self.system_prompt),
            Message(role="user", content=task.prompt),
        ]
        started = self.clock()
        step_index = 0

        while True:
            stop = self._limit_reached(traj, limits, started, step_index)
            if stop is not None:
                traj.finish(stop, _last_text(traj))
                return traj

            step_started = self.clock()
            try:
                response = model.complete(
                    CompletionRequest(
                        messages=list(messages),
                        tools=tools,
                        max_tokens=self.max_response_tokens,
                        temperature=self.temperature,
                    )
                )
            except ProviderError as exc:
                traj.finish(RunStatus.ERROR, _last_text(traj), error=str(exc))
                return traj

            traj.record_step(
                StepEvent(
                    index=step_index,
                    text=response.text,
                    tool_calls=tuple(response.tool_calls),
                    usage=response.usage,
                    duration_ms=int((self.clock() - step_started) * 1000),
                )
            )
            messages.append(
                Message(role="assistant", content=response.text,
                        tool_calls=tuple(response.tool_calls))
            )
            step_index += 1

            if not response.tool_calls:
                traj.finish(RunStatus.COMPLETED, response.text)
                return traj

            for call in response.tool_calls:
                event = self._execute(routes, traj, call, step_index - 1)
                messages.append(
                    Message(role="tool", content=event.result_text,
                            tool_call_id=call.id, is_error=event.is_error)
                )

    def _limit_reached(
        self, traj: Trajectory, limits: Limits, started: float, step_index: int
    ) -> RunStatus | None:
        if step_index >= limits.max_steps:
            return RunStatus.MAX_STEPS
        if self.clock() - started > limits.timeout_s:
            return RunStatus.TIMEOUT
        if traj.usage.total >= limits.max_tokens:
            return RunStatus.BUDGET_EXCEEDED
        return None

    def _execute(self, routes: dict, traj: Trajectory, call, step: int) -> ToolEvent:
        owner = routes.get(call.name)
        if owner is None:
            event = ToolEvent(
                step=step, api_name=call.name, server="", tool=call.name,
                arguments=dict(call.arguments),
                result_text=f"error: unknown tool {call.name!r}", is_error=True,
            )
            traj.record_tool(event)
            return event

        connector = owner
        started = self.clock()
        try:
            result = connector.call(call.name, call.arguments)
            text, is_error = result.text, result.is_error
        except Exception as exc:  # a connector failure is a tool error, not a crash
            text, is_error = f"error: {exc}", True

        event = ToolEvent(
            step=step,
            api_name=call.name,
            server=connector.name,
            tool=call.name,
            arguments=dict(call.arguments),
            result_text=text,
            is_error=is_error,
            duration_ms=int((self.clock() - started) * 1000),
        )
        traj.record_tool(event)
        return event


def _routes(connectors: Sequence) -> tuple[dict, list]:
    """Map every offered tool to the Connector that owns it.

    A later Connector never shadows an earlier one: the first to claim a name
    keeps it, so a collision cannot silently redirect a call somewhere
    unexpected.
    """
    routes: dict[str, object] = {}
    specs: list = []
    for connector in connectors:
        for spec in connector.tools():
            if spec.name in routes:
                continue
            routes[spec.name] = connector
            specs.append(spec)
    return routes, specs


def _last_text(traj: Trajectory) -> str:
    return traj.steps[-1].text if traj.steps else ""
