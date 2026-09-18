"""The Environment contract."""

from __future__ import annotations

from typing import Protocol

from crossbar.environment.handle import EnvironmentHandle


class EnvironmentError_(RuntimeError):
    """The environment could not be created, reset, or torn down.

    Trailing underscore so it does not shadow the builtin.
    """


class Environment(Protocol):
    """The isolated system a Task runs against."""

    def start(self) -> EnvironmentHandle: ...
    def reset(self) -> EnvironmentHandle: ...
    def stop(self) -> None: ...
