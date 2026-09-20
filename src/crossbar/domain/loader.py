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
    StateSpec,
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


def _parse_state(raw: Any, reset: str, source: str) -> "StateSpec | None":
    """The state block, and the two ways it can disagree with the reset policy.

    Either half without the other is a config that reads as though it does
    something and does not, so both are refused rather than warned about.
    """
    if raw is None:
        if reset == "hooks":
            _fail(source, "reset: hooks needs a 'state' block naming the hooks to run")
        return None
    if reset != "hooks":
        _fail(source, f"a 'state' block only does anything under reset: hooks, not {reset!r}")
    if not isinstance(raw, Mapping):
        _fail(source, "'state' must be a mapping")

    required = ("dir", "snapshot_dir", "snapshot", "restore")
    missing = [key for key in required if not str(raw.get(key) or "").strip()]
    if missing:
        _fail(source, f"'state' is missing {', '.join(missing)}")

    state_dir = str(raw["dir"]).rstrip("/") or "/"
    snapshot_dir = str(raw["snapshot_dir"]).rstrip("/") or "/"
    # Compared segment-wise: /app/db-snapshots starts with /app/db as a string
    # but is a sibling, and rejecting it would rule out a legal layout.
    if snapshot_dir == state_dir or snapshot_dir.startswith(state_dir + "/"):
        _fail(
            source,
            f"the snapshot directory {snapshot_dir!r} is inside the state directory "
            f"{state_dir!r}; a snapshot written there is a second state file",
        )

    seed = str(raw.get("seed") or "").strip()
    if seed:
        # Relative to the environment file, like everything else a Test names.
        base = Path(source).parent if source and source != "<memory>" else Path(".")
        resolved = (base / seed) if not Path(seed).is_absolute() else Path(seed)
        if not resolved.is_file():
            _fail(
                source,
                f"the state 'seed' {seed!r} is not a file at {resolved}; it is "
                "restored before the baseline is taken, so a run would fail "
                "with every Attempt starting from an empty store",
            )
        seed = str(resolved)

    return StateSpec(
        dir=state_dir,
        seed=seed,
        snapshot_dir=snapshot_dir,
        snapshot=str(raw["snapshot"]),
        restore=str(raw["restore"]),
        restart_after_restore=bool(raw.get("restart_after_restore", False)),
    )


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

    state = _parse_state(data.get("state"), reset, source)

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
        state=state,
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
