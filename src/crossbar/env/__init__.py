"""Environments: the isolated box a task's MCP servers run inside."""

from crossbar.env.base import Environment, EnvironmentError_, ToolHandle
from crossbar.env.local import LocalEnvironment
from crossbar.env.docker import DockerEnvironment
from crossbar.env.factory import build_environment

__all__ = [
    "DockerEnvironment",
    "Environment",
    "EnvironmentError_",
    "LocalEnvironment",
    "ToolHandle",
    "build_environment",
]
