"""A real, minimal MCP server over stdio: an in-memory notes store.

Used by the test suite so the MCP client, environment layer, harnesses and
scorer are all exercised end to end without a network or a model.

Run as: ``python -m tests.fixtures.notes_server``
"""

from __future__ import annotations

import json
import os
import sys

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "create_note",
        "description": "Create a note with a title and body.",
        "inputSchema": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
            "required": ["title"],
        },
    },
    {
        "name": "list_notes",
        "description": "List all notes, newest last.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tag_note",
        "description": "Add a tag to an existing note.",
        "inputSchema": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "tag": {"type": "string"}},
            "required": ["title", "tag"],
        },
    },
    {
        "name": "delete_all",
        "description": "Delete every note. Destructive.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "explode",
        "description": "Always fails. Used to test error handling.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

NOTES: list[dict] = []


def _db_path() -> str | None:
    """Notes persist in the run workspace when crossbar provides one."""
    workspace = os.environ.get("CROSSBAR_WORKSPACE")
    return os.path.join(workspace, "notes.json") if workspace else None


def _load() -> None:
    path = _db_path()
    if not path or not os.path.exists(path):
        return
    NOTES.clear()
    with open(path) as handle:
        NOTES.extend(json.load(handle))


def _save() -> None:
    path = _db_path()
    if not path:
        return
    with open(path, "w") as handle:
        json.dump(NOTES, handle)


def _handle_call(name: str, args: dict) -> dict:
    if name == "create_note":
        title = args.get("title")
        if not title:
            return {"content": [{"type": "text", "text": "title is required"}], "isError": True}
        NOTES.append({"title": title, "body": args.get("body", ""), "tags": []})
        return {"content": [{"type": "text", "text": f"created note {title!r}"}]}
    if name == "list_notes":
        return {
            "content": [{"type": "text", "text": json.dumps({"notes": NOTES})}],
            "structuredContent": {"notes": NOTES},
        }
    if name == "tag_note":
        for note in NOTES:
            if note["title"] == args.get("title"):
                note["tags"].append(args.get("tag"))
                return {"content": [{"type": "text", "text": "tagged"}]}
        return {"content": [{"type": "text", "text": "no such note"}], "isError": True}
    if name == "delete_all":
        NOTES.clear()
        return {"content": [{"type": "text", "text": "all notes deleted"}]}
    if name == "explode":
        return {"content": [{"type": "text", "text": "tool exploded"}], "isError": True}
    raise KeyError(name)


def _dispatch(request: dict) -> dict | None:
    method = request.get("method")
    req_id = request.get("id")
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": os.environ.get("NOTES_SERVER_NAME", "notes"), "version": "0.1.0"},
        }
    elif method in ("notifications/initialized", "initialized"):
        return None
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params") or {}
        try:
            result = _handle_call(params.get("name", ""), params.get("arguments") or {})
        except KeyError as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": f"unknown tool {exc.args[0]!r}"},
            }
    elif method == "ping":
        result = {}
    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def main() -> None:
    _load()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = _dispatch(request)
        _save()
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
