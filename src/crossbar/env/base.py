"""Shared environment behaviour: tool aggregation, routing, teardown."""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Mapping

from crossbar.mcpclient import McpError, McpStdioClient, ToolResult, ToolSpec
from crossbar.tasks import EnvironmentSpec, ServerSpec

_SAFE = re.compile(r"[^A-Za-z0-9_-]")
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class EnvironmentError_(RuntimeError):
    """Environment could not be created, reached, or torn down.

    Named with a trailing underscore to avoid shadowing the builtin.
    """


@dataclass(frozen=True)
class ToolHandle:
    """A tool as the model sees it: flat name, schema, and where to send the call."""

    api_name: str
    server: str
    tool: str
    description: str
    input_schema: Mapping[str, Any]

    @property
    def qualified_name(self) -> str:
        return f"{self.server}.{self.tool}"


class Environment:
    """Base class: subclasses only have to say how a server gets launched."""

    WORKSPACE_VAR = "CROSSBAR_WORKSPACE"
    """Every server is told where the run's scratch directory is, so state that
    must outlive the agent's own client (for post-run checks) has somewhere to go."""

    def __init__(self, spec: EnvironmentSpec, workspace: str | None = None) -> None:
        self.spec = spec
        self.clients: dict[str, McpStdioClient] = {}
        self.workspace: str = workspace or ""
        self._owns_workspace = workspace is None
        self._tools: tuple[ToolHandle, ...] | None = None

    def server_env(self, server: ServerSpec) -> dict[str, str]:
        """Environment variables for one server: the task's own, plus the workspace."""
        env = {k: self.expand(v) for k, v in server.env.items()}
        if self.workspace:
            env.setdefault(self.WORKSPACE_VAR, self.workspace)
        return env

    def variables(self) -> dict[str, str]:
        """What ``${...}`` in a task's server command can refer to.

        ``CROSSBAR_PYTHON`` is always the interpreter running crossbar, so a
        task pack can launch a Python MCP server without guessing whether this
        machine calls it python, python3 or something inside a virtualenv.
        """
        variables = dict(os.environ)
        variables["CROSSBAR_PYTHON"] = sys.executable
        if self.workspace:
            variables[self.WORKSPACE_VAR] = self.workspace
        return variables

    def expand(self, text: str) -> str:
        """Substitute ``${VAR}``; unknown names are left untouched, not blanked."""
        variables = self.variables()
        return _VAR.sub(lambda m: variables.get(m.group(1), m.group(0)), text)

    def expanded_command(self, server: ServerSpec) -> tuple[str, list[str]]:
        return self.expand(server.command), [self.expand(a) for a in server.args]

    # -- subclass hook -----------------------------------------------------

    def _make_client(self, server: ServerSpec) -> McpStdioClient:
        raise NotImplementedError

    def _prepare(self) -> None:
        """Anything that must happen before servers start (e.g. run a container)."""

    def _cleanup(self) -> None:
        """Anything that must happen after servers stop."""

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Bring the whole environment up, rolling back fully on any failure."""
        if self.clients:
            return
        if not self.workspace:
            self.workspace = tempfile.mkdtemp(prefix="crossbar-ws-")
        else:
            os.makedirs(self.workspace, exist_ok=True)
        self._prepare()
        started: dict[str, McpStdioClient] = {}
        try:
            for server in self.spec.servers:
                client = self._make_client(server)
                client.start()
                started[server.name] = client
        except McpError as exc:
            for client in started.values():
                client.stop()
            self._cleanup()
            raise EnvironmentError_(str(exc)) from exc
        self.clients = started

    def stop(self) -> None:
        """Tear everything down; safe to call before start or twice."""
        for client in self.clients.values():
            client.stop()
        self.clients = {}
        self._tools = None
        self._cleanup()
        if self._owns_workspace and self.workspace:
            shutil.rmtree(self.workspace, ignore_errors=True)
            self.workspace = ""

    def __enter__(self) -> "Environment":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # -- tools -------------------------------------------------------------

    def tools(self) -> tuple[ToolHandle, ...]:
        """Every tool from every server, flattened into model-safe names."""
        if self._tools is None:
            handles: list[ToolHandle] = []
            for name, client in self.clients.items():
                try:
                    specs = client.list_tools()
                except McpError as exc:
                    raise EnvironmentError_(f"could not list tools on {name!r}: {exc}") from exc
                handles.extend(self._handle(name, spec) for spec in specs)
            self._tools = tuple(handles)
        return self._tools

    def call(self, name: str, arguments: Mapping[str, Any] | None = None) -> ToolResult:
        """Invoke a tool by api name (``server__tool``), dotted name, or bare name."""
        handle = self.resolve(name)
        if handle is None:
            raise EnvironmentError_(f"unknown tool {name!r}")
        client = self.clients.get(handle.server)
        if client is None:
            raise EnvironmentError_(f"server {handle.server!r} is not running")
        try:
            return client.call_tool(handle.tool, arguments or {})
        except McpError as exc:
            raise EnvironmentError_(str(exc)) from exc

    def resolve(self, name: str) -> ToolHandle | None:
        """Map any accepted spelling of a tool name onto a handle."""
        tools = self.tools()
        for tool in tools:
            if name in (tool.api_name, tool.qualified_name):
                return tool
        bare = [t for t in tools if t.tool == name]
        if len(bare) == 1:
            return bare[0]
        return None

    @staticmethod
    def _handle(server: str, spec: ToolSpec) -> ToolHandle:
        api_name = f"{_SAFE.sub('_', server)}__{_SAFE.sub('_', spec.name)}"[:64]
        return ToolHandle(
            api_name=api_name,
            server=server,
            tool=spec.name,
            description=spec.description,
            input_schema=spec.input_schema,
        )
