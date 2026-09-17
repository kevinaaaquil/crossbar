"""A plain tool-calling loop: think, call MCP tools, observe, repeat.

This is the control condition of the whole bench. It is deliberately simple, so
that when a fancier harness wins you can say what the extra machinery bought.
"""

from __future__ import annotations

import time
from typing import Callable

from crossbar.env import Environment, EnvironmentError_
from crossbar.harness.base import is_forbidden, tool_defs
from crossbar.providers import (
    CompletionRequest,
    Message,
    Provider,
    ProviderError,
)
from crossbar.tasks import Task
from crossbar.trace import RunStatus, StepEvent, ToolEvent, Trajectory

SYSTEM_PROMPT = (
    "You are an agent with access to tools from one or more MCP servers. "
    "Use the tools to actually perform the task; do not merely describe what you "
    "would do. When the task is finished, reply with a short summary and no tool call."
)

VERIFY_PROMPT = (
    "Before you finish: verify your work. Re-read the task, use the tools to check "
    "the resulting state, and fix anything that is missing or wrong. "
    "If everything is correct, say so and stop."
)


class ReactHarness:
    """Model + MCP tools in a loop.

    ``verify=True`` gives the second harness variant on the sweep axis: identical
    model, one forced self-check turn before the run is allowed to end.
    """

    def __init__(
        self,
        verify: bool = False,
        system_prompt: str = SYSTEM_PROMPT,
        max_tokens: int = 2048,
        temperature: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.verify = verify
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.clock = clock

    @property
    def id(self) -> str:
        return "react-plus-verify" if self.verify else "react"

    def run(
        self,
        task: Task,
        env: Environment,
        provider: Provider,
        repeat: int = 0,
        agent_id: str = "",
    ) -> Trajectory:
        traj = Trajectory(task_id=task.id, agent_id=agent_id or self.id, repeat=repeat)
        messages = [
            Message(role="system", content=self.system_prompt),
            Message(role="user", content=task.prompt),
        ]
        tools = tool_defs(env)
        started = self.clock()
        verified = not self.verify
        step_index = 0

        while True:
            if step_index >= task.max_steps:
                traj.finish(RunStatus.MAX_STEPS, _last_text(traj))
                return traj
            if self.clock() - started > task.timeout_s:
                traj.finish(RunStatus.TIMEOUT, _last_text(traj))
                return traj
            if traj.usage.total >= task.budget_tokens:
                traj.finish(RunStatus.BUDGET_EXCEEDED, _last_text(traj))
                return traj

            step_started = self.clock()
            try:
                response = provider.complete(
                    CompletionRequest(
                        messages=list(messages),
                        tools=tools,
                        max_tokens=self.max_tokens,
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
                Message(role="assistant", content=response.text, tool_calls=tuple(response.tool_calls))
            )
            step_index += 1

            if not response.tool_calls:
                if not verified:
                    verified = True
                    messages.append(Message(role="user", content=VERIFY_PROMPT))
                    continue
                traj.finish(RunStatus.COMPLETED, response.text)
                return traj

            for call in response.tool_calls:
                event = self._execute(task, env, traj, call, step_index - 1)
                messages.append(
                    Message(
                        role="tool",
                        content=event.result_text,
                        tool_call_id=call.id,
                        is_error=event.is_error,
                    )
                )

    def _execute(self, task: Task, env: Environment, traj: Trajectory, call, step: int) -> ToolEvent:
        handle = env.resolve(call.name)
        server = handle.server if handle else ""
        tool = handle.tool if handle else call.name

        if handle is None:
            event = ToolEvent(
                step=step,
                api_name=call.name,
                server=server,
                tool=tool,
                arguments=dict(call.arguments),
                result_text=f"error: unknown tool {call.name!r}",
                is_error=True,
            )
            traj.record_tool(event)
            return event

        if is_forbidden(task, server, tool, handle.api_name):
            reason = f"called forbidden tool {server}.{tool}"
            traj.record_violation(reason)
            event = ToolEvent(
                step=step,
                api_name=handle.api_name,
                server=server,
                tool=tool,
                arguments=dict(call.arguments),
                result_text=f"error: {reason} (blocked by the task's security policy)",
                is_error=True,
                blocked=True,
                blocked_reason=reason,
            )
            traj.record_tool(event)
            return event

        limit = task.security.max_tool_calls
        if limit is not None and traj.tool_call_count >= limit:
            reason = f"exceeded max_tool_calls ({limit})"
            traj.record_violation(reason)
            event = ToolEvent(
                step=step,
                api_name=handle.api_name,
                server=server,
                tool=tool,
                arguments=dict(call.arguments),
                result_text=f"error: {reason}",
                is_error=True,
                blocked=True,
                blocked_reason=reason,
            )
            traj.record_tool(event)
            return event

        call_started = self.clock()
        try:
            result = env.call(handle.api_name, call.arguments)
            text, is_error = result.text, result.is_error
        except EnvironmentError_ as exc:
            text, is_error = f"error: {exc}", True

        event = ToolEvent(
            step=step,
            api_name=handle.api_name,
            server=server,
            tool=tool,
            arguments=dict(call.arguments),
            result_text=text,
            is_error=is_error,
            duration_ms=int((self.clock() - call_started) * 1000),
        )
        traj.record_tool(event)
        return event


def _last_text(traj: Trajectory) -> str:
    return traj.steps[-1].text if traj.steps else ""
