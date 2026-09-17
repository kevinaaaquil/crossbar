"""The harness contract and the bits every harness shares."""

from __future__ import annotations

from typing import Protocol

from crossbar.env import Environment
from crossbar.providers import Provider, ToolDef
from crossbar.tasks import Task
from crossbar.trace import Trajectory


class HarnessError(RuntimeError):
    """The harness itself failed, as opposed to the agent failing the task."""


class Harness(Protocol):
    """Anything that can drive a model through a task inside an environment."""

    id: str

    def run(
        self,
        task: Task,
        env: Environment,
        provider: Provider,
        repeat: int = 0,
        agent_id: str = "",
    ) -> Trajectory: ...


def tool_defs(env: Environment) -> list[ToolDef]:
    """Expose the environment's MCP tools in the shape a provider expects."""
    return [
        ToolDef(name=h.api_name, description=h.description, input_schema=h.input_schema)
        for h in env.tools()
    ]


def is_forbidden(task: Task, server: str, tool: str, api_name: str) -> bool:
    """True when the task's security block forbids this tool under any spelling."""
    forbidden = set(task.security.forbidden_tools)
    return bool(forbidden & {f"{server}.{tool}", api_name, tool, f"{server}.*"})
