"""Environments: the isolated system a Task runs against.

One container, one Task at a time. Recreated between Attempts, because leaked
state produces quietly wrong numbers with nothing to notice.
"""

from crossbar.environment.handle import EnvironmentHandle
from crossbar.environment.base import Environment, EnvironmentError_
from crossbar.environment.local import LocalEnvironment
from crossbar.environment.docker import DockerEnvironment
from crossbar.environment.factory import build_environment

__all__ = [
    "DockerEnvironment",
    "Environment",
    "EnvironmentError_",
    "EnvironmentHandle",
    "LocalEnvironment",
    "build_environment",
]
