"""Claude Code as a harness: drive the real CLI in print mode and parse its stream.

The point of the bench is to preserve each harness's native behaviour, so this
adapter does not reimplement Claude Code's loop. It hands the CLI the task, the
same MCP servers, and the same limits, then reads back what happened.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from typing import Any, Iterable

from crossbar.env import Environment
from crossbar.providers import Provider, ToolCall, Usage
from crossbar.tasks import Task
from crossbar.trace import RunStatus, StepEvent, ToolEvent, Trajectory

MCP_TOOL_PREFIX = "mcp__"
BUILTIN_SERVER = "claude-code"


class ClaudeCodeHarness:
    """Runs `claude -p` with only the task's MCP servers attached."""

    id = "claude-code"
    owns_servers = True
    """The CLI launches its own copies of the task's MCP servers, so post-run
    state has to be read back through a fresh connection to the workspace."""

    def __init__(
        self,
        claude_bin: str = "claude",
        model: str | None = None,
        permission_mode: str = "bypassPermissions",
        extra_args: Iterable[str] = (),
    ) -> None:
        self.claude_bin = claude_bin
        self.model = model
        self.permission_mode = permission_mode
        self.extra_args = list(extra_args)

    def run(
        self,
        task: Task,
        env: Environment,
        provider: Provider | None = None,
        repeat: int = 0,
        agent_id: str = "",
    ) -> Trajectory:
        traj = Trajectory(task_id=task.id, agent_id=agent_id or self.id, repeat=repeat)
        model = self.model or getattr(provider, "model", None)
        config_path = _write_mcp_config(task, env)
        try:
            argv = self._argv(task, env, model, config_path)
            started = time.monotonic()
            try:
                proc = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=task.timeout_s,
                    env={**os.environ, **_workspace_env(env)},
                )
            except FileNotFoundError:
                traj.finish(
                    RunStatus.ERROR,
                    error=f"claude CLI not found at {self.claude_bin!r}; "
                    "install Claude Code or point the roster at a different harness",
                )
                return traj
            except subprocess.TimeoutExpired:
                traj.finish(RunStatus.TIMEOUT, error=f"claude exceeded {task.timeout_s}s")
                return traj

            _parse_stream(proc.stdout, task, traj)
            if traj.status is RunStatus.RUNNING:
                detail = (proc.stderr or proc.stdout or "").strip()[-600:]
                if proc.returncode != 0:
                    traj.finish(RunStatus.ERROR, error=f"claude exited {proc.returncode}: {detail}")
                else:
                    traj.finish(RunStatus.ERROR, error=f"claude produced no result event: {detail}")
            traj.wall_time_s = max(traj.wall_time_s, time.monotonic() - started)
            return traj
        finally:
            try:
                os.unlink(config_path)
            except OSError:
                pass

    def _argv(self, task: Task, env: Environment, model: str | None, config_path: str) -> list[str]:
        argv = [
            self.claude_bin,
            "-p",
            task.prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--mcp-config",
            config_path,
            "--strict-mcp-config",
            "--permission-mode",
            self.permission_mode,
            "--max-turns",
            str(task.max_steps),
            "--allowedTools",
            ",".join(_allowed_tools(task, env)),
        ]
        if model:
            argv += ["--model", model]
        argv += self.extra_args
        return argv


def _allowed_tools(task: Task, env: Environment) -> list[str]:
    """Only the task's own MCP tools, minus anything the task forbids.

    Leaving the built-in tools out is what makes an MCP task an MCP task: the
    agent cannot sidestep the server by shelling out.
    """
    forbidden = set(task.security.forbidden_tools)
    allowed = []
    for handle in env.tools():
        if {f"{handle.server}.{handle.tool}", handle.api_name, handle.tool} & forbidden:
            continue
        allowed.append(f"{MCP_TOOL_PREFIX}{handle.server}__{handle.tool}")
    return allowed


