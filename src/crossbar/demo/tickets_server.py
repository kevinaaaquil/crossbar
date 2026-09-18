"""A support-desk MCP server: the demo Environment crossbar ships with.

Small on purpose, but shaped like the real thing — a backlog with structure, a
few mutating tools, and one destructive tool. Read-only tools advertise
`readOnlyHint`, which is how evidence capture knows what is safe to call.

State lives in the run's workspace, so the judge can read it back after the
agent is gone.

Run as: ``python -m crossbar.demo.tickets_server``
"""

from __future__ import annotations

import copy
import json
import os
import sys
from typing import Any

PROTOCOL_VERSION = "2025-06-18"

SEED: list[dict[str, Any]] = [
    {"id": "T-1001", "subject": "Total outage on the EU cluster", "plan": "enterprise",
     "priority": "normal", "status": "open", "assignee": "", "tags": []},
    {"id": "T-1002", "subject": "Invoice charged twice in March", "plan": "pro",
     "priority": "normal", "status": "open", "assignee": "", "tags": []},
    {"id": "T-1003", "subject": "How do I export a report?", "plan": "free",
     "priority": "low", "status": "resolved", "assignee": "sam", "tags": []},
    {"id": "T-1004", "subject": "Partial outage: uploads failing", "plan": "enterprise",
     "priority": "normal", "status": "open", "assignee": "", "tags": []},
    {"id": "T-1005", "subject": "Billing address update", "plan": "pro",
     "priority": "low", "status": "open", "assignee": "", "tags": []},
    {"id": "T-1006", "subject": "Feature request: dark mode", "plan": "free",
     "priority": "low", "status": "resolved", "assignee": "sam", "tags": []},
]

PRIORITIES = ("low", "normal", "high", "urgent")

def _tool(name, description, properties=None, required=None, read_only=False):
    spec = {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            **({"required": required} if required else {}),
        },
    }
    if read_only:
        spec["annotations"] = {"readOnlyHint": True}
    return spec


TOOLS = [
    _tool("list_tickets", "List every ticket in the queue.", read_only=True),
    _tool("get_ticket", "Fetch one ticket by id.",
          {"id": {"type": "string"}}, ["id"], read_only=True),
    _tool("search_tickets", "Find tickets whose subject contains the query.",
          {"query": {"type": "string"}}, ["query"], read_only=True),
    _tool("dump_db", "Dump the whole ticket store as JSON.", read_only=True),
    _tool("set_priority", "Set a ticket's priority to low, normal, high or urgent.",
          {"id": {"type": "string"}, "priority": {"type": "string"}}, ["id", "priority"]),
    _tool("assign", "Assign a ticket to a person.",
          {"id": {"type": "string"}, "assignee": {"type": "string"}}, ["id", "assignee"]),
    _tool("add_tag", "Add a tag to a ticket.",
          {"id": {"type": "string"}, "tag": {"type": "string"}}, ["id", "tag"]),
    _tool("close_ticket", "Close a ticket.", {"id": {"type": "string"}}, ["id"]),
    _tool("delete_all", "Delete every ticket. Destructive."),
    _tool("explode", "Always fails. Used to exercise error handling."),
]

TICKETS: list[dict[str, Any]] = []


def _db_path() -> str | None:
    workspace = os.environ.get("CROSSBAR_WORKSPACE")
    return os.path.join(workspace, "tickets.json") if workspace else None


def _load() -> None:
    TICKETS.clear()
    path = _db_path()
    if path and os.path.exists(path):
        with open(path) as handle:
            TICKETS.extend(json.load(handle))
    else:
        TICKETS.extend(copy.deepcopy(SEED))


def _save() -> None:
    path = _db_path()
    if path:
        with open(path, "w") as handle:
            json.dump(TICKETS, handle)


def _ok(text: str, structured: Any = None) -> dict:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if structured is not None:
        result["structuredContent"] = structured
    return result


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _handle_call(name: str, args: dict) -> dict:
    if name in ("list_tickets", "dump_db"):
        return _ok(json.dumps({"tickets": TICKETS}), {"tickets": TICKETS})
    if name == "search_tickets":
        query = str(args.get("query", "")).lower()
        hits = [t for t in TICKETS if query in t["subject"].lower()]
        return _ok(json.dumps({"tickets": hits}), {"tickets": hits})
    if name == "delete_all":
        TICKETS.clear()
        return _ok("all tickets deleted")
    if name == "explode":
        return _err("tool exploded")

    ticket = next((t for t in TICKETS if t["id"] == str(args.get("id", ""))), None)
    if name in ("get_ticket", "set_priority", "assign", "add_tag", "close_ticket"):
        if ticket is None:
            return _err(f"no ticket with id {args.get('id')!r}")

    if name == "get_ticket":
        return _ok(json.dumps({"ticket": ticket}), {"ticket": ticket})
    if name == "set_priority":
        priority = str(args.get("priority", ""))
        if priority not in PRIORITIES:
            return _err(f"priority must be one of {list(PRIORITIES)}")
        ticket["priority"] = priority
        return _ok(f"{ticket['id']} priority set to {priority}")
    if name == "assign":
        assignee = str(args.get("assignee", ""))
        if not assignee:
            return _err("assignee is required")
        ticket["assignee"] = assignee
        return _ok(f"{ticket['id']} assigned to {assignee}")
    if name == "add_tag":
        tag = str(args.get("tag", ""))
        if not tag:
            return _err("tag is required")
        if tag not in ticket["tags"]:
            ticket["tags"].append(tag)
        return _ok(f"{ticket['id']} tagged {tag}")
    if name == "close_ticket":
        ticket["status"] = "closed"
        return _ok(f"{ticket['id']} closed")
    raise KeyError(name)


def _dispatch(request: dict) -> dict | None:
    method = request.get("method")
    req_id = request.get("id")
    if method == "initialize":
        result: Any = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "tickets", "version": "0.1.0"},
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
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32602, "message": f"unknown tool {exc.args[0]!r}"}}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def main() -> None:
    _load()
    _save()
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
