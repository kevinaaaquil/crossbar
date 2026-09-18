"""Parsing and validating Test directories.

Validation is loud and early. A Test that silently accepts a typo'd field
produces an evaluation that measures nothing, and nobody notices for a week.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Container, Mapping

import yaml

from crossbar.domain.model import (
    ENVIRONMENT_KINDS,
    RESET_POLICIES,
    ConnectorConfig,
    EnvironmentSpec,
    Limits,
    Task,
    Test,
)

TEST_FILE = "test.yaml"
TASK_SUFFIX = ".task.yaml"

TASK_KEYS = frozenset({"id", "name", "prompt", "golden", "limits", "environment"})
TEST_KEYS = frozenset({"name", "description", "repeats", "environment"})
LIMIT_KEYS = frozenset({"timeout_s", "max_steps", "max_tokens"})


class DomainError(ValueError):
    """A malformed Test, Task or Environment. Always names its source."""


def _fail(source: str, message: str) -> None:
    raise DomainError(f"{source}: {message}")


# -- tasks -----------------------------------------------------------------


def parse_task(data: Any, source: str = "<memory>") -> Task:
    """Build a :class:`Task` from a mapping."""
    if not isinstance(data, Mapping):
        _fail(source, "a task must be a mapping")
    _reject_unknown(data, TASK_KEYS, source, "task")

    task_id = _required_string(data, "id", source)
    prompt = _required_string(data, "prompt", source)
    golden = _required_string(data, "golden", source)

    return Task(
        id=task_id,
        prompt=prompt,
        golden=golden,
        name=str(data.get("name", "")),
        limits=_parse_limits(data.get("limits"), source),
        environment_ref=str(data.get("environment", "")),
        source=source,
    )


def _parse_limits(raw: Any, source: str) -> Limits:
    if raw is None:
        return Limits()
    if not isinstance(raw, Mapping):
        _fail(source, "'limits' must be a mapping")
    _reject_unknown(raw, LIMIT_KEYS, source, "limits")
    defaults = Limits()
    values = {}
    for key in LIMIT_KEYS:
        values[key] = _positive_int(raw.get(key, getattr(defaults, key)), key, source)
    return Limits(**values)


# -- environments ----------------------------------------------------------


def parse_environment(
    data: Any,
    source: str = "<memory>",
    known_connectors: Container[str] | None = None,
) -> EnvironmentSpec:
    if not isinstance(data, Mapping):
        _fail(source, "an environment must be a mapping")

    kind = str(data.get("kind", "local"))
    if kind not in ENVIRONMENT_KINDS:
        _fail(source, f"unknown environment kind {kind!r}, expected one of {list(ENVIRONMENT_KINDS)}")

    reset = str(data.get("reset", "recreate"))
    if reset not in RESET_POLICIES:
        _fail(source, f"unknown reset policy {reset!r}, expected one of {list(RESET_POLICIES)}")

    image = data.get("image")
    if kind == "docker" and not image:
        _fail(source, "a docker environment needs an 'image'")

    raw_connectors = data.get("connectors")
    if not isinstance(raw_connectors, Mapping) or not raw_connectors:
        _fail(source, "an environment needs at least one connector")

    connectors = []
    for name, options in raw_connectors.items():
        if known_connectors is not None and name not in known_connectors:
            _fail(source, f"unknown connector {name!r}")
        options = options or {}
        if not isinstance(options, Mapping):
            _fail(source, f"options for connector {name!r} must be a mapping")
        read_only = options.get("read_only_tools") or ()
        if not isinstance(read_only, (list, tuple)):
            _fail(source, f"'read_only_tools' for {name!r} must be a list")
        connectors.append(
            ConnectorConfig(
                name=str(name),
                options={k: v for k, v in options.items() if k != "read_only_tools"},
                read_only_tools=tuple(str(t) for t in read_only),
            )
        )

    return EnvironmentSpec(
        kind=kind,
        connectors=tuple(connectors),
        image=str(image) if image else None,
        reset=reset,
        source=source,
    )


# -- tests -----------------------------------------------------------------


def load_test(
    path: str | os.PathLike[str],
    known_connectors: Container[str] | None = None,
) -> Test:
    """Load a Test directory: metadata, environment, and every Task in it."""
    directory = Path(path)
    if not directory.is_dir():
        raise DomainError(f"{directory}: not a directory")

    meta_path = directory / TEST_FILE
    if not meta_path.exists():
        raise DomainError(f"{directory}: no {TEST_FILE} found")
    meta = _read_yaml(meta_path) or {}
    if not isinstance(meta, Mapping):
        _fail(str(meta_path), f"{TEST_FILE} must be a mapping")
    _reject_unknown(meta, TEST_KEYS, str(meta_path), "test")

    repeats = _positive_int(meta.get("repeats", 1), "repeats", str(meta_path))
    default_env = _load_environment(
        directory, str(meta.get("environment") or "env.yaml"), str(meta_path), known_connectors
    )

    tasks: list[Task] = []
    seen: dict[str, str] = {}
    for task_path in sorted(directory.glob(f"*{TASK_SUFFIX}")):
        task = parse_task(_read_yaml(task_path), source=str(task_path))
        if task.id in seen:
            raise DomainError(
                f"{task_path}: duplicate task id {task.id!r} (already defined in {seen[task.id]})"
            )
        seen[task.id] = str(task_path)
        environment = (
            _load_environment(directory, task.environment_ref, str(task_path), known_connectors)
            if task.environment_ref
            else default_env
        )
        tasks.append(_with_environment(task, environment))

    if not tasks:
        raise DomainError(f"{directory}: no tasks found (expected *{TASK_SUFFIX} files)")

    return Test(
        name=str(meta.get("name", directory.name)),
        description=str(meta.get("description", "")),
        repeats=repeats,
        tasks=tuple(tasks),
        environment=default_env,
        path=str(directory),
    )


def _load_environment(
    directory: Path,
    ref: str,
    referenced_by: str,
    known_connectors: Container[str] | None,
) -> EnvironmentSpec:
    env_path = directory / ref
    if not env_path.exists():
        raise DomainError(f"{referenced_by}: environment file {ref!r} not found at {env_path}")
    return parse_environment(_read_yaml(env_path), str(env_path), known_connectors)


def _with_environment(task: Task, environment: EnvironmentSpec) -> Task:
    return Task(
        id=task.id,
        prompt=task.prompt,
        golden=task.golden,
        name=task.name,
        limits=task.limits,
        environment=environment,
        environment_ref=task.environment_ref,
        source=task.source,
    )


# -- helpers ---------------------------------------------------------------


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise DomainError(f"{path}: malformed YAML: {exc}") from exc


def _required_string(data: Mapping, key: str, source: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        _fail(source, f"missing required field {key!r}")
    return value


def _reject_unknown(data: Mapping, allowed: frozenset[str], source: str, what: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        _fail(source, f"unknown {what} field(s): {', '.join(unknown)}")


def _positive_int(value: Any, field_name: str, source: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = -1
    if number <= 0:
        _fail(source, f"{field_name!r} must be a positive integer")
    return number
