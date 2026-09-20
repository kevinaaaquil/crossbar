"""Immutable domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

ENVIRONMENT_KINDS = ("local", "docker")
RESET_POLICIES = ("recreate", "hooks")


class Role(Enum):
    """A user-assigned label. Roles do not change how a Task executes.

    They carry exactly two consequences: Candidate Attempts run before Baseline
    Attempts for a given Test, and the Baseline is the Judge fallback.
    """

    CANDIDATE = "candidate"
    BASELINE = "baseline"
    JUDGE = "judge"

    @property
    def order(self) -> int | None:
        """Execution order within a Test. None for roles that do not execute."""
        return {Role.CANDIDATE: 0, Role.BASELINE: 1}.get(self)


@dataclass(frozen=True)
class Limits:
    """Ceilings for a single Attempt."""

    timeout_s: int = 120
    max_steps: int = 20
    max_tokens: int = 100_000


@dataclass(frozen=True)
class ConnectorConfig:
    """One connector the agent is granted, plus its options.

    Options are passed through untouched: the domain layer does not know what
    any particular connector needs.
    """

    name: str
    options: Mapping[str, Any] = field(default_factory=dict)
    read_only_tools: tuple[str, ...] = ()
    """Tools the author declares safe to call during evidence capture, for
    servers that do not advertise it themselves."""


@dataclass(frozen=True)
class StateSpec:
    """How an Environment hands its state out and takes it back.

    Declared by environments whose server keeps state of its own -- a database,
    a file store, a queue. Two executables and one rule about where state
    lives: everything durable is inside ``dir``, and ``snapshot_dir`` holds
    copies of it. The hooks are the only store-specific part, which is what
    lets the same machinery serve SQLite, Postgres or a folder of documents.
    """

    dir: str
    """The state directory. Nothing durable lives outside it."""

    snapshot_dir: str
    """Where snapshots are written and restored from. Never inside ``dir``: a
    snapshot written there would be a second state file, and the server's next
    start would delete one of the two."""

    snapshot: str
    """Executable, run with no arguments. Writes the current state into
    ``snapshot_dir`` and prints the absolute path it wrote as its last line."""

    restore: str
    """Executable, run with no arguments. Installs the first file in
    ``snapshot_dir`` as the live state. Validates before installing: a
    half-applied restore is worse than a refused one."""

    restart_after_restore: bool = False
    """True when the server caches state across calls, so replacing the file
    under it is not enough. crossbar restarts the container around a restore
    instead -- still cheaper than recreating, since the image is already
    there."""


@dataclass(frozen=True)
class EnvironmentSpec:
    """Where a Task runs and what the agent may touch."""

    kind: str
    connectors: tuple[ConnectorConfig, ...]
    image: str | None = None
    reset: str = "recreate"
    state: StateSpec | None = None
    source: str = "<memory>"

    def connector(self, name: str) -> ConnectorConfig | None:
        return next((c for c in self.connectors if c.name == name), None)


@dataclass(frozen=True)
class Task:
    """One isolated unit of work, judged against a known correct result."""

    id: str
    prompt: str
    golden: str
    """What a correct result means, in prose. Never shaped to fit a schema."""
    name: str = ""
    limits: Limits = field(default_factory=Limits)
    environment: EnvironmentSpec | None = None
    environment_ref: str = ""
    source: str = "<memory>"

    @property
    def title(self) -> str:
        return self.name or self.id


@dataclass(frozen=True)
class Test:
    """A list of Tasks run by models against an Environment."""

    __test__ = False  # this is a domain object, not something for pytest to collect

    name: str
    tasks: tuple[Task, ...]
    environment: EnvironmentSpec
    description: str = ""
    repeats: int = 1
    path: str = ""

    def __iter__(self):
        return iter(self.tasks)

    def __len__(self) -> int:
        return len(self.tasks)

    def task(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)
