"""Servers as plain subprocesses on this machine.

Fast, no daemon, and the right default while authoring a task pack. It offers
no isolation, so anything untrusted belongs in the docker environment.
"""

from __future__ import annotations

from crossbar.env.base import Environment
from crossbar.mcpclient import McpStdioClient
from crossbar.tasks import ServerSpec


class LocalEnvironment(Environment):
    def _make_client(self, server: ServerSpec) -> McpStdioClient:
        command, args = self.expanded_command(server)
        return McpStdioClient(
            name=server.name,
            command=command,
            args=args,
            env=self.server_env(server),
            cwd=self.expand(server.cwd) if server.cwd else None,
        )
