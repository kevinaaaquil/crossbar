"""One rollout, recorded in enough detail to score it and to argue about it later."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from crossbar.providers import ToolCall, Usage

MAX_RESULT_CHARS = 8_000
"""Tool output beyond this is truncated on disk; a full trace archive of raw MCP
payloads gets large fast and the tail is rarely what explains a failure."""


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    MAX_STEPS = "max_steps"
    TIMEOUT = "timeout"
    BUDGET_EXCEEDED = "budget_exceeded"
    ERROR = "error"

    @property
    def is_terminal(self) -> bool:
        return self is not RunStatus.RUNNING


@dataclass
class ToolEvent:
    """One tool invocation and what came back."""

    step: int
    api_name: str
    server: str
    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    result_text: str = ""
    is_error: bool = False
    duration_ms: int = 0
    blocked: bool = False
    blocked_reason: str = ""

    @property
    def qualified_name(self) -> str:
        return f"{self.server}.{self.tool}"

    def to_dict(self, truncate: bool = True) -> dict[str, Any]:
        text = self.result_text
        if truncate and len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + f"... [truncated {len(text) - MAX_RESULT_CHARS} chars]"
        return {
            "step": self.step,
            "api_name": self.api_name,
            "server": self.server,
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "result_text": text,
            "is_error": self.is_error,
            "duration_ms": self.duration_ms,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolEvent":
        return cls(
            step=int(data.get("step", 0)),
            api_name=str(data.get("api_name", "")),
            server=str(data.get("server", "")),
            tool=str(data.get("tool", "")),
            arguments=dict(data.get("arguments") or {}),
            result_text=str(data.get("result_text", "")),
            is_error=bool(data.get("is_error", False)),
            duration_ms=int(data.get("duration_ms", 0)),
            blocked=bool(data.get("blocked", False)),
            blocked_reason=str(data.get("blocked_reason", "")),
        )


@dataclass
class StepEvent:
    """One model turn."""

    index: int
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "text": self.text,
            "tool_calls": [
                {"id": c.id, "name": c.name, "arguments": dict(c.arguments)} for c in self.tool_calls
            ],
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
            },
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StepEvent":
        usage = data.get("usage") or {}
        return cls(
            index=int(data.get("index", 0)),
            text=str(data.get("text", "")),
            tool_calls=tuple(
                ToolCall(id=str(c.get("id", "")), name=str(c.get("name", "")),
                         arguments=dict(c.get("arguments") or {}))
                for c in data.get("tool_calls") or []
            ),
            usage=Usage(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))),
            duration_ms=int(data.get("duration_ms", 0)),
        )


@dataclass
class Trajectory:
    """Everything that happened during one (task, agent, repeat) rollout."""

    task_id: str
    agent_id: str
    repeat: int
    steps: list[StepEvent] = field(default_factory=list)
    tool_events: list[ToolEvent] = field(default_factory=list)
    security_violations: list[str] = field(default_factory=list)
    status: RunStatus = RunStatus.RUNNING
    final_text: str = ""
    error: str = ""
    started_at: float = field(default_factory=time.time)
    wall_time_s: float = 0.0
    reported_cost_usd: float = 0.0
    """Cost as reported by the harness itself, when it reports one (Claude Code does)."""

    # -- recording ---------------------------------------------------------

    def record_step(self, step: StepEvent) -> None:
        self.steps.append(step)

    def record_tool(self, event: ToolEvent) -> None:
        self.tool_events.append(event)

    def record_violation(self, reason: str) -> None:
        self.security_violations.append(reason)

    def finish(self, status: RunStatus, final_text: str = "", error: str = "") -> None:
        self.status = status
        self.final_text = final_text
        self.error = error
        self.wall_time_s = max(0.0, time.time() - self.started_at)

    # -- derived facts -----------------------------------------------------

    @property
    def usage(self) -> Usage:
        total = Usage()
        for step in self.steps:
            total = total + step.usage
        return total

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_events)

    @property
    def error_count(self) -> int:
        return sum(1 for e in self.tool_events if e.is_error)

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def tool_call_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self.tool_events:
            counts[event.qualified_name] = counts.get(event.qualified_name, 0) + 1
        return counts

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "repeat": self.repeat,
            "status": self.status.value,
            "final_text": self.final_text,
            "error": self.error,
            "started_at": self.started_at,
            "wall_time_s": self.wall_time_s,
            "reported_cost_usd": self.reported_cost_usd,
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
            },
            "security_violations": list(self.security_violations),
            "steps": [s.to_dict() for s in self.steps],
            "tool_events": [e.to_dict() for e in self.tool_events],
        }

    def write(self, path: str | os.PathLike[str]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2))
        return target

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Trajectory":
        traj = cls(
            task_id=str(data.get("task_id", "")),
            agent_id=str(data.get("agent_id", "")),
            repeat=int(data.get("repeat", 0)),
            status=RunStatus(str(data.get("status", "running"))),
            final_text=str(data.get("final_text", "")),
            error=str(data.get("error", "")),
            started_at=float(data.get("started_at", 0.0)),
            wall_time_s=float(data.get("wall_time_s", 0.0)),
            reported_cost_usd=float(data.get("reported_cost_usd", 0.0)),
        )
        traj.steps = [StepEvent.from_dict(s) for s in data.get("steps") or []]
        traj.tool_events = [ToolEvent.from_dict(e) for e in data.get("tool_events") or []]
        traj.security_violations = list(data.get("security_violations") or [])
        return traj


def load_trajectory(path: str | os.PathLike[str]) -> Trajectory:
    return Trajectory.from_dict(json.loads(Path(path).read_text()))
