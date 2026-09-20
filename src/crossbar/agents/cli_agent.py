"""An external agent CLI, driven headless.

Claude Code and its relatives bring their own loop: their own prompting, context
management, retry behaviour and tool policy. We hand one the task and the same
MCP servers, then read back what it did.

**It brings its own harness**, which is why `owns_harness` is True. Two
consequences follow, and neither is optional:

* Comparing it against a model driven by our Harness is a *product* comparison,
  not a controlled model comparison. The report has to label it.
* It launches its own copies of the task's MCP servers, so crossbar's own
  connectors never see what it did. The orchestrator must reconnect before
  capturing evidence.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from typing import Any, Sequence

from crossbar.domain import Limits, Task
from crossbar.providers import ToolCall, Usage
from crossbar.trace import RunStatus, StepEvent, ToolEvent, Trajectory

MCP_TOOL_PREFIX = "mcp__"
BUILTIN_SERVER = "cli-builtin"


AUTH_KINDS = ("api-key", "subscription")


class CliAgent:
    """Runs an agent CLI in print mode with only the Task's MCP servers attached."""

    owns_harness = True

    def __init__(
        self,
        model_id: str,
        model: str | None = None,
        claude_bin: str = "claude",
        auth: str = "api-key",
        bare: bool | None = None,
        permission_mode: str = "bypassPermissions",
        extra_args: Sequence[str] = (),
    ) -> None:
        self.id = model_id
        self.model = model
        self.claude_bin = claude_bin
        if auth not in AUTH_KINDS:
            raise ValueError(
                f"unknown auth {auth!r} for the claude CLI; use one of "
                + ", ".join(sorted(AUTH_KINDS))
            )
        self.auth = auth
        """How the CLI authenticates. ``api-key`` uses ANTHROPIC_API_KEY and
        runs ``--bare``. ``subscription`` uses the operator's OAuth login, which
        ``--bare`` refuses to read -- see ``bare`` below."""

        self.bare = (auth == "api-key") if bare is None else bare
        """Run with `--bare`: no hooks, no skills, no auto-memory, no plugin
        sync. Without it the operator's own configuration joins the run — on a
        real machine this fired SessionStart hooks and burned two turns loading
        personal skills before the model answered anything. That is not the
        CLI's behaviour, it is that laptop's, and it does not belong in a
        measurement.

        The cost: `--bare` reads Anthropic credentials only from
        ANTHROPIC_API_KEY, never from an OAuth session, so a subscription login
        is not enough. ``auth="subscription"`` trades it away and shuts off by
        flag what it can -- see `_argv`.
        """
        self.permission_mode = permission_mode
        self.extra_args = list(extra_args)

    def run(self, task: Task, connectors: Sequence, repeat: int = 0) -> Trajectory:
        traj = Trajectory(task_id=task.id, agent_id=self.id, repeat=repeat)
        limits = task.limits or Limits()
        config_path = _write_mcp_config(task, connectors)
        started = time.monotonic()

        try:
            argv = self._argv(task, connectors, config_path, limits)
            try:
                proc = subprocess.run(
                    argv, capture_output=True, text=True, timeout=limits.timeout_s
                )
            except FileNotFoundError:
                traj.finish(
                    RunStatus.ERROR,
                    error=f"the claude CLI was not found at {self.claude_bin!r}; "
                    "install it, or point this model at an API endpoint instead",
                )
                return traj
            except subprocess.TimeoutExpired:
                traj.finish(RunStatus.TIMEOUT, error=f"claude exceeded {limits.timeout_s}s")
                return traj

            _parse_stream(proc.stdout, traj)
            if traj.status is RunStatus.RUNNING:
                detail = (proc.stderr or proc.stdout or "").strip()[-600:]
                traj.finish(
                    RunStatus.ERROR,
                    error=(
                        f"claude exited {proc.returncode}: {detail}"
                        if proc.returncode
                        else f"claude produced no result: {detail}"
                    ),
                )
            traj.wall_time_s = max(traj.wall_time_s, time.monotonic() - started)
            return traj
        finally:
            try:
                os.unlink(config_path)
            except OSError:
                pass

    def _argv(self, task: Task, connectors: Sequence, config_path: str, limits: Limits) -> list[str]:
        argv = [self.claude_bin]
        if self.bare:
            argv.append("--bare")
        else:
            # Without --bare the operator's environment comes along. Measured
            # against the real CLI (2.1.274), these two take out the hooks and
            # the skills; a global CLAUDE.md still loads, and there is no flag
            # for it, so the report says so rather than pretending otherwise.
            argv += [
                "--disable-slash-commands",
                "--settings", json.dumps({"disableAllHooks": True}),
            ]
        argv += [
            "-p", task.prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--mcp-config", config_path,
            "--strict-mcp-config",
            "--permission-mode", self.permission_mode,
            "--max-turns", str(limits.max_steps),
            "--allowedTools", ",".join(_allowed_tools(connectors)),
        ]
        if self.model:
            argv += ["--model", self.model]
        return argv + self.extra_args


def _allowed_tools(connectors: Sequence) -> list[str]:
    """Only the Task's own tools.

    Leaving the CLI's built-ins out is what keeps an MCP task an MCP task: the
    agent cannot sidestep the server by shelling out.
    """
    allowed = []
    for connector in connectors:
        for spec in connector.tools():
            server, _, tool = spec.name.partition("__")
            allowed.append(f"{MCP_TOOL_PREFIX}{server}__{tool}")
    return allowed


