"""A stand-in for the `claude` CLI.

Lets the CLI agent be exercised end to end — argv, MCP config, stream parsing,
exit codes — with no API key and no network.

It can also *really* drive the MCP servers named in the config it is handed,
which is what makes the reconnect-before-capture path testable: the state the
fake CLI leaves behind is state crossbar's own connectors never saw.

Environment:
  FAKE_CLAUDE_ARGV_LOG   write the received argv here, one per line
  FAKE_CLAUDE_MCP_LOG    copy the --mcp-config contents here
  FAKE_CLAUDE_APPLY      JSON list of {server, tool, args} to actually execute
  FAKE_CLAUDE_STREAM     path to a file of stream-json lines to emit
  FAKE_CLAUDE_EXIT       exit code (default 0)
  FAKE_CLAUDE_STDERR     text to write to stderr
  FAKE_CLAUDE_HANG       sleep this many seconds instead of answering
"""

from __future__ import annotations

import json
import os
import sys
import time

DEFAULT_STREAM = [
    {"type": "system", "subtype": "init", "session_id": "fake", "mcp_servers": []},
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "hidden"},
                {"type": "text", "text": "Working on it."},
            ],
            "usage": {"input_tokens": 10, "cache_creation_input_tokens": 100,
                      "cache_read_input_tokens": 500, "output_tokens": 20},
        },
    },
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Done.",
        "num_turns": 2,
        "duration_ms": 1234,
        "total_cost_usd": 0.0042,
        "usage": {"input_tokens": 10, "cache_creation_input_tokens": 100,
                  "cache_read_input_tokens": 500, "output_tokens": 20},
    },
]


def _apply_calls(config_path: str, calls: list) -> None:
    """Drive the task's MCP servers, the way the real CLI would."""
    from crossbar.mcpclient import McpStdioClient

    with open(config_path) as handle:
        servers = json.load(handle)["mcpServers"]
    for call in calls:
        spec = servers[call["server"]]
        client = McpStdioClient(
            name=call["server"],
            command=spec["command"],
            args=spec.get("args", []),
            env=spec.get("env", {}),
        )
        client.start()
        try:
            client.call_tool(call["tool"], call.get("args", {}))
        finally:
            client.stop()


def main() -> int:
    argv = sys.argv[1:]

    log = os.environ.get("FAKE_CLAUDE_ARGV_LOG")
    if log:
        with open(log, "w") as handle:
            handle.write("\n".join(argv))

    config_path = argv[argv.index("--mcp-config") + 1] if "--mcp-config" in argv else None

    mcp_log = os.environ.get("FAKE_CLAUDE_MCP_LOG")
    if mcp_log and config_path:
        with open(config_path) as src, open(mcp_log, "w") as dst:
            dst.write(src.read())

    apply = os.environ.get("FAKE_CLAUDE_APPLY")
    if apply and config_path:
        _apply_calls(config_path, json.loads(apply))

    hang = os.environ.get("FAKE_CLAUDE_HANG")
    if hang:
        time.sleep(float(hang))

    stderr = os.environ.get("FAKE_CLAUDE_STDERR")
    if stderr:
        sys.stderr.write(stderr + "\n")

    stream = os.environ.get("FAKE_CLAUDE_STREAM")
    if stream and os.path.exists(stream):
        with open(stream) as handle:
            for line in handle:
                if line.strip():
                    sys.stdout.write(line if line.endswith("\n") else line + "\n")
    else:
        for event in DEFAULT_STREAM:
            sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()
    return int(os.environ.get("FAKE_CLAUDE_EXIT", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
