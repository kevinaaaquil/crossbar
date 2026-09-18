"""Pick the environment implementation a Test asked for."""

from __future__ import annotations

from crossbar.domain import EnvironmentSpec
from crossbar.environment.base import Environment, EnvironmentError_
from crossbar.environment.docker import DockerEnvironment
from crossbar.environment.local import LocalEnvironment


def build_environment(spec: EnvironmentSpec, **options) -> Environment:
    if spec.kind == "local":
        return LocalEnvironment(spec, **options)
    if spec.kind == "docker":
        return DockerEnvironment(spec, **options)
    raise EnvironmentError_(f"unknown environment kind {spec.kind!r}")
