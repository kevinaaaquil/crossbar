"""One throwaway container per Attempt.

Network off by default, removed on teardown, and recreated rather than cleaned
between Attempts.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from typing import Sequence

from crossbar.domain import EnvironmentSpec
from crossbar.environment.base import EnvironmentError_, EnvironmentHandle
from crossbar.environment.handle import RemoteExec
from crossbar.environment.state import StateHooks

CONTAINER_WORKSPACE = "/tmp/crossbar-workspace"


class DockerEnvironment:
    def __init__(
        self,
        spec: EnvironmentSpec,
        docker_bin: str = "docker",
        network: str = "none",
        startup_timeout_s: int = 300,
    ) -> None:
        self.spec = spec
        self.docker_bin = docker_bin
        self.network = network
        self.startup_timeout_s = startup_timeout_s
        self.container_id: str | None = None
        self._handle: EnvironmentHandle | None = None

    def start(self) -> EnvironmentHandle:
        if self._handle is not None:
            return self._handle
        if shutil.which(self.docker_bin) is None and "/" not in self.docker_bin:
            raise EnvironmentError_(
                f"docker binary {self.docker_bin!r} not found on PATH; install Docker "
                "or use an environment of kind 'local'"
            )

        name = f"crossbar-{uuid.uuid4().hex[:12]}"
        argv = [
            self.docker_bin, "run", "-d", "--rm",
            "--name", name,
            "--network", self.network,
            self.spec.image or "",
            "sleep", "infinity",
        ]
        proc = self._run(argv, "docker run")
        self.container_id = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        self.container_id = self.container_id[0]
        if not self.container_id:
            raise EnvironmentError_("docker run returned no container id")

        # The workspace lives inside the container, since that is where the
        # servers run and where evidence probes will read from.
        self._run(
            [self.docker_bin, "exec", self.container_id, "mkdir", "-p", CONTAINER_WORKSPACE],
            "creating the container workspace",
        )
        prefix = (self.docker_bin, "exec", "-i", self.container_id)
        self._handle = EnvironmentHandle(
            workspace=CONTAINER_WORKSPACE,
            command_prefix=prefix,
            # Flags go before the container id, which is the prefix's last word.
            remote=RemoteExec(insert_at=len(prefix) - 1),
        )
        return self._handle

    # -- state hooks -------------------------------------------------------

    def state_hooks(self) -> StateHooks | None:
        """This Environment's snapshot and restore hooks, if it declared any.

        The hooks are scripts inside the image, so they run through `docker
        exec` and their files move through `docker cp`. Nothing here knows what
        kind of store is behind them.
        """
        if self.spec.state is None:
            return None
        return StateHooks(self.spec.state, run=self._run_in_container, files=self)

    def _run_in_container(self, argv: Sequence[str], what: str) -> str:
        if not self.container_id:
            raise EnvironmentError_(f"cannot run {what}: the container is not running")
        proc = self._run(
            [self.docker_bin, "exec", self.container_id, *argv], what
        )
        return proc.stdout or ""

    def copy_out(self, remote: str, local) -> None:
        self._run(
            [self.docker_bin, "cp", f"{self.container_id}:{remote}", str(local)],
            f"copying {remote} out of the container",
        )

    def copy_in(self, local, remote_dir: str) -> None:
        self._run(
            [self.docker_bin, "cp", str(local), f"{self.container_id}:{remote_dir}/"],
            f"copying {local} into the container",
        )

    def clear(self, remote_dir: str) -> None:
        """Empty a directory inside the container, leaving the directory."""
        self._run(
            [
                self.docker_bin, "exec", self.container_id,
                "find", remote_dir, "-maxdepth", "1", "-type", "f", "-delete",
            ],
            f"clearing {remote_dir}",
        )

    def reset(self) -> EnvironmentHandle:
        self.stop()
        return self.start()

    def stop(self) -> None:
        container, self.container_id = self.container_id, None
        self._handle = None
        if container:
            try:
                subprocess.run(
                    [self.docker_bin, "rm", "-f", container],
                    capture_output=True, text=True, timeout=60,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass

    def __enter__(self) -> EnvironmentHandle:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def _run(self, argv: list[str], what: str) -> subprocess.CompletedProcess:
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=self.startup_timeout_s
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EnvironmentError_(f"{what} failed: {exc}") from exc
        if proc.returncode != 0:
            raise EnvironmentError_(
                f"{what} failed: {(proc.stderr or proc.stdout or '').strip()}"
            )
        return proc
