"""Stdio MCP server replaying Notion's tool catalog and recording every tools/call.

argv: catalog.json calls.jsonl marker.txt. Arguments are validated against the tool's inputSchema like a real
server would, and an invalid call answers isError with the validation messages; scoring is still offline.
"""
import json
import sys
import time
import uuid

import jsonschema

catalog, log_path, marker_path = sys.argv[1], sys.argv[2], sys.argv[3]
tools = json.load(open(catalog))
schemas = {t["name"]: t.get("inputSchema") or {"type": "object"} for t in tools}


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def task_id():
    try:
        return open(marker_path).read().strip()
    except OSError:
        return ""


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except ValueError:
        continue
    method, rid = req.get("method"), req.get("id")
    if rid is None:
        continue
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": req.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "notion-recorder", "version": "1.0.0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})
    elif method == "tools/call":
        params = req.get("params") or {}
        with open(log_path, "a") as fh:
            fh.write(json.dumps({"task_id": task_id(), "name": params.get("name"),
                                 "arguments": params.get("arguments"), "t": time.time()}) + "\n")
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in schemas:
            errors = [f"Unknown tool: {name}"]
        else:
            validator = jsonschema.validators.validator_for(schemas[name])(schemas[name])
            errors = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
                      for e in sorted(validator.iter_errors(args), key=lambda e: list(map(str, e.absolute_path)))][:8]
        if errors:
            text = "MCP error -32602: Invalid arguments for tool " + str(name) + ":\n" + "\n".join(errors)
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": text}], "isError": True}})
        else:
            body = {"ok": True, "id": str(uuid.uuid4()), "message": "Done."}
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": json.dumps(body)}], "isError": False}})
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": rid, "result": {}})
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown method"}})