def _write_mcp_config(task: Task, env: Environment) -> str:
    servers: dict[str, Any] = {}
    workspace_env = _workspace_env(env)
    for server in task.environment.servers:
        command, args = env.expanded_command(server)
        entry: dict[str, Any] = {"command": command, "args": args}
        merged = {**workspace_env, **dict(server.env)}
        if merged:
            entry["env"] = merged
        servers[server.name] = entry
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".mcp.json", prefix="crossbar-", delete=False
    )
    with handle:
        json.dump({"mcpServers": servers}, handle)
    return handle.name


def _workspace_env(env: Environment) -> dict[str, str]:
    workspace = getattr(env, "workspace", None)
    return {"CROSSBAR_WORKSPACE": str(workspace)} if workspace else {}


def _parse_stream(stdout: str, task: Task, traj: Trajectory) -> None:
    """Turn Claude Code's stream-json into steps, tool events and a verdict."""
    pending: dict[str, ToolEvent] = {}
    step_index = 0
    forbidden = set(task.security.forbidden_tools)

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
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            calls: list[ToolCall] = []
            for block in blocks:
                if block.get("type") != "tool_use":
                    continue
                server, tool = _split_tool_name(str(block.get("name") or ""))
                call_id = str(block.get("id") or f"tu_{len(pending)}")
                calls.append(ToolCall(id=call_id, name=str(block.get("name") or ""),
                                      arguments=dict(block.get("input") or {})))
                tool_event = ToolEvent(
                    step=step_index,
                    api_name=str(block.get("name") or ""),
                    server=server,
                    tool=tool,
                    arguments=dict(block.get("input") or {}),
                )
                if {f"{server}.{tool}", tool} & forbidden:
                    reason = f"called forbidden tool {server}.{tool}"
                    traj.record_violation(reason)
                    tool_event.blocked = True
                    tool_event.blocked_reason = reason
                pending[call_id] = tool_event
                traj.record_tool(tool_event)
            usage = message.get("usage") or {}
            traj.record_step(
                StepEvent(
                    index=step_index,
                    text=text,
                    tool_calls=tuple(calls),
                    usage=Usage(
                        int(usage.get("input_tokens") or 0),
                        int(usage.get("output_tokens") or 0),
                    ),
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
            usage = event.get("usage") or {}
            if usage and not traj.steps:
                traj.record_step(
                    StepEvent(
                        index=0,
                        usage=Usage(
                            int(usage.get("input_tokens") or 0),
                            int(usage.get("output_tokens") or 0),
                        ),
                    )
                )
            elif usage:
                # The result event reports the authoritative totals for the run.
                traj.steps[-1] = StepEvent(
                    index=traj.steps[-1].index,
                    text=traj.steps[-1].text,
                    tool_calls=traj.steps[-1].tool_calls,
                    usage=Usage(
                        int(usage.get("input_tokens") or 0) - _summed(traj, "input"),
                        int(usage.get("output_tokens") or 0) - _summed(traj, "output"),
                    )
                    + traj.steps[-1].usage,
                    duration_ms=traj.steps[-1].duration_ms,
                )
            subtype = str(event.get("subtype") or "")
            status = RunStatus.COMPLETED
            if subtype.startswith("error_max_turns"):
                status = RunStatus.MAX_STEPS
            elif event.get("is_error") or subtype.startswith("error"):
                status = RunStatus.ERROR
            traj.finish(
                status,
                final_text=str(event.get("result") or ""),
                error="" if status is RunStatus.COMPLETED else subtype or "claude reported an error",
            )
            if event.get("duration_ms"):
                traj.wall_time_s = float(event["duration_ms"]) / 1000.0


def _summed(traj: Trajectory, which: str) -> int:
    return sum(
        s.usage.input_tokens if which == "input" else s.usage.output_tokens for s in traj.steps
    )


def _split_tool_name(name: str) -> tuple[str, str]:
    """``mcp__notes__create_note`` -> ``('notes', 'create_note')``."""
    if name.startswith(MCP_TOOL_PREFIX):
        rest = name[len(MCP_TOOL_PREFIX) :]
        server, _, tool = rest.partition("__")
        return server, tool or server
    return BUILTIN_SERVER, name


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    return "" if content is None else str(content)
