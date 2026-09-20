"""The MCP connector: MCP servers as agent tools and evidence probes."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from crossbar.connectors.base import ConnectorError, ToolResult, ToolSpec
from crossbar.connectors.registry import register
from crossbar.domain import ConnectorConfig
from crossbar.environment import EnvironmentHandle, Launch
from crossbar.mcpclient import McpError, McpStdioClient

_SAFE = re.compile(r"[^A-Za-z0-9_-]")


class McpConnector:
    """Runs a Task's MCP servers and exposes their tools."""

    name = "mcp"

    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config
        self.clients: dict[str, McpStdioClient] = {}
        self._tools: tuple[ToolSpec, ...] | None = None
        self.handle: EnvironmentHandle | None = None

    # -- lifecycle ---------------------------------------------------------

    def setup(self, handle: EnvironmentHandle) -> None:
        if self.clients:
            return
        servers = self.config.options.get("servers") or []
        if not servers:
            raise ConnectorError("the mcp connector needs at least one server")

        self.handle = handle
        started: dict[str, McpStdioClient] = {}
        try:
            for spec in servers:
                server_name = str(spec.get("name") or "")
                if not server_name:
                    raise ConnectorError("every mcp server needs a 'name'")
                launch = self.build_launch(handle, spec)
                client = McpStdioClient(
                    name=server_name,
                    command=launch.argv[0],
                    args=launch.argv[1:],
                    env=launch.env,
                    cwd=launch.cwd,
                )
                client.start()
                started[server_name] = client
        except (McpError, ConnectorError, IndexError) as exc:
            for client in started.values():
                client.stop()
            raise ConnectorError(str(exc)) from exc
        self.clients = started

    def teardown(self) -> None:
        for client in self.clients.values():
            client.stop()
        self.clients = {}
        self._tools = None

    @property
    def is_running(self) -> bool:
        return bool(self.clients) and all(c.is_running for c in self.clients.values())

    def build_launch(self, handle: EnvironmentHandle, spec: Mapping[str, Any]) -> Launch:
        """Everything needed to start one server under this Environment.

        The Environment, not this Connector, decides whether the server's
        ``env`` and ``cwd`` belong in the argv or on the process: under docker
        the server is inside the container, and anything applied here would
        stop at the ``docker exec`` client without a word."""
        command = str(spec.get("command") or "")
        if not handle.expand(command):
            raise ConnectorError(f"mcp server {spec.get('name')!r} has no 'command'")
        return handle.launch(
            command,
            tuple(spec.get("args") or ()),
            env=spec.get("env"),
            cwd=spec.get("cwd"),
        )

    def build_argv(self, handle: EnvironmentHandle, spec: Mapping[str, Any]) -> list[str]:
        """The command line alone. Kept for callers that only want the words."""
        return self.build_launch(handle, spec).argv

    # -- tools -------------------------------------------------------------

    def tools(self) -> tuple[ToolSpec, ...]:
        if self._tools is None:
            declared = set(self.config.read_only_tools)
            specs: list[ToolSpec] = []
            for server, client in self.clients.items():
                try:
                    listed = client.list_tools()
                except McpError as exc:
                    raise ConnectorError(f"could not list tools on {server!r}: {exc}") from exc
                for spec in listed:
                    api_name = _api_name(server, spec.name)
                    specs.append(
                        ToolSpec(
                            name=api_name,
                            description=spec.description,
                            input_schema=spec.input_schema,
                            read_only=_is_read_only(spec, api_name, declared),
                            connector=self.name,
                        )
                    )
            self._tools = tuple(specs)
        return self._tools

    def probes(self) -> tuple[ToolSpec, ...]:
        """Only tools established as read-only. A tool we cannot establish is
        never a probe: capture must not mutate what it measures."""
        return tuple(t for t in self.tools() if t.read_only)

    def call(self, tool: str, args: Mapping[str, Any] | None = None) -> ToolResult:
        server, name = self._route(tool)
        try:
            result = self.clients[server].call_tool(name, args or {})
        except McpError as exc:
            raise ConnectorError(str(exc)) from exc
        return ToolResult(
            text=result.text,
            is_error=result.is_error,
            structured=result.structured,
            raw=result.raw,
        )

    def probe(self, name: str, args: Mapping[str, Any] | None = None) -> ToolResult:
        spec = next((t for t in self.tools() if t.name == name), None)
        if spec is None:
            raise ConnectorError(f"unknown tool {name!r}")
        if not spec.read_only:
            raise ConnectorError(
                f"{name!r} is not read-only, so it cannot be used to capture evidence"
            )
        return self.call(name, args)

    # -- internals ---------------------------------------------------------

    def _route(self, tool: str) -> tuple[str, str]:
        if not self.clients:
            raise ConnectorError("the mcp connector is not set up")
        if not any(t.name == tool for t in self.tools()):
            raise ConnectorError(f"unknown tool {tool!r}")
        server, _, name = tool.partition("__")
        if server not in self.clients:
            raise ConnectorError(f"unknown tool {tool!r}")
        return server, name


def _api_name(server: str, tool: str) -> str:
    return f"{_SAFE.sub('_', server)}__{_SAFE.sub('_', tool)}"[:64]


def _is_read_only(spec, api_name: str, declared: set[str]) -> bool:
    """Precedence: the server's own annotation, then an explicit declaration in
    the Test, then no. Never guessed from the tool's name."""
    annotations = getattr(spec, "annotations", None) or {}
    if annotations.get("readOnlyHint") is True:
        return True
    return api_name in declared or spec.name in declared


register("mcp", McpConnector)
