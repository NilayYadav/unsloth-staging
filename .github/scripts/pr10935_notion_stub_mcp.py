#!/usr/bin/env python3
"""Stdio MCP stand-in for Notion's notion-query-data-sources, schema copied verbatim.

Every tools/call is appended to the JSONL path in argv[1], so the probe reads the
exact arguments Studio executed, not what the UI chose to render.
"""
import json
import sys

LOG = sys.argv[1] if len(sys.argv) > 1 else "/tmp/pr10935_stub_calls.jsonl"

SQL_BRANCH = {
    "type": "object",
    "properties": {
        "data_source_urls": {"maxItems": 100, "type": "array", "items": {"type": "string"}},
        "query": {"type": "string"},
        "mode": {"type": "string", "enum": ["sql"]},
        "params": {
            "maxItems": 100,
            "type": "array",
            "items": {"anyOf": [{"type": "string"}, {"type": "number"},
                                {"type": "boolean"}, {"type": "null"}]},
        },
    },
    "required": ["data_source_urls", "query"],
    "additionalProperties": False,
}
VIEW_BRANCH = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["view"]},
        "view_url": {"type": "string"},
        "start_cursor": {"type": "string", "minLength": 1, "maxLength": 200},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
        "is_archived": {"type": "boolean"},
    },
    "required": ["mode", "view_url"],
    "additionalProperties": False,
}
TOOL = {
    "name": "notion-query-data-sources",
    "description": "Query a Notion database view. Pass start_cursor to fetch the next page.",
    "inputSchema": {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "data": {
                "description": "The data required for querying data sources",
                "anyOf": [SQL_BRANCH, VIEW_BRANCH],
            }
        },
        "required": ["data"],
        "additionalProperties": {},
    },
}


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def answer(arguments):
    data = arguments.get("data") if isinstance(arguments, dict) else None
    data = data if isinstance(data, dict) else {}
    cursor = data.get("start_cursor")
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"arguments": arguments, "key_order": list(data)}) + "\n")
    if cursor:
        return f"start_cursor received: {cursor}. Returned rows 26-50 (page 2)."
    return "start_cursor MISSING. Returned rows 1-25 again (page 1)."


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, rid = req.get("method"), req.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": req.get("params", {}).get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "notion-stub", "version": "1.0.0"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [TOOL]}})
        elif method == "tools/call":
            arguments = (req.get("params") or {}).get("arguments") or {}
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": answer(arguments)}],
                "isError": False}})
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32601, "message": "unknown method " + str(method)}})


if __name__ == "__main__":
    main()
