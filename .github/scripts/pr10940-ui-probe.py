#!/usr/bin/env python3
"""PR 10940 A/B probe: what the model and the tool card get when an MCP tool returns audio or a file.

Real path: Studio spawns a stdio MCP server, calls its tools through the tool loop, flattens
the results and builds the next provider request. Two deterministic stand-ins:

* stdio MCP server with read_media_file (the official filesystem server's exact shape: an
  audio block mirrored in structuredContent) and export_report (a PDF embedded resource).
* OpenAI-compatible provider that calls both tools, then answers with a readout of what the
  tool messages it received actually held.

EXPECT: BEFORE (fix reverted) the model gets the base64 wav dump from read_media_file and
nothing from export_report; AFTER it gets one short note per attachment and no base64.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from studio_test_kit.auth import login, seed_init_script
from studio_test_kit.ui import open_chat, send_prompt

MODEL_ID = "attachment-reader"
REPORT_URI = "file:///reports/q3-summary.pdf"
AUDIO_NOTE = "[audio attachment (audio/wav) not shown to the model]"
PDF_NOTE = f"[file attachment (application/pdf) <{REPORT_URI}> not shown to the model]"


def _wav_b64() -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(8000)
        w.writeframes(bytes([128]) * 800)
    return base64.b64encode(buf.getvalue()).decode("ascii")


WAV_B64 = _wav_b64()
PDF_B64 = base64.b64encode(
    b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
).decode("ascii")

STUB_MCP_SERVER = r'''
import json, sys
WAV_B64 = "__WAV__"
PDF_B64 = "__PDF__"
TOOLS = [
    {"name": "read_media_file", "description": "Read a media file and return it as base64 content.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "export_report", "description": "Export the quarterly summary report as a file.",
     "inputSchema": {"type": "object", "properties": {"format": {"type": "string"}}}},
]
def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n"); sys.stdout.flush()
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
            "capabilities": {"tools": {}}, "serverInfo": {"name": "files-stub", "version": "1.0.0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        name = req.get("params", {}).get("name")
        if name == "read_media_file":
            item = {"type": "audio", "data": WAV_B64, "mimeType": "audio/wav"}
            result = {"content": [item], "structuredContent": {"content": [item]}, "isError": False}
        else:
            item = {"type": "resource", "resource": {"uri": "__URI__", "mimeType": "application/pdf", "blob": PDF_B64}}
            result = {"content": [item], "isError": False}
        send({"jsonrpc": "2.0", "id": rid, "result": result})
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown method"}})
'''


def pass_log(message: str) -> None:
    print(f"PASS {message}", flush=True)


def fail(message: str) -> None:
    print(f"FAIL {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def find_unsloth_bin(home: Path) -> Path:
    candidates = [home / "bin" / "unsloth", home / "unsloth_studio" / "bin" / "unsloth"]
    candidates.extend(home.glob(".venv*/*/unsloth"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    fail(f"could not find unsloth CLI under {home}")


def read_bootstrap_password(home: Path, log_path: Path) -> str | None:
    for rel in ("auth/.bootstrap_password", ".bootstrap_password"):
        try:
            text = (home / rel).read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            pass
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(r"(?i)(?:bootstrap|initial|generated)\s*password(?:\s+is)?\s*[:=]?\s+(\S+)", log_text)
    return match.group(1).strip().strip(".,") if match else None


def wait_for_health(base_url: str, timeout_s: int = 240) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for path in ("/healthz", "/api/health"):
            try:
                with urllib.request.urlopen(f"{base_url}{path}", timeout=3) as resp:
                    if resp.status < 500:
                        return
            except (urllib.error.URLError, OSError, TimeoutError):
                pass
        time.sleep(2)
    fail(f"Studio did not become healthy within {timeout_s}s")


def start_studio(home: Path, log_path: Path, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["UNSLOTH_STUDIO_HOME"] = str(home)
    env.pop("STUDIO_HOME", None)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        return subprocess.Popen(
            [str(find_unsloth_bin(home)), "studio", "-H", "127.0.0.1", "-p", str(port)],
            stdout=handle, stderr=subprocess.STDOUT, env=env, start_new_session=True,
        )


def stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        proc.kill()


def _text_of(content) -> str:
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return "" if content is None else str(content)


def _describe(content: str) -> str:
    if WAV_B64[:48] in content or PDF_B64[:48] in content:
        return f"{len(content)} characters of encoded file data I cannot read"
    if not content.strip():
        return "nothing at all"
    return f'"{content.strip()}"'


class ProviderState:
    def __init__(self) -> None:
        self.completions = 0
        self.offered: list[str] = []
        self.tool_contents: list[str] = []
        self.answer = ""


def make_provider(state: ProviderState, tools: list[str]) -> type:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def _sse(self, delta: dict, finish=None) -> None:
            chunk = {"id": "chatcmpl-pr10940", "object": "chat.completion.chunk", "created": 0,
                     "model": MODEL_ID, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")

        def do_GET(self):
            body = json.dumps({"data": [{"id": MODEL_ID, "object": "model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            state.completions += 1
            for spec in payload.get("tools") or []:
                name = (spec.get("function") or {}).get("name") or spec.get("name")
                if name and name not in state.offered:
                    state.offered.append(name)
            tool_msgs = [m for m in payload.get("messages", []) if m.get("role") == "tool"]
            state.tool_contents = [_text_of(m.get("content")) for m in tool_msgs]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self._sse({"role": "assistant"})
            if len(tool_msgs) < len(tools):
                i = len(tool_msgs)
                args = json.dumps({"path": "/data/clip.wav"} if i == 0 else {"format": "pdf"})
                # Studio names MCP tools mcp__<server_key>__<tool>; take the exact name it offered
                name = next((t for t in state.offered if t.endswith(f"__{tools[i]}")), tools[i])
                self._sse({"tool_calls": [{"index": 0, "id": f"call_pr10940_{i}", "type": "function",
                                           "function": {"name": name, "arguments": args}}]})
                self._sse({}, finish="tool_calls")
            else:
                state.answer = (f"read_media_file gave me {_describe(state.tool_contents[0])}. "
                                f"export_report gave me {_describe(state.tool_contents[1])}.")
                for word in state.answer.split(" "):
                    self._sse({"content": word + " "})
                self._sse({}, finish="stop")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return Handler


def api(base_url: str, token: str, method: str, path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{base_url}{path}", method=method,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read()
    return json.loads(body) if body else {}


async def run(base_url: str, password: str, artifact_dir: Path, label: str) -> None:
    auth = await login(base_url, "unsloth", password)
    # a fresh home answers 403 "Password change required" until the bootstrap password is rotated
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{base_url}/api/auth/change-password",
                              headers={"Authorization": f"Bearer {auth.access_token}"},
                              json={"current_password": password, "new_password": "UnslothStudioCI2026!"})
    if r.status_code < 400:
        body = r.json()
        auth.access_token = body["access_token"]
        auth.refresh_token = body.get("refresh_token", "")
        pass_log("bootstrap password rotated")
    else:
        print(f"change-password answered {r.status_code}; keeping the login tokens", flush=True)
    token = auth.access_token
    pass_log("Studio login succeeded")

    script = artifact_dir.parent / "pr10940_files_stub.py"
    script.write_text(STUB_MCP_SERVER.replace("__WAV__", WAV_B64).replace("__PDF__", PDF_B64)
                      .replace("__URI__", REPORT_URI))
    server = api(base_url, token, "POST", "/api/mcp/servers/",
                 {"display_name": "Files", "url": f"{sys.executable} {script}", "is_enabled": True})
    pass_log(f"stdio MCP server registered ({server.get('id')})")
    tools = ["read_media_file", "export_report"]

    state = ProviderState()
    port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_provider(state, tools))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    facts: dict = {"label": label, "ref": os.environ.get("GITHUB_SHA", "")}
    shot = artifact_dir / f"{label.lower()}_pr10940.png"
    try:
        api(base_url, token, "POST", "/api/providers/", {
            "provider_type": "custom", "display_name": "Local Orchestrator",
            "base_url": f"http://127.0.0.1:{port}/v1", "models": [MODEL_ID], "available_models": [MODEL_ID],
        })
        async with open_chat(base_url, init_scripts=[seed_init_script(auth, [])], viewport=(1280, 1300)) as sp:
            page = sp.page
            await page.get_by_role("button", name="Select model").first.click(timeout=60_000)
            await page.get_by_role("tab", name="Connected").first.click(timeout=30_000)
            await page.get_by_text(MODEL_ID, exact=True).first.click(timeout=30_000)
            await page.wait_for_timeout(1_500)
            pill = page.locator('form:has(textarea) button[aria-label="MCP servers"]').first
            if await pill.count() == 0:
                await page.get_by_role("button", name="Tools and attachments").first.click(timeout=30_000)
                await page.wait_for_timeout(1_500)
                menu = page.locator("[data-radix-popper-content-wrapper]").last
                await menu.get_by_text("MCP", exact=True).first.click(timeout=15_000)
                await page.wait_for_timeout(1_500)
            await pill.wait_for(state="visible", timeout=15_000)
            await send_prompt(sp, "Read /data/clip.wav, export the Q3 report, and tell me what you got.")

            for _ in range(180):
                if state.answer:
                    break
                await page.wait_for_timeout(1_000)
            await page.get_by_text("export_report gave me", exact=False).first.wait_for(timeout=60_000)
            # consecutive tool calls collapse into one "N tool calls" group; open it first
            group = page.locator('[data-slot="tool-group-trigger"]').first
            if await group.count():
                await group.click()
                await page.locator('[data-slot="tool-fallback-root"]').first.wait_for(timeout=15_000)
            cards = page.locator('[data-slot="tool-fallback-root"]')
            n_cards = await cards.count()
            for i in range(n_cards):
                await cards.nth(i).locator('[data-slot="tool-fallback-trigger"]').first.click()
                await page.wait_for_timeout(500)
            await page.wait_for_timeout(3_000)
            results = page.locator('[data-slot="tool-fallback-result"] pre')
            card_texts = [await results.nth(i).inner_text() for i in range(await results.count())]
            await page.screenshot(path=str(shot), clip={"x": 280, "y": 0, "width": 1000, "height": 1300})

        facts.update({
            "tools_offered": [t for t in tools if t in state.offered],
            "provider_completions": state.completions,
            "tool_cards": n_cards,
            "model_got_read_media_file": state.tool_contents[0] if state.tool_contents else None,
            "model_got_export_report": state.tool_contents[1] if len(state.tool_contents) > 1 else None,
            "model_got_chars": [len(c) for c in state.tool_contents],
            "base64_reached_model": any(WAV_B64[:48] in c or PDF_B64[:48] in c for c in state.tool_contents),
            "tool_card_texts": card_texts,
            "answer": state.answer,
        })
    finally:
        httpd.shutdown()
        (artifact_dir / f"{label.lower()}_facts.json").write_text(json.dumps(facts, indent=2))
        print("FACTS " + json.dumps({k: (v[:160] if isinstance(v, str) else v) for k, v in facts.items()}), flush=True)

    if len(state.tool_contents) != 2 or state.completions != 3 or n_cards != 2:
        fail(f"harness: tool_results={len(state.tool_contents)} completions={state.completions} cards={n_cards}")
    pass_log("harness: both tools ran, 3 completions, 2 tool cards")
    if facts["base64_reached_model"]:
        fail(f"repro: base64 attachment data reached the model ({facts['model_got_chars']} chars)")
    if state.tool_contents[0] != AUDIO_NOTE:
        fail(f"repro: read_media_file result to model was {state.tool_contents[0][:120]!r}")
    if state.tool_contents[1] != PDF_NOTE:
        fail(f"repro: export_report result to model was {state.tool_contents[1][:120]!r}")
    if not any(AUDIO_NOTE in t for t in card_texts) or not any(PDF_NOTE in t for t in card_texts):
        fail(f"repro: tool cards do not show the notes: {card_texts!r}")
    pass_log("each attachment reaches the model and the tool card as a short note, no base64")


async def main() -> None:
    home = Path(os.environ["UNSLOTH_STUDIO_HOME"]).resolve()
    artifact_dir = Path(os.environ.get("STUDIO_ARTIFACT_DIR", "studio-live-artifacts")).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    label = "BEFORE" if "-before-" in os.environ.get("GITHUB_REF_NAME", "") else "AFTER"
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = artifact_dir / "studio.log"
    proc = start_studio(home, log_path, port)
    try:
        wait_for_health(base_url)
        password = read_bootstrap_password(home, log_path)
        if not password:
            fail("could not read Studio bootstrap password")
        await run(base_url, password, artifact_dir, label)
    finally:
        stop_process(proc)


if __name__ == "__main__":
    asyncio.run(main())
