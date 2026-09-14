"""Scene: what the model is handed when a tool result merely QUOTES `__IMAGES__:`.

PR 10668 is one BACKEND function (`strip_result_for_model`), so nothing may be
seeded: a stored thread replays bytes the frontend already has and photographs
identically on both sides. The whole path runs for real -- Studio spawns a stdio
MCP server, calls its tool, and builds the next request to the provider.

The MCP tool answers with a build log whose second line contains the literal text
`__IMAGES__:` in the MIDDLE of a line (an uploader's source, quoted in the log),
followed by the two lines the user actually asked about: the artifact URL and its
sha256.

BEFORE, `strip_result_for_model` truncates at the FIRST occurrence of the marker
anywhere in the string, so the model is handed a log that stops mid-line and the
artifact URL and checksum never reach it. AFTER, only a structurally valid
trailing envelope is stripped, so the whole log arrives.

The tool CARD is the control: the card renders the raw result, and the frontend
slices only a line-anchored marker, so the user sees all four lines on BOTH
sides. That is the point of the PR -- nothing looks wrong on screen while the
model is answering from a truncated log.

The stand-in provider answers with a sentence that is a pure function of what it
was actually handed, so the assistant bubble is the backend's own decision quoted
back rather than a stand-in's opinion about it.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post, pick_free_ports  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat, send_prompt  # noqa: E402

MODEL_ID = "build-log-reader"

ARTIFACT_URL = "https://builds.example.com/unsloth-42.tar.gz"
CHECKSUM = "9f2c1ad4b7e60f38"

# The marker sits mid-line, exactly as it does in the uploader source a build log
# quotes. `_defuse_sentinels` only breaks a marker that STARTS a line, so this is
# what really reaches `strip_result_for_model`.
LOG_LINES = [
    "build-42.log",
    'uploader.py:88:    out += f"\\n__IMAGES__:{json.dumps(images)}"',
    f"artifact: {ARTIFACT_URL}",
    f"sha256: {CHECKSUM}",
]
LOG_TEXT = "\n".join(LOG_LINES)

ANSWER_MARK = "From the log I was handed:"

STUB_MCP_SERVER = r'''#!/usr/bin/env python3
"""Stdio MCP server whose one tool returns a build log that quotes the marker."""
import json
import sys

LOG_TEXT = {log_text!r}

TOOL = {{
    "name": "read_build_log",
    "description": "Read the build log for a release.",
    "inputSchema": {{"type": "object", "properties": {{}}}},
}}


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


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
            send({{"jsonrpc": "2.0", "id": rid, "result": {{
                "protocolVersion": req.get("params", {{}}).get(
                    "protocolVersion", "2025-06-18"),
                "capabilities": {{"tools": {{}}}},
                "serverInfo": {{"name": "build-log-stub", "version": "1.0.0"}}}}}})
        elif method == "tools/list":
            send({{"jsonrpc": "2.0", "id": rid, "result": {{"tools": [TOOL]}}}})
        elif method == "tools/call":
            send({{"jsonrpc": "2.0", "id": rid, "result": {{
                "content": [{{"type": "text", "text": LOG_TEXT}}],
                "isError": False}}}})
        elif rid is not None:
            send({{"jsonrpc": "2.0", "id": rid, "error": {{
                "code": -32601, "message": "unknown method " + str(method)}}}})


if __name__ == "__main__":
    main()
'''


class _ProviderState:
    """What the fake provider was handed, so the scene can prove the tool ran and
    show exactly how much of the log survived the trip."""

    def __init__(self) -> None:
        self.completions = 0
        self.offered_tools: list[str] = []
        self.tool_result_text = ""


def _sse(chunk: dict) -> bytes:
    return b"data: " + json.dumps(chunk).encode() + b"\n\n"


def _delta(delta: dict, finish=None) -> dict:
    return {
        "id": "chatcmpl-pr10668",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _answer_for(text: str) -> str:
    """What a model would say having been handed this much of the log.

    Deliberately mechanical: it reports only whether the two lines the user asked
    about are present in the text this request carried, which is the single thing
    the reviewed function decides.
    """
    if ARTIFACT_URL in text and CHECKSUM in text:
        return f"{ANSWER_MARK} artifact {ARTIFACT_URL}, sha256 {CHECKSUM}."
    n_lines = len([line for line in text.splitlines() if line])
    return (
        f"{ANSWER_MARK} it stops after {n_lines} line(s) and carries no artifact URL "
        f"and no checksum, so I cannot give you either."
    )


def _make_provider(state: _ProviderState, tool_name: str) -> type:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):  # noqa: A003 -- silence stderr spam
            pass

        def do_GET(self):  # noqa: N802
            body = json.dumps({"data": [{"id": MODEL_ID, "object": "model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            payload = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"
            )
            state.completions += 1
            for spec in payload.get("tools") or []:
                name = (spec.get("function") or {}).get("name") or spec.get("name")
                if name:
                    state.offered_tools.append(name)

            tool_msgs = [m for m in payload.get("messages", []) if m.get("role") == "tool"]
            if tool_msgs:
                content = tool_msgs[0].get("content")
                # Rebuilt from the request rather than appended to, because the
                # client may retry a turn and a growing value would double-count.
                state.tool_result_text = (
                    content if isinstance(content, str) else json.dumps(content)
                )

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(_sse(_delta({"role": "assistant"})))
            if tool_msgs:
                for word in _answer_for(state.tool_result_text).split(" "):
                    self.wfile.write(_sse(_delta({"content": word + " "})))
                self.wfile.write(_sse(_delta({}, finish="stop")))
            else:
                self.wfile.write(_sse(_delta({"tool_calls": [{
                    "index": 0, "id": "call_pr10668", "type": "function",
                    "function": {"name": tool_name, "arguments": ""}}]})))
                self.wfile.write(_sse(_delta({"tool_calls": [{
                    "index": 0, "function": {"arguments": "{}"}}]})))
                self.wfile.write(_sse(_delta({}, finish="tool_calls")))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return Handler


def _reset_connections(session: Session) -> None:
    """Drop provider / MCP rows this home already holds: a leftover row pointing at
    a dead port wins the model lookup and reads exactly like the tool never running."""
    for path in ("/api/providers/", "/api/mcp/servers/"):
        for row in api_get(session, path):
            req = urllib.request.Request(
                f"{session.base_url}{path}{row['id']}",
                headers={"Authorization": f"Bearer {session.access_token}"},
                method="DELETE",
            )
            urllib.request.urlopen(req, timeout=60)


def _register_provider(session: Session, port: int) -> str:
    """Registered as "custom", not "openai": the openai type routes to
    /v1/responses and this stand-in speaks chat/completions."""
    created = api_post(session, "/api/providers/", {
        "provider_type": "custom",
        "display_name": "Local Build Log Reader",
        "base_url": f"http://127.0.0.1:{port}/v1",
        "models": [MODEL_ID],
        "available_models": [MODEL_ID],
    })
    return created["id"]


def _register_mcp_server(session: Session, script: Path) -> str:
    created = api_post(session, "/api/mcp/servers/", {
        "display_name": "Builds",
        "url": f"{sys.executable} {script}",
        "is_enabled": True,
    })
    return created["id"]


async def _select_connected_model(page, model_id: str) -> None:
    await page.get_by_role("button", name="Select model").first.click(timeout=60_000)
    await page.get_by_role("tab", name="Connected").first.click(timeout=30_000)
    await page.get_by_text(model_id, exact=True).first.click(timeout=30_000)


async def _enable_mcp_tools(page) -> None:
    """Turn the composer's MCP pill on. Registering a server is not enough: the
    request carries whatever the toggles say. The menu entry TOGGLES, and the
    preference outlives a run, so it is only clicked when the pill is absent."""
    pill = page.locator('form:has(textarea) button[aria-label="MCP servers"]').first
    if await pill.count() == 0:
        await page.get_by_role("button", name="Tools and attachments").first.click(timeout=30_000)
        await page.wait_for_timeout(1_500)
        menu = page.locator("[data-radix-popper-content-wrapper]").last
        await menu.get_by_text("MCP", exact=True).first.click(timeout=15_000)
        await page.wait_for_timeout(1_500)
    await pill.wait_for(state="visible", timeout=15_000)


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    *,
    prompt: str = (
        "Read the build log for release 42 and tell me the artifact URL and its sha256."
    ),
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph the answer after a tool result that only quotes the marker."""
    # One shared path, so the registered command -- and every fact derived from it --
    # is identical BEFORE and AFTER.
    script = Path(tempfile.gettempdir()) / "pr10668_stub_mcp_server.py"
    script.write_text(STUB_MCP_SERVER.format(log_text=LOG_TEXT))
    script.chmod(0o755)

    _reset_connections(session)
    server_id = _register_mcp_server(session, script)
    tool_name = f"mcp__{server_id}__read_build_log"

    state = _ProviderState()
    port = pick_free_ports(1, start=9400, stop=9500)[0]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _make_provider(state, tool_name))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    try:
        provider_id = _register_provider(session, port)
        auth = type("A", (), {
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
        })()

        async with open_chat(
            session.base_url,
            init_scripts=[seed_init_script(auth, [])],
            viewport=(1280, 1100),
            headless=True,
        ) as sp:
            page = sp.page
            await _select_connected_model(page, MODEL_ID)
            await page.wait_for_timeout(1_500)
            await _enable_mcp_tools(page)
            await send_prompt(sp, prompt)

            card = page.locator('[data-slot="tool-fallback-root"]').first
            await card.wait_for(state="visible", timeout=120_000)
            trigger = card.locator('[data-slot="tool-fallback-trigger"]').first
            if await trigger.count():
                try:
                    await trigger.click(timeout=15_000)
                except Exception:  # noqa: BLE001 -- already open
                    pass
            # Keyed on the sentence the stand-in only writes once the result is in,
            # so neither side is photographed mid-stream.
            await page.get_by_text(ANSWER_MARK, exact=False).first.wait_for(timeout=90_000)
            for _attempt in range(60):
                if state.completions >= 2:
                    break
                await page.wait_for_timeout(1_000)
            await page.wait_for_timeout(4_000)

            card_text = " ".join((await card.inner_text()).split())
            transcript = " ".join((await page.locator("main").first.inner_text()).split())

            shot = out_dir / f"{label.lower()}_tool_result_sentinel_anchor.png"
            # A FIXED clip of the chat column, not an element shot: the card grows
            # with what it renders, and element shots of different sizes scale one
            # half of the composite to illegibility. The clip starts right of the
            # sidebar, whose recents list is per-home.
            await page.screenshot(
                path=str(shot),
                clip={"x": 280, "y": 0, "width": 1000, "height": 1100},
            )

            handed = state.tool_result_text
            facts = {
                "provider_registered": bool(provider_id),
                "tool_offered_to_model": tool_name in state.offered_tools,
                "provider_completions": state.completions,
                # Control: the tool returned the same bytes on both sides.
                "mcp_tool_returned_chars": len(LOG_TEXT),
                # Control: the CARD the user reads is whole on both sides.
                "card_shows_artifact_url": ARTIFACT_URL in card_text,
                "card_shows_checksum": CHECKSUM in card_text,
                "card_text": card_text,
                # The measurement.
                "model_received_chars": len(handed),
                "chars_lost_before_model": len(LOG_TEXT) - len(handed),
                "model_received_lines": len([ln for ln in handed.splitlines() if ln]),
                "model_received_artifact_url": ARTIFACT_URL in handed,
                "model_received_checksum": CHECKSUM in handed,
                "model_received_tail": " ".join(handed.split())[-160:],
                "ui_answer_gives_artifact_url": ARTIFACT_URL in transcript,
                "ui_answer_says_it_cannot": "cannot give you either" in transcript,
            }
            return [shot], facts
    finally:
        httpd.shutdown()
        httpd.server_close()
