"""Connector registry.

Adding a connector is a registration here and nothing else. If a second place
has to learn its name, the seam has leaked.
"""

from __future__ import annotations

from typing import Callable

from crossbar.connectors.base import Connector, ConnectorError
from crossbar.domain import ConnectorConfig

CONNECTORS: dict[str, Callable[[ConnectorConfig], Connector]] = {}


def register(name: str, factory: Callable[[ConnectorConfig], Connector]) -> None:
    CONNECTORS[name] = factory


def known_connectors() -> frozenset[str]:
    """Names the domain loader may validate a Test against."""
    return frozenset(CONNECTORS)


def build_connector(config: ConnectorConfig) -> Connector:
    factory = CONNECTORS.get(config.name)
    if factory is None:
        raise ConnectorError(
            f"unknown connector {config.name!r}; known: {sorted(CONNECTORS) or 'none'}"
        )
    return factory(config)
