"""An MCP server whose every tool mutates, so nothing can be used as a probe.

Used to check that pre-flight warns when a Test's Environment can show nothing
back — every Attempt against it would come back Unchecked.
"""

from __future__ import annotations

import json
import sys

TOOLS = [
    {"name": "do_thing", "description": "Changes something.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        method, req_id = request.get("method"), request.get("id")
        if method in ("notifications/initialized", "initialized"):
            continue
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "probeless", "version": "0.1.0"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "did the thing"}]}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
