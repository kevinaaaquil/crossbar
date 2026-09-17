"""Minimal MCP client over stdio.

Deliberately dependency-free: the surface we need is initialize, tools/list and
tools/call, and owning it keeps the transport inspectable when a harness
misbehaves.
"""

from crossbar.mcpclient.stdio import (
    McpError,
    McpStdioClient,
    ToolResult,
    ToolSpec,
)

__all__ = ["McpError", "McpStdioClient", "ToolResult", "ToolSpec"]
