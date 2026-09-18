"""Immutable domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

ENVIRONMENT_KINDS = ("local", "docker")
RESET_POLICIES = ("recreate",)


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
class EnvironmentSpec:
    """Where a Task runs and what the agent may touch."""

    kind: str
    connectors: tuple[ConnectorConfig, ...]
    image: str | None = None
    reset: str = "recreate"
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
