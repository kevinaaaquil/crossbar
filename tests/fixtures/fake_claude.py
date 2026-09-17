"""A stand-in for the `claude` CLI that replays a canned stream-json transcript.

Lets the Claude Code harness be tested end to end - argv, MCP config file,
stream parsing, exit codes - with no API key and no network.

Controlled by env vars:
  FAKE_CLAUDE_SCRIPT   path to a file of stream-json lines to emit
  FAKE_CLAUDE_ARGV_LOG path to write the received argv (one arg per line)
  FAKE_CLAUDE_EXIT     exit code to return (default 0)
  FAKE_CLAUDE_STDERR   text to write to stderr
  FAKE_CLAUDE_HANG     if set, sleep this many seconds instead of answering
  FAKE_CLAUDE_MCP_LOG  path to copy the --mcp-config file contents to
"""

from __future__ import annotations

import json
import os
import sys
import time


def main() -> int:
    argv_log = os.environ.get("FAKE_CLAUDE_ARGV_LOG")
    if argv_log:
        with open(argv_log, "w") as handle:
            handle.write("\n".join(sys.argv[1:]))

    mcp_log = os.environ.get("FAKE_CLAUDE_MCP_LOG")
    if mcp_log and "--mcp-config" in sys.argv:
        config_path = sys.argv[sys.argv.index("--mcp-config") + 1]
        with open(config_path) as src, open(mcp_log, "w") as dst:
            dst.write(src.read())

    hang = os.environ.get("FAKE_CLAUDE_HANG")
    if hang:
        time.sleep(float(hang))

    stderr = os.environ.get("FAKE_CLAUDE_STDERR")
    if stderr:
        sys.stderr.write(stderr + "\n")

    script = os.environ.get("FAKE_CLAUDE_SCRIPT")
    if script and os.path.exists(script):
        with open(script) as handle:
            for line in handle:
                if line.strip():
                    sys.stdout.write(line if line.endswith("\n") else line + "\n")
                    sys.stdout.flush()
    else:
        sys.stdout.write(json.dumps({"type": "result", "subtype": "success", "result": "ok"}) + "\n")

    return int(os.environ.get("FAKE_CLAUDE_EXIT", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
