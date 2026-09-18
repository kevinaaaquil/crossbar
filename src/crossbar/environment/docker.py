"""One throwaway container per Attempt.

Network off by default, removed on teardown, and recreated rather than cleaned
between Attempts.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid

from crossbar.domain import EnvironmentSpec
from crossbar.environment.base import EnvironmentError_, EnvironmentHandle

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
        self._handle = EnvironmentHandle(
            workspace=CONTAINER_WORKSPACE,
            command_prefix=(self.docker_bin, "exec", "-i", self.container_id),
        )
        return self._handle

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
