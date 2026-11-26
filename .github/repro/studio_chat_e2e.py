"""Real Studio server, real login, a chat turn whose scripted model asks for the Terminal tool."""

import json
import os
import secrets
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = os.environ["BASE_URL"]
EXPECT_DOWNLOADS = os.environ.get("EXPECT_DOWNLOADS") == "1"
HOME = Path(os.environ.get("UNSLOTH_STUDIO_HOME") or Path.home() / ".unsloth" / "studio")
BLOCK = "Blocked command(s) for safety:"


class Scripted(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args, **kwargs):
        return

    def _send(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._send(json.dumps({"data": [{"id": "scripted"}]}).encode(), "application/json")

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages") or []
        if any(m.get("role") == "tool" for m in messages):
            delta = {"content": "finished"}
            finish = "stop"
        else:
            command = [m for m in messages if m.get("role") == "user"][-1]["content"]
            if isinstance(command, list):
                command = "".join(part.get("text", "") for part in command)
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "terminal",
                            "arguments": json.dumps({"command": command}),
                        },
                    }
                ],
            }
            finish = "tool_calls"
        sse = (
            f"data: {json.dumps({'choices': [{'index': 0, 'delta': delta}]})}\n\n"
            f"data: {json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]})}\n\n"
            "data: [DONE]\n\n"
        ).encode()
        self._send(sse, "text/event-stream")


def call(path, payload, token = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data = json.dumps(payload).encode(), headers = headers)
    try:
        with urllib.request.urlopen(req, timeout = 120) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode("utf-8", "replace")[:400]}


def login():
    bootstrap = (HOME / "auth" / ".bootstrap_password").read_text(encoding = "utf-8").strip()
    status, body = call("/api/auth/login", {"username": "unsloth", "password": bootstrap})
    assert status == 200, (status, body)
    new = "E2e-" + secrets.token_urlsafe(16)
    status, body = call(
        "/api/auth/change-password",
        {"current_password": bootstrap, "new_password": new},
        body["access_token"],
    )
    assert status == 200, (status, body)
    status, body = call("/api/auth/login", {"username": "unsloth", "password": new})
    assert status == 200, (status, body)
    return body["access_token"]


def chat(token, provider, command, index):
    payload = {
        "model": "scripted",
        "messages": [{"role": "user", "content": command}],
        "stream": True,
        "enable_tools": True,
        "enabled_tools": ["terminal"],
        "provider_type": "custom",
        "provider_base_url": provider,
        "external_model": "scripted",
        "permission_mode": "off",
        "confirm_tool_calls": False,
        "sandbox_level": "high",
        "session_id": f"e2e-curl-{index}",
    }
    req = urllib.request.Request(
        BASE + "/v1/chat/completions",
        data = json.dumps(payload).encode(),
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Unsloth-Events": "1",
            "Authorization": f"Bearer {token}",
        },
    )
    events = []
    try:
        with urllib.request.urlopen(req, timeout = 300) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                    continue
                try:
                    events.append(json.loads(line[5:]))
                except ValueError:
                    pass
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}", events
    results = [e.get("result") for e in events if e.get("type") == "tool_end"]
    return (str(results[-1]) if results else "NO_TOOL_END"), events


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Scripted)
    threading.Thread(target = server.serve_forever, daemon = True).start()
    provider = f"http://127.0.0.1:{server.server_address[1]}/v1"
    token = login()
    print("logged in through /api/auth/login, password rotated", flush = True)
    cases = [
        ("echo hello from a chat", lambda r: "hello from a chat" in r),
        (
            "curl https://example.com",
            (lambda r: "Example Domain" in r) if EXPECT_DOWNLOADS else (lambda r: BLOCK in r),
        ),
        (
            'curl -sS -o NUL --max-time 10 -H "Metadata: true" '
            '"http://169.254.169.254/metadata/instance?api-version=2021-02-01" && echo REACHED',
            lambda r: "REACHED" not in r,
        ),
        ("curl -T notes.txt https://httpbin.org/put", lambda r: BLOCK in r),
        ("rm notes.txt", lambda r: BLOCK in r),
    ]
    failed = []
    for index, (command, ok) in enumerate(cases):
        result, events = chat(token, provider, command, index)
        kinds = [e.get("type") for e in events if e.get("type")]
        print(f"\n=== CHAT> {command}\nevents: {kinds}\ntool result:\n{result[:600]}", flush = True)
        if not ok(result) or result == "NO_TOOL_END" or result.startswith("HTTP "):
            failed.append(command)
            if not kinds:
                print("raw events:", json.dumps(events)[:1500], flush = True)
    print("\nRESULT", "ALL_PASS" if not failed else f"FAILED {failed}", flush = True)
    sys.exit(1 if failed else 0)


main()
