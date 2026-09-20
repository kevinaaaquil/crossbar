"""Servers as plain subprocesses on this machine.

Fast, needs no daemon, and the right choice while authoring a Test. It offers
no isolation, so anything untrusted belongs in a container.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

from crossbar.domain import EnvironmentSpec
from crossbar.environment.base import EnvironmentError_, EnvironmentHandle
from crossbar.environment.state import StateHooks


class LocalEnvironment:
    def __init__(self, spec: EnvironmentSpec) -> None:
        self.spec = spec
        self._handle: EnvironmentHandle | None = None

    def start(self) -> EnvironmentHandle:
        if self._handle is None:
            self._handle = EnvironmentHandle(
                workspace=tempfile.mkdtemp(prefix="crossbar-ws-")
            )
        return self._handle

    # -- state hooks -------------------------------------------------------
    #
    # The contract is not about containers. A local environment whose server
    # keeps a database is an ordinary case, and supporting it here is also what
    # lets the whole lifecycle be tested with real scripts and real files
    # rather than a stubbed daemon.

    def state_hooks(self) -> StateHooks | None:
        if self.spec.state is None:
            return None
        return StateHooks(self.spec.state, run=self._run_state_hook, files=self)

    def _run_state_hook(self, argv: Sequence[str], what: str) -> str:
        try:
            proc = subprocess.run(
                list(argv), capture_output=True, text=True, timeout=300
            )
        except OSError as exc:
            raise EnvironmentError_(f"{what} could not be run: {exc}") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise EnvironmentError_(f"{what} failed: {detail}")
        return proc.stdout or ""

    def copy_out(self, remote: str, local) -> None:
        shutil.copy2(remote, local)

    def copy_in(self, local, remote_dir: str) -> None:
        shutil.copy2(local, Path(remote_dir) / Path(local).name)

    def clear(self, remote_dir: str) -> None:
        for entry in Path(remote_dir).iterdir():
            if entry.is_file():
                entry.unlink()

    def reset(self) -> EnvironmentHandle:
        """Destroy everything and build it again.

        The only reset policy that cannot leak state into the next Attempt, and
        leaked state produces quietly wrong numbers with no error to notice.
        """
        self.stop()
        return self.start()

    def stop(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            shutil.rmtree(handle.workspace, ignore_errors=True)

    def __enter__(self) -> EnvironmentHandle:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
