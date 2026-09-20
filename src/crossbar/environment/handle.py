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
from typing import Mapping, Sequence

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

WORKSPACE_VAR = "CROSSBAR_WORKSPACE"
PYTHON_VAR = "CROSSBAR_PYTHON"


@dataclass(frozen=True)
class RemoteExec:
    """How to hand a command its environment when the prefix runs it elsewhere.

    ``docker exec`` launches the server inside the container, so variables set
    on the local process reach the ``docker exec`` client and stop there. They
    have to travel as flags on the prefix instead, and those flags belong
    before the container id -- hence ``insert_at``, which the Environment that
    built the prefix is the only thing in a position to know.
    """

    insert_at: int
    env_flag: str = "-e"
    cwd_flag: str = "-w"

    def flags(self, env: Mapping[str, str], cwd: str | None) -> list[str]:
        out: list[str] = []
        for key, value in env.items():
            out.extend([self.env_flag, f"{key}={value}"])
        if cwd:
            out.extend([self.cwd_flag, cwd])
        return out


@dataclass(frozen=True)
class Launch:
    """One command, ready to run: what to exec, and under what."""

    argv: list[str]
    env: dict[str, str]
    cwd: str | None


@dataclass(frozen=True)
class EnvironmentHandle:
    """A live Environment, as far as a Connector is concerned."""

    workspace: str
    """Scratch directory for this Attempt. Servers that must persist state so it
    can be read back after the agent is gone write it here."""

    command_prefix: tuple[str, ...] = ()
    """Prepended to every command a Connector launches. Empty for a local
    environment; ``docker exec -i <container>`` for a containerised one."""

    remote: "RemoteExec | None" = None
    """Set when ``command_prefix`` runs the command somewhere else, so a Task's
    ``env`` and ``cwd`` must be injected into the prefix. ``None`` means the
    command runs locally and they apply to the process."""

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

    def launch(
        self,
        command: str,
        args: "Sequence[str]" = (),
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
    ) -> Launch:
        """Everything needed to start one server under this Environment.

        Whether a Task's ``env`` and ``cwd`` end up in the argv or on the
        process is the Environment's business, not the Connector's: a Connector
        that decided for itself would silently drop both under docker.
        """
        expanded_env = {k: self.expand(str(v)) for k, v in (env or {}).items()}
        expanded_cwd = self.expand(cwd) if cwd else None
        argv = [
            *self.command_prefix,
            self.expand(command),
            *(self.expand(str(a)) for a in args),
        ]
        if self.remote is None:
            return Launch(
                argv=argv,
                env=self.server_env(expanded_env),
                cwd=expanded_cwd,
            )
        flags = self.remote.flags(expanded_env, expanded_cwd)
        at = self.remote.insert_at
        argv[at:at] = flags
        # The variables travel as flags; the local `docker exec` client gets
        # only what the Environment itself set, never the Task's.
        return Launch(argv=argv, env=dict(self.env_vars), cwd=None)