def _write_mcp_config(task: Task, connectors: Sequence) -> str:
    """Hand the CLI the same servers, pointed at the same workspace."""
    handle = next((c.handle for c in connectors if getattr(c, "handle", None)), None)
    servers: dict[str, Any] = {}

    for config in (task.environment.connectors if task.environment else ()):
        if config.name != "mcp":
            continue
        for spec in config.options.get("servers") or []:
            name = str(spec.get("name") or "")
            command = handle.expand(str(spec.get("command", ""))) if handle else spec.get("command")
            args = [handle.expand(str(a)) for a in spec.get("args") or ()] if handle else list(
                spec.get("args") or ()
            )
            entry: dict[str, Any] = {"command": command, "args": args}
            env = handle.server_env(spec.get("env")) if handle else dict(spec.get("env") or {})
            if env:
                entry["env"] = env
            servers[name] = entry

    handle_file = tempfile.NamedTemporaryFile(
        "w", suffix=".mcp.json", prefix="crossbar-", delete=False
    )
    with handle_file:
        json.dump({"mcpServers": servers}, handle_file)
    return handle_file.name


def _parse_stream(stdout: str, traj: Trajectory) -> None:
    """Turn the CLI's stream-json into steps, tool events and a verdict.

    Written against the real stream rather than an assumed one: unknown event
    types appear routinely (hooks, rate-limit notices, thinking-token counts)
    and must be skipped rather than treated as a problem.
    """
    pending: dict[str, ToolEvent] = {}
    step_index = 0

    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")

        if kind == "assistant":
            message = event.get("message") or {}
            blocks = message.get("content") or []
            # "thinking" blocks are not the answer, and must not be read as one.
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            calls: list[ToolCall] = []
            for block in blocks:
                if block.get("type") != "tool_use":
                    continue
                name = str(block.get("name") or "")
                server, tool = _split_tool_name(name)
                call_id = str(block.get("id") or f"tu_{len(pending)}")
                arguments = dict(block.get("input") or {})
                calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
                tool_event = ToolEvent(
                    step=step_index, api_name=name, server=server, tool=tool,
                    arguments=arguments,
                )
                pending[call_id] = tool_event
                traj.record_tool(tool_event)
            traj.record_step(
                StepEvent(
                    index=step_index,
                    text=text,
                    tool_calls=tuple(calls),
                    usage=_usage(message.get("usage")),
                )
            )
            step_index += 1

        elif kind == "user":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") != "tool_result":
                    continue
                target = pending.get(str(block.get("tool_use_id") or ""))
                if target is None:
                    continue
                target.result_text = _result_text(block.get("content"))
                target.is_error = bool(block.get("is_error", False))

        elif kind == "result":
            traj.reported_cost_usd = float(event.get("total_cost_usd") or 0.0)
            usage = _usage(event.get("usage"))
            if usage.total:
                # The result event carries the authoritative totals for the run.
                traj.steps = [
                    StepEvent(index=s.index, text=s.text, tool_calls=s.tool_calls,
                              usage=Usage(), duration_ms=s.duration_ms)
                    for s in traj.steps
                ] or [StepEvent(index=0)]
                traj.steps[-1] = StepEvent(
                    index=traj.steps[-1].index,
                    text=traj.steps[-1].text,
                    tool_calls=traj.steps[-1].tool_calls,
                    usage=usage,
                    duration_ms=traj.steps[-1].duration_ms,
                )

            subtype = str(event.get("subtype") or "")
            text = str(event.get("result") or "")
            if subtype.startswith("error_max_turns"):
                status, error = RunStatus.MAX_STEPS, "the CLI ran out of turns"
            elif event.get("is_error"):
                # Observed on the real CLI: subtype "success" alongside
                # is_error true. The flag is authoritative, not the subtype.
                status, error = RunStatus.ERROR, text or subtype or "the CLI reported an error"
            else:
                status, error = RunStatus.COMPLETED, ""
            traj.finish(status, final_text=text, error=error)
            if event.get("duration_ms"):
                traj.wall_time_s = float(event["duration_ms"]) / 1000.0


def _usage(raw: Any) -> Usage:
    """Total input includes cached tokens.

    The CLI reports `cache_creation_input_tokens` and `cache_read_input_tokens`
    separately from `input_tokens`, and on a real run they dwarf it. Counting
    only `input_tokens` understates usage by orders of magnitude.
    """
    raw = raw or {}
    return Usage(
        input_tokens=int(raw.get("input_tokens") or 0)
        + int(raw.get("cache_creation_input_tokens") or 0)
        + int(raw.get("cache_read_input_tokens") or 0),
        output_tokens=int(raw.get("output_tokens") or 0),
    )


def _split_tool_name(name: str) -> tuple[str, str]:
    """``mcp__tickets__set_priority`` -> ``('tickets', 'set_priority')``."""
    if name.startswith(MCP_TOOL_PREFIX):
        server, _, tool = name[len(MCP_TOOL_PREFIX) :].partition("__")
        return server, tool or server
    return BUILTIN_SERVER, name


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return "" if content is None else str(content)
