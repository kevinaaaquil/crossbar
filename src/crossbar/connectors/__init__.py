"""Connectors: toggleable capabilities the agent uses to touch the Environment.

The Harness asks every enabled Connector for its tools and routes calls back to
the owner. **The Harness never learns what kind of Connector it is talking to**,
which is what keeps adding browser, HTTP or shell support from being a rewrite.
"""

from crossbar.connectors.base import (
    Connector,
    ConnectorError,
    ToolResult,
    ToolSpec,
)
from crossbar.connectors.mcp import McpConnector
from crossbar.connectors.registry import build_connector, known_connectors, register

__all__ = [
    "Connector",
    "ConnectorError",
    "McpConnector",
    "ToolResult",
    "ToolSpec",
    "build_connector",
    "known_connectors",
    "register",
]
