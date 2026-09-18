"""What a running Environment offers to the Connectors attached to it.

Plain data. The Environment decides what goes in it; Connectors read it and do
not care whether the thing behind it is a container or a set of subprocesses.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

WORKSPACE_VAR = "CROSSBAR_WORKSPACE"
PYTHON_VAR = "CROSSBAR_PYTHON"


@dataclass(frozen=True)
class EnvironmentHandle:
    """A live Environment, as far as a Connector is concerned."""

    workspace: str
    """Scratch directory for this Attempt. Servers that must persist state so it
    can be read back after the agent is gone write it here."""

    command_prefix: tuple[str, ...] = ()
    """Prepended to every command a Connector launches. Empty for a local
    environment; ``docker exec -i <container>`` for a containerised one."""

    env_vars: Mapping[str, str] = field(default_factory=dict)
    endpoints: Mapping[str, str] = field(default_factory=dict)
    """Named addresses the Environment exposes, e.g. ``{"http": "localhost:8080"}``."""

    @property
    def workspace_path(self) -> Path:
        return Path(self.workspace)

    def variables(self) -> dict[str, str]:
        """What ``${...}`` may refer to in a Task's connector configuration.

        ``CROSSBAR_PYTHON`` is always the interpreter running crossbar, so a
        Test can launch a Python MCP server without guessing whether this
        machine calls it python, python3, or something inside a virtualenv.
        """
        variables = dict(os.environ)
        variables.update(self.env_vars)
        variables[PYTHON_VAR] = sys.executable
        if self.workspace:
            variables[WORKSPACE_VAR] = self.workspace
        return variables

    def expand(self, text: str) -> str:
        """Substitute ``${VAR}``. Unknown names are left alone, not blanked, so a
        typo is visible rather than silently becoming an empty string."""
        variables = self.variables()
        return _VAR.sub(lambda m: variables.get(m.group(1), m.group(0)), text)

    def server_env(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        """Environment variables to hand a launched process."""
        env = dict(self.env_vars)
        env.update({k: self.expand(str(v)) for k, v in (extra or {}).items()})
        if self.workspace:
            env.setdefault(WORKSPACE_VAR, self.workspace)
        return env
