"""Scene: what the model is actually handed when an MCP tool answers with an image.

PR 10088 is a BACKEND change, so nothing here may seed a stored conversation: a
seeded replay photographs identically on both sides. The whole path runs for real
-- Studio spawns a stdio MCP server, calls its tool, flattens the image, and
builds the next request to the provider.

Two stand-ins make that deterministic without weights or a GPU:

* a stdio MCP server whose one tool returns an ImageContent block holding a plain
  blue square.
* a local OpenAI-compatible provider, registered as a VISION model, that answers
  with what it can actually see. If the second request carries an image part it
  says so and names the colour; if it carries only the tool's note it says it
  cannot see the picture. The sentence is a truthful function of the payload the
  backend built, which is exactly the thing under review.

Both Studios build the same frontend, so the renderer is constant and the only
thing that can move the answer is what the backend put in the request.
"""

from __future__ import annotations

import base64
import io
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


def _blue_square_b64() -> str:
    """A 192x192 flat blue PNG. Flat on purpose: the provider's answer names the
    colour, so the assertion is about a property the pixels really carry."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (192, 192), (24, 90, 219)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


IMAGE_B64 = _blue_square_b64()

STUB_MCP_SERVER = r'''#!/usr/bin/env python3
"""Stdio MCP server whose one tool answers with an ImageContent block."""
import json
import sys

IMAGE_B64 = "{image_b64}"

TOOL = {{
    "name": "screenshot",
    "description": "Take a screenshot of the current display.",
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
                "serverInfo": {{"name": "screen-stub", "version": "1.0.0"}}}}}})
        elif method == "tools/list":
            send({{"jsonrpc": "2.0", "id": rid, "result": {{"tools": [TOOL]}}}})
        elif method == "tools/call":
            send({{"jsonrpc": "2.0", "id": rid, "result": {{
                "content": [{{"type": "image", "data": IMAGE_B64,
                              "mimeType": "image/png"}}],
                "isError": False}}}})
        elif rid is not None:
            send({{"jsonrpc": "2.0", "id": rid, "error": {{
                "code": -32601, "message": "unknown method " + str(method)}}}})


if __name__ == "__main__":
    main()
'''

MODEL_ID = "screen-reader-vl"
SAW_IT = "I can see the screenshot: it is a solid blue square."
BLIND = "I cannot see the screenshot, so I can only guess what is in it."


def _image_parts(messages) -> int:
    n = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            n += sum(
                1
                for part in content
                if isinstance(part, dict)
                and part.get("type") in ("image", "image_url", "input_image")
            )
    return n


class _ProviderState:
    """What the fake provider was handed, so the scene can prove the tool ran and
    show what the second request actually carried."""

    def __init__(self) -> None:
        self.completions = 0
        self.offered_tools: list[str] = []
        self.post_tool_image_parts = 0
        self.post_tool_roles: list[str] = []
        self.post_tool_extra_turn = ""
        self.tool_note = ""
        self.envelope_in_payload = False


def _sse(chunk: dict) -> bytes:
    return b"data: " + json.dumps(chunk).encode() + b"\n\n"


def _delta(delta: dict, finish=None) -> dict:
    return {
        "id": "chatcmpl-pr10088",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _make_provider(state: _ProviderState, tool_name: str) -> type:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):  # noqa: A003
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

            messages = payload.get("messages", [])
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            if tool_msgs:
                # The turn after the tool ran: the one the change rewrites.
                state.post_tool_image_parts = _image_parts(messages)
                state.post_tool_roles = [m.get("role") for m in messages]
                state.tool_note = str(tool_msgs[0].get("content"))[:120]
                state.envelope_in_payload = "__MCP_IMAGES__" in json.dumps(messages)
                trailing = messages[-1] if messages else {}
                if trailing.get("role") == "user" and isinstance(
                    trailing.get("content"), list
                ):
                    texts = [
                        p.get("text", "")
                        for p in trailing["content"]
                        if isinstance(p, dict) and p.get("type") == "text"
                    ]
                    state.post_tool_extra_turn = " ".join(texts)[:120]

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(_sse(_delta({"role": "assistant"})))
            if tool_msgs:
                # Truthful: the sentence reports what this request actually carried.
                answer = SAW_IT if state.post_tool_image_parts else BLIND
                for word in answer.split(" "):
                    self.wfile.write(_sse(_delta({"content": word + " "})))
                self.wfile.write(_sse(_delta({}, finish="stop")))
            else:
                self.wfile.write(_sse(_delta({"tool_calls": [{
                    "index": 0, "id": "call_pr10088", "type": "function",
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
    """Registered as "custom" (the openai type routes to /v1/responses, and this
    stand-in speaks chat/completions) and declared vision-capable, which is the
    gate the backend reads before it promotes an image."""
    created = api_post(session, "/api/providers/", {
        "provider_type": "custom",
        "display_name": "Local Vision Orchestrator",
        "base_url": f"http://127.0.0.1:{port}/v1",
        "models": [MODEL_ID],
        "available_models": [MODEL_ID],
        "vision_models": [MODEL_ID],
    })
    return created["id"]


def _register_mcp_server(session: Session, script: Path) -> str:
    created = api_post(session, "/api/mcp/servers/", {
        "display_name": "Screen",
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
    prompt: str = "Take a screenshot and tell me what colour it is.",
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph the answer a vision model gives after an MCP tool returns a picture."""
    # One shared path, so the registered command -- and every fact derived from it --
    # is identical BEFORE and AFTER.
    script = Path(tempfile.gettempdir()) / "pr10088_stub_mcp_server.py"
    script.write_text(STUB_MCP_SERVER.format(image_b64=IMAGE_B64))
    script.chmod(0o755)

    _reset_connections(session)
    server_id = _register_mcp_server(session, script)
    tool_name = f"mcp__{server_id}__screenshot"

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
            viewport=(1280, 1000),
            headless=True,
        ) as sp:
            page = sp.page
            await _select_connected_model(page, MODEL_ID)
            await page.wait_for_timeout(1_500)
            await _enable_mcp_tools(page)
            await send_prompt(sp, prompt)

            card = page.locator('[data-slot="tool-fallback-root"]').first
            await card.wait_for(state="visible", timeout=120_000)
            await card.locator('[data-slot="tool-fallback-trigger"]').first.click()
            # The answer only lands after the second completion; wait for the
            # sentence rather than a fixed beat so neither side is photographed
            # mid-stream.
            await page.get_by_text("screenshot", exact=False).first.wait_for(timeout=60_000)
            for _attempt in range(60):
                if state.completions >= 2:
                    break
                await page.wait_for_timeout(1_000)
            await page.wait_for_timeout(4_000)

            shot = out_dir / f"{label.lower()}_mcp_images_to_model.png"
            # A fixed clip of the chat column, not an element shot: the card grows
            # with what it renders, and element shots of different sizes scale one
            # half of the composite to illegibility. The clip starts right of the
            # sidebar, whose recents list is per-home.
            await page.screenshot(
                path=str(shot),
                clip={"x": 280, "y": 0, "width": 1000, "height": 1000},
            )

            transcript = " ".join((await page.locator("main").first.inner_text()).split())
            facts = {
                "provider_registered": bool(provider_id),
                "tool_offered_to_model": tool_name in state.offered_tools,
                "provider_completions": state.completions,
                "post_tool_image_parts": state.post_tool_image_parts,
                "post_tool_roles": state.post_tool_roles,
                "promoted_turn_text": state.post_tool_extra_turn,
                "tool_note_to_model": state.tool_note,
                "envelope_leaked_to_provider": state.envelope_in_payload,
                "model_says_it_saw_the_image": SAW_IT in transcript,
                "model_says_it_is_blind": BLIND in transcript,
                "transcript_char_count": len(transcript),
            }
            return [shot], facts
    finally:
        httpd.shutdown()
        httpd.server_close()
