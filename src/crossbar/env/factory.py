"""Pick the environment implementation named by a task."""

from __future__ import annotations

from crossbar.env.base import Environment, EnvironmentError_
from crossbar.env.docker import DockerEnvironment
from crossbar.env.local import LocalEnvironment
from crossbar.tasks import EnvironmentSpec


def build_environment(spec: EnvironmentSpec, **kwargs) -> Environment:
    if spec.kind == "local":
        return LocalEnvironment(spec, **kwargs)
    if spec.kind == "docker":
        return DockerEnvironment(spec, **kwargs)
    raise EnvironmentError_(f"unknown environment kind {spec.kind!r}")
