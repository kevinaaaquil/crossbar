"""JSON-RPC 2.0 over a child process' stdin/stdout, MCP flavoured."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Mapping

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "crossbar", "version": "0.1.0"}
DEFAULT_TIMEOUT_S = 30.0


class McpError(RuntimeError):
    """Any transport, protocol or server-side JSON-RPC failure."""


@dataclass(frozen=True)
class ToolSpec:
    """One tool advertised by a server."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    server: str = ""

    @property
    def qualified_name(self) -> str:
        return f"{self.server}.{self.name}" if self.server else self.name


@dataclass(frozen=True)
class ToolResult:
    """The outcome of one ``tools/call``."""

    text: str
    is_error: bool = False
    structured: Any = None
    raw: Mapping[str, Any] = field(default_factory=dict)


class McpStdioClient:
    """Spawns an MCP server as a subprocess and speaks JSON-RPC to it."""

    def __init__(
        self,
        name: str,
        command: str,
        args: tuple[str, ...] | list[str] = (),
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.name = name
        self.command = command
        self.args = list(args)
        self.env = dict(env or {})
        self.cwd = cwd
        self.timeout_s = timeout_s

        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[dict] = queue.Queue()
        self._stderr: list[str] = []
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._next_id = 0
        self._tools: tuple[ToolSpec, ...] | None = None
        self.server_info: dict[str, Any] = {}
        self.protocol_version: str = ""
        self.last_request_id: int = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Launch the server and complete the MCP initialize handshake."""
        if self._process is not None:
            return
        env = {**os.environ, **self.env}
        try:
            self._process = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
                cwd=self.cwd,
            )
        except OSError as exc:
            raise McpError(f"could not start MCP server {self.name!r}: {exc}") from exc

        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_reader.start()

        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        self.server_info = dict(result.get("serverInfo") or {})
        self.protocol_version = str(result.get("protocolVersion") or "")
        self._notify("notifications/initialized", {})

    def stop(self) -> None:
        """Terminate the server; safe to call more than once."""
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        except OSError:
            pass

    def __enter__(self) -> "McpStdioClient":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    # -- MCP surface -------------------------------------------------------

    def list_tools(self) -> tuple[ToolSpec, ...]:
        """Tools this server offers, cached after the first call."""
        if self._tools is None:
            result = self._request("tools/list", {})
            self._tools = tuple(
                ToolSpec(
                    name=str(t.get("name", "")),
                    description=str(t.get("description", "")),
                    input_schema=dict(t.get("inputSchema") or {}),
                    server=self.name,
                )
                for t in result.get("tools", [])
            )
        return self._tools

    def call_tool(self, tool: str, arguments: Mapping[str, Any] | None = None) -> ToolResult:
        """Invoke one tool. Server-reported tool errors come back flagged, not raised."""
        result = self._request("tools/call", {"name": tool, "arguments": dict(arguments or {})})
        blocks = result.get("content") or []
        text = "\n".join(b.get("text", "") for b in blocks if isinstance(b, Mapping))
        return ToolResult(
            text=text,
            is_error=bool(result.get("isError", False)),
            structured=result.get("structuredContent"),
            raw=result,
        )

    # -- transport ---------------------------------------------------------

    def _request(self, method: str, params: Mapping[str, Any]) -> dict:
        if self._process is None:
            raise McpError(f"MCP server {self.name!r} is not started")
        self._next_id += 1
        self.last_request_id = self._next_id
        self._write({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": dict(params)})

        try:
            message = self._responses.get(timeout=self.timeout_s)
        except queue.Empty:
            raise McpError(
                f"MCP server {self.name!r} timed out after {self.timeout_s}s on {method}"
                f"{self._stderr_tail()}"
            ) from None
        if message is _EOF:
            raise McpError(
                f"MCP server {self.name!r} exited before answering {method}{self._stderr_tail()}"
            )
        if "error" in message:
            err = message["error"] or {}
            raise McpError(f"{self.name}.{method} failed: {err.get('message', err)}")
        return message.get("result") or {}

    def _notify(self, method: str, params: Mapping[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": dict(params)})

    def _write(self, payload: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise McpError(f"MCP server {self.name!r} is not started")
        try:
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise McpError(f"MCP server {self.name!r} closed its input: {exc}") from exc

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # servers sometimes log to stdout; ignore non-JSON
            if isinstance(message, dict) and "id" in message:
                self._responses.put(message)
        self._responses.put(_EOF)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._stderr.append(line.rstrip())
            del self._stderr[:-50]  # keep only the tail

    def _stderr_tail(self) -> str:
        if not self._stderr:
            return ""
        return "\n  stderr: " + "\n  stderr: ".join(self._stderr[-10:])


_EOF: dict = {"__eof__": True}
