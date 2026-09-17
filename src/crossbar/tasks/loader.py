"""Parse and validate task YAML.

Validation is deliberately loud and early: a task pack that silently accepts a
typo'd server name produces an eval suite that scores nothing and nobody
notices for a week.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

import yaml

from crossbar.tasks.model import (
    CHECK_TYPES,
    ENV_KINDS,
    MATCH_MODES,
    Check,
    DemoScript,
    DemoStep,
    EnvironmentSpec,
    SecuritySpec,
    ServerSpec,
    Task,
    TaskPack,
)

TASK_SUFFIX = ".task.yaml"


class TaskValidationError(ValueError):
    """Raised for any malformed task definition, always naming its source."""


def _fail(source: str, message: str) -> None:
    raise TaskValidationError(f"{source}: {message}")


def parse_task(data: Any, source: str = "<memory>") -> Task:
    """Build a :class:`Task` from a plain mapping, validating as we go."""
    if not isinstance(data, Mapping):
        _fail(source, "task must be a mapping")
    task_id = data.get("id")
    if not task_id or not isinstance(task_id, str):
        _fail(source, "missing required field 'id'")
    prompt = data.get("prompt")
    if not prompt or not isinstance(prompt, str):
        _fail(source, "missing required field 'prompt'")

    environment = _parse_environment(data.get("environment"), source)
    checks = _parse_checks(data.get("checks"), environment, source)
    security = _parse_security(data.get("security"), source)
    demo = _parse_demo(data.get("demo"), source)

    return Task(
        id=task_id,
        prompt=prompt,
        environment=environment,
        checks=checks,
        name=str(data.get("name", "")),
        category=str(data.get("category", "mcp-workflow")),
        timeout_s=_positive_int(data.get("timeout_s", 120), "timeout_s", source),
        max_steps=_positive_int(data.get("max_steps", 20), "max_steps", source),
        budget_tokens=_positive_int(data.get("budget_tokens", 100_000), "budget_tokens", source),
        security=security,
        demo=demo,
        source=source,
    )


def _parse_environment(raw: Any, source: str) -> EnvironmentSpec:
    if not isinstance(raw, Mapping):
        _fail(source, "missing required field 'environment'")
    kind = raw.get("kind", "local")
    if kind not in ENV_KINDS:
        _fail(source, f"environment kind {kind!r} must be one of {list(ENV_KINDS)}")
    raw_servers = raw.get("servers") or []
    if not isinstance(raw_servers, list) or not raw_servers:
        _fail(source, "environment needs at least one MCP server")

    servers: list[ServerSpec] = []
    seen: set[str] = set()
    for entry in raw_servers:
        if not isinstance(entry, Mapping):
            _fail(source, "each server must be a mapping")
        name = entry.get("name")
        command = entry.get("command")
        if not name:
            _fail(source, "server is missing 'name'")
        if not command:
            _fail(source, f"server {name!r} is missing 'command'")
        if name in seen:
            _fail(source, f"duplicate server name {name!r}")
        seen.add(name)
        env = entry.get("env") or {}
        if not isinstance(env, Mapping):
            _fail(source, f"server {name!r} env must be a mapping")
        servers.append(
            ServerSpec(
                name=str(name),
                command=str(command),
                args=tuple(str(a) for a in (entry.get("args") or ())),
                env={str(k): str(v) for k, v in env.items()},
                cwd=entry.get("cwd"),
            )
        )

    image = raw.get("image")
    if kind == "docker" and not image:
        _fail(source, "docker environment requires an 'image'")
    return EnvironmentSpec(kind=kind, servers=tuple(servers), image=image)


def _parse_checks(raw: Any, environment: EnvironmentSpec, source: str) -> tuple[Check, ...]:
    if not isinstance(raw, list) or not raw:
        _fail(source, "task needs at least one entry under 'checks'")
    checks: list[Check] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            _fail(source, "each check must be a mapping")
        ctype = entry.get("type")
        if ctype not in CHECK_TYPES:
            _fail(source, f"unknown check type {ctype!r}, expected one of {list(CHECK_TYPES)}")
        server = entry.get("server")
        if ctype in ("mcp_state", "tool_called"):
            if not server:
                _fail(source, f"check {ctype!r} requires a 'server'")
            if environment.server(str(server)) is None:
                _fail(source, f"check references unknown server {server!r}")
            if not entry.get("tool"):
                _fail(source, f"check {ctype!r} requires a 'tool'")
        match = entry.get("match", "subset")
        if match not in MATCH_MODES:
            _fail(source, f"unknown match mode {match!r}, expected one of {list(MATCH_MODES)}")
        if ctype == "final_text" and entry.get("value") in (None, ""):
            _fail(source, "check 'final_text' requires a 'value'")
        checks.append(
            Check(
                type=str(ctype),
                server=str(server) if server else None,
                tool=str(entry["tool"]) if entry.get("tool") else None,
                args=dict(entry.get("args") or {}),
                expect=entry.get("expect"),
                match=str(match),
                value=entry.get("value"),
                min_times=int(entry.get("min_times", 1)),
                max_times=entry.get("max_times"),
                weight=float(entry.get("weight", 1.0)),
                description=str(entry.get("description", "")),
            )
        )
    return tuple(checks)


def _parse_security(raw: Any, source: str) -> SecuritySpec:
    if raw is None:
        return SecuritySpec()
    if not isinstance(raw, Mapping):
        _fail(source, "'security' must be a mapping")
    forbidden = raw.get("forbidden_tools") or ()
    if not isinstance(forbidden, (list, tuple)):
        _fail(source, "'forbidden_tools' must be a list")
    max_calls = raw.get("max_tool_calls")
    return SecuritySpec(
        forbidden_tools=tuple(str(t) for t in forbidden),
        max_tool_calls=int(max_calls) if max_calls is not None else None,
    )


def _parse_demo(raw: Any, source: str) -> DemoScript | None:
    """Optional: how the built-in mock model should play this task."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        _fail(source, "'demo' must be a mapping")
    steps = []
    for entry in raw.get("script") or ():
        if not isinstance(entry, Mapping) or not entry.get("tool"):
            _fail(source, "each demo step needs a 'tool'")
        steps.append(
            DemoStep(
                tool=str(entry["tool"]),
                args=dict(entry.get("args") or {}),
                thought=str(entry.get("thought", "")),
            )
        )
    return DemoScript(steps=tuple(steps), final=str(raw.get("final", "Done.")))


