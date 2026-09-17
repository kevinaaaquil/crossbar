"""Servers inside a throwaway container, reached over ``docker exec`` stdio.

One container per task run, network off by default, removed on teardown, so a
task cannot leak state into the next repeat.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid

from crossbar.env.base import Environment, EnvironmentError_
from crossbar.mcpclient import McpStdioClient
from crossbar.tasks import EnvironmentSpec, ServerSpec


class DockerEnvironment(Environment):
    def __init__(
        self,
        spec: EnvironmentSpec,
        docker_bin: str = "docker",
        network: str = "none",
        keep_container: bool = False,
        workspace: str | None = None,
    ) -> None:
        super().__init__(spec, workspace=workspace)
        self.docker_bin = docker_bin
        self.network = network
        self.keep_container = keep_container
        self.container_id: str | None = None
        self.container_name = f"crossbar-{uuid.uuid4().hex[:12]}"

    def _prepare(self) -> None:
        if shutil.which(self.docker_bin) is None and "/" not in self.docker_bin:
            raise EnvironmentError_(
                f"docker binary {self.docker_bin!r} not found on PATH; "
                "install Docker or use an environment of kind 'local'"
            )
        argv = [
            self.docker_bin, "run", "-d", "--rm",
            "--name", self.container_name,
            "--network", self.network,
            self.spec.image or "",
            "sleep", "infinity",
        ]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EnvironmentError_(f"docker run failed: {exc}") from exc
        if proc.returncode != 0:
            raise EnvironmentError_(f"docker run failed: {proc.stderr.strip() or proc.stdout.strip()}")
        self.container_id = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else None
        if not self.container_id:
            raise EnvironmentError_("docker run returned no container id")

    def _make_client(self, server: ServerSpec) -> McpStdioClient:
        env_flags: list[str] = []
        for key, value in self.server_env(server).items():
            env_flags += ["-e", f"{key}={value}"]
        command, server_args = self.expanded_command(server)
        args = ["exec", "-i", *env_flags, self.container_id or "", command, *server_args]
        return McpStdioClient(name=server.name, command=self.docker_bin, args=args)

    def _cleanup(self) -> None:
        if self.container_id is None or self.keep_container:
            return
        container, self.container_id = self.container_id, None
        try:
            subprocess.run(
                [self.docker_bin, "rm", "-f", container],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
