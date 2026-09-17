"""Immutable task-pack data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

CHECK_TYPES = ("mcp_state", "tool_called", "final_text", "no_tool_errors")
ENV_KINDS = ("local", "docker")
MATCH_MODES = ("exact", "subset", "contains", "regex")


@dataclass(frozen=True)
class ServerSpec:
    """One MCP server the agent will be given access to."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str | None = None


@dataclass(frozen=True)
class EnvironmentSpec:
    """Where the task's MCP servers run."""

    kind: str
    servers: tuple[ServerSpec, ...]
    image: str | None = None

    def server(self, name: str) -> ServerSpec | None:
        return next((s for s in self.servers if s.name == name), None)


@dataclass(frozen=True)
class SecuritySpec:
    """Hard constraints. Violating any of these zeroes the task score."""

    forbidden_tools: tuple[str, ...] = ()
    max_tool_calls: int | None = None


@dataclass(frozen=True)
class Check:
    """One deterministic assertion against the post-run state or transcript."""

    type: str
    server: str | None = None
    tool: str | None = None
    args: Mapping[str, Any] = field(default_factory=dict)
    expect: Any = None
    match: str = "subset"
    value: Any = None
    min_times: int = 1
    max_times: int | None = None
    weight: float = 1.0
    description: str = ""

    def label(self) -> str:
        if self.description:
            return self.description
        if self.type == "tool_called":
            return f"tool_called {self.server}.{self.tool}"
        if self.type == "mcp_state":
            return f"mcp_state {self.server}.{self.tool}"
        if self.type == "final_text":
            return f"final_text {self.match} {self.value!r}"
        return self.type


@dataclass(frozen=True)
class DemoStep:
    """One scripted action for the built-in mock model."""

    tool: str
    args: Mapping[str, Any] = field(default_factory=dict)
    thought: str = ""


@dataclass(frozen=True)
class DemoScript:
    """How a competent agent would solve this task, for offline demos only.

    Real models never see it. It exists so the bench can be run, shown and
    tested end to end before anyone wires up an endpoint.
    """

    steps: tuple[DemoStep, ...] = ()
    final: str = "Done."


@dataclass(frozen=True)
class Task:
    """One unit of work with a starting state and a definition of done."""

    id: str
    prompt: str
    environment: EnvironmentSpec
    checks: tuple[Check, ...]
    name: str = ""
    category: str = "mcp-workflow"
    timeout_s: int = 120
    max_steps: int = 20
    budget_tokens: int = 100_000
    security: SecuritySpec = field(default_factory=SecuritySpec)
    demo: "DemoScript | None" = None
    source: str = "<memory>"

    @property
    def title(self) -> str:
        return self.name or self.id


@dataclass(frozen=True)
class TaskPack:
    """A directory of tasks that get run together."""

    tasks: tuple[Task, ...]
    name: str = ""
    description: str = ""
    path: str = ""

    def __iter__(self):
        return iter(self.tasks)

    def __len__(self) -> int:
        return len(self.tasks)

    def by_id(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)
