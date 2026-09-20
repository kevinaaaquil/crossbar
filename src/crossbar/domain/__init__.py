"""Domain objects: what a Test, a Task and an Environment are.

Parsing and validation only. Nothing here executes anything.
"""

from crossbar.domain.model import (
    ConnectorConfig,
    EnvironmentSpec,
    StateSpec,
    Limits,
    Role,
    Task,
    Test,
)
from crossbar.domain.loader import DomainError, load_test, parse_task

__all__ = [
    "ConnectorConfig",
    "DomainError",
    "EnvironmentSpec",
    "StateSpec",
    "Limits",
    "Role",
    "Task",
    "Test",
    "load_test",
    "parse_task",
]
