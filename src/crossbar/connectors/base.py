"""The Connector contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from crossbar.environment import EnvironmentHandle


class ConnectorError(RuntimeError):
    """A connector could not be set up, or was asked for something it cannot do."""


@dataclass(frozen=True)
class ToolSpec:
    """A capability offered to the agent."""

    name: str
    """Flat, model-safe: ``server__tool``."""

    description: str
    input_schema: Mapping[str, Any]
    read_only: bool = False
    """Established read-only, and therefore safe to call during evidence
    capture. Never inferred from the name."""

    connector: str = ""


@dataclass(frozen=True)
class ToolResult:
    text: str
    is_error: bool = False
    structured: Any = None
    raw: Mapping[str, Any] = field(default_factory=dict)


class Connector(Protocol):
    """Anything that gives the agent a way to touch the Environment."""

    name: str

    handle: EnvironmentHandle | None
    """The Environment this connector was set up against. Public because an
    external agent CLI has to be handed the same workspace, or the state it
    leaves behind cannot be read back."""

    def setup(self, handle: EnvironmentHandle) -> None: ...
    def teardown(self) -> None: ...

    # For the agent: things it may do.
    def tools(self) -> Sequence[ToolSpec]: ...
    def call(self, tool: str, args: Mapping[str, Any]) -> ToolResult: ...

    # For evidence capture: things that only observe.
    def probes(self) -> Sequence[ToolSpec]: ...
    def probe(self, name: str, args: Mapping[str, Any]) -> ToolResult: ...
