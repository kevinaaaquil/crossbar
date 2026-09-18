"""Servers as plain subprocesses on this machine.

Fast, needs no daemon, and the right choice while authoring a Test. It offers
no isolation, so anything untrusted belongs in a container.
"""

from __future__ import annotations

import shutil
import tempfile

from crossbar.domain import EnvironmentSpec
from crossbar.environment.base import EnvironmentHandle


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