def _positive_int(value: Any, field_name: str, source: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = -1
    if number <= 0:
        _fail(source, f"'{field_name}' must be a positive integer")
    return number


def load_task_file(path: str | os.PathLike[str]) -> Task:
    """Load a single ``*.task.yaml`` file."""
    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError as exc:
        raise TaskValidationError(f"{p}: malformed YAML: {exc}") from exc
    return parse_task(data, source=str(p))


def load_pack(path: str | os.PathLike[str]) -> TaskPack:
    """Load every task in a pack directory, plus optional ``pack.yaml`` metadata."""
    directory = Path(path)
    if not directory.is_dir():
        raise TaskValidationError(f"{directory}: not a directory")

    name = description = ""
    meta_path = directory / "pack.yaml"
    if meta_path.exists():
        try:
            meta = yaml.safe_load(meta_path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise TaskValidationError(f"{meta_path}: malformed YAML: {exc}") from exc
        name = str(meta.get("name", ""))
        description = str(meta.get("description", ""))

    tasks: list[Task] = []
    seen: dict[str, str] = {}
    for task_path in sorted(directory.glob(f"*{TASK_SUFFIX}")):
        task = load_task_file(task_path)
        if task.id in seen:
            raise TaskValidationError(
                f"{task_path}: duplicate task id {task.id!r} (already defined in {seen[task.id]})"
            )
        seen[task.id] = str(task_path)
        tasks.append(task)

    if not tasks:
        raise TaskValidationError(f"{directory}: no tasks found (expected *{TASK_SUFFIX} files)")
    return TaskPack(tasks=tuple(tasks), name=name, description=description, path=str(directory))
