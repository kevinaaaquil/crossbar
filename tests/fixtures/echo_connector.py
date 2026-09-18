"""A second, non-MCP connector used to prove the harness is connector-agnostic.

If the harness ever grows an assumption that connectors are MCP servers, the
tests that use this one break.
"""

from __future__ import annotations

from typing import Any, Mapping

from crossbar.connectors import ConnectorError, ToolResult, ToolSpec
from crossbar.environment import EnvironmentHandle


class EchoConnector:
    """Two tools: one that echoes, one read-only that reports what it has seen."""

    name = "echo"

    def __init__(self, config=None) -> None:
        self.config = config
        self.seen: list[str] = []
        self._handle: EnvironmentHandle | None = None

    def setup(self, handle: EnvironmentHandle) -> None:
        self._handle = handle

    def teardown(self) -> None:
        self._handle = None

    def tools(self) -> tuple[ToolSpec, ...]:
        return (
            ToolSpec(
                name="echo__say",
                description="Echo a message back.",
                input_schema={"type": "object", "properties": {"message": {"type": "string"}}},
                connector=self.name,
            ),
            ToolSpec(
                name="echo__history",
                description="Everything said so far.",
                input_schema={"type": "object", "properties": {}},
                read_only=True,
                connector=self.name,
            ),
        )

    def probes(self) -> tuple[ToolSpec, ...]:
        return tuple(t for t in self.tools() if t.read_only)

    def call(self, tool: str, args: Mapping[str, Any] | None = None) -> ToolResult:
        args = args or {}
        if tool == "echo__say":
            message = str(args.get("message", ""))
            self.seen.append(message)
            return ToolResult(text=f"echo: {message}")
        if tool == "echo__history":
            return ToolResult(text="\n".join(self.seen), structured={"seen": list(self.seen)})
        raise ConnectorError(f"unknown tool {tool!r}")

    def probe(self, name: str, args: Mapping[str, Any] | None = None) -> ToolResult:
        spec = next((t for t in self.tools() if t.name == name), None)
        if spec is None:
            raise ConnectorError(f"unknown tool {name!r}")
        if not spec.read_only:
            raise ConnectorError(f"{name!r} is not read-only")
        return self.call(name, args)
