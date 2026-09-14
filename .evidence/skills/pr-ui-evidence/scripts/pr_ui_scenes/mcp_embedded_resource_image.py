"""Scene: the chat tool card for an MCP tool that answers with an image.

The change under review is in the BACKEND (`_flatten_result`), so nothing here may
seed a stored result: a seeded conversation replays bytes the frontend already has
and photographs identically on both sides. The whole path has to run for real —
Studio spawns a stdio MCP server, calls its tool, flattens the MCP content blocks,
streams the result to the SPA, and the SPA draws the card.

Two stand-ins make that deterministic without weights or a GPU:

* a stdio MCP server (`STUB_MCP_SERVER`) speaking raw JSON-RPC, whose one tool
  returns its image the way FastMCP's `File` helper does — an EmbeddedResource
  wrapping BlobResourceContents, NOT an ImageContent block. That distinction is
  the entire subject of the comparison.
* a local OpenAI-compatible server that emits a tool call on the first turn and a
  sentence on the second, so the model's decision to call the tool is not a
  variable between the sides.

Both Studios build the same frontend (the diff is one backend file), so the card
renderer is constant and the only thing that can move the picture is what the
backend made of the MCP result.
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

# A 192x192 PNG of concentric rings, generated rather than sampled: it has to be
# unmistakably a picture at card size so the composite cannot be read as "some
# grey box appeared".
IMAGE_B64 = (
    "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"
)

STUB_MCP_SERVER = r'''#!/usr/bin/env python3
"""Stdio MCP server returning an image as an EmbeddedResource (FastMCP File shape)."""
import json
import sys

IMAGE_B64 = "{image_b64}"
MIMES = {mimes}

TOOL = {{
    "name": "generate_image",
    "description": "Render an image from a text prompt with the local ComfyUI graph.",
    "inputSchema": {{
        "type": "object",
        "properties": {{"prompt": {{"type": "string"}}}},
        "required": ["prompt"],
    }},
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
                "serverInfo": {{"name": "comfy-stub", "version": "1.0.0"}}}}}})
        elif method == "tools/list":
            send({{"jsonrpc": "2.0", "id": rid, "result": {{"tools": [TOOL]}}}})
        elif method == "tools/call":
            send({{"jsonrpc": "2.0", "id": rid, "result": {{
                "content": [
                    {{
                        "type": "resource",
                        "resource": {{
                            "uri": "file:///ComfyUI/output/gen_%02d.png" % i,
                            "mimeType": m,
                            "blob": IMAGE_B64,
                        }},
                    }}
                    for i, m in enumerate(MIMES)
                ],
                "isError": False}}}})
        elif rid is not None:
            send({{"jsonrpc": "2.0", "id": rid, "error": {{
                "code": -32601, "message": "unknown method " + str(method)}}}})


if __name__ == "__main__":
    main()
'''

MODEL_ID = "comfy-orchestrator"
ANSWER = "Here is the image I generated with the local ComfyUI graph."


class _ProviderState:
    """What the fake provider saw, so the scene can prove the tool actually ran."""

    def __init__(self) -> None:
        self.completions = 0
        self.tool_result_texts: list[str] = []
        self.offered_tools: list[str] = []


def _sse(chunk: dict) -> bytes:
    return b"data: " + json.dumps(chunk).encode() + b"\n\n"


def _delta(delta: dict, finish=None) -> dict:
    return {
        "id": "chatcmpl-pr9584",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


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
                # chat/completions nests the name under "function"; the Responses
                # API puts it at the top level. Read both so an empty list is a
                # real "no tools offered" rather than a parser miss.
                name = (spec.get("function") or {}).get("name") or spec.get("name")
                if name:
                    state.offered_tools.append(name)
            # The second turn is the one that carries the tool's output back. Keeping
            # it lets the scene assert the model was handed the flattened result, which
            # is the thing the backend change rewrites.
            tool_msgs = [m for m in payload.get("messages", []) if m.get("role") == "tool"]
            for m in tool_msgs:
                content = m.get("content")
                state.tool_result_texts.append(
                    content if isinstance(content, str) else json.dumps(content)
                )

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(_sse(_delta({"role": "assistant"})))
            if tool_msgs:
                for word in ANSWER.split(" "):
                    self.wfile.write(_sse(_delta({"content": word + " "})))
                self.wfile.write(_sse(_delta({}, finish="stop")))
            else:
                self.wfile.write(_sse(_delta({"tool_calls": [{
                    "index": 0, "id": "call_pr9584", "type": "function",
                    "function": {"name": tool_name, "arguments": ""}}]})))
                self.wfile.write(_sse(_delta({"tool_calls": [{
                    "index": 0,
                    "function": {"arguments": json.dumps({"prompt": "a sunset over water"})}}]})))
                self.wfile.write(_sse(_delta({}, finish="tool_calls")))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return Handler


def _reset_connections(session: Session) -> None:
    """Drop any provider / MCP rows this home already holds.

    A home is reused across runs, and a leftover row pointing at a port nothing
    is listening on wins the model lookup: the chat then fails to connect and no
    tool card is ever drawn, which reads exactly like the tool not being called.
    """
    for path in ("/api/providers/", "/api/mcp/servers/"):
        for row in api_get(session, path):
            req = urllib.request.Request(
                f"{session.base_url}{path}{row['id']}",
                headers={"Authorization": f"Bearer {session.access_token}"},
                method="DELETE",
            )
            urllib.request.urlopen(req, timeout=60)


def _register_provider(session: Session, port: int) -> str:
    """Save the fake endpoint as a connection through the REAL provider API.

    Seeding localStorage is not enough: the picker's "Connected" tab only exists
    once the backend holds a provider row, so the model is otherwise unselectable.
    Registered as "custom", not "openai": the openai type routes to the Responses
    API (/v1/responses), and this stand-in speaks chat/completions.
    """
    created = api_post(session, "/api/providers/", {
        "provider_type": "custom",
        "display_name": "Local Orchestrator",
        "base_url": f"http://127.0.0.1:{port}/v1",
        "models": [MODEL_ID],
        "available_models": [MODEL_ID],
    })
    return created["id"]


async def _select_connected_model(page, model_id: str) -> None:
    """Pick `model_id` from the header picker's Connected tab.

    Not studio_test_kit.pick_model: that scopes the trigger to the composer form,
    and this build puts the picker in the header. The tab is role="tab", not a
    button, so it has to be addressed as one.
    """
    await page.get_by_role("button", name="Select model").first.click(timeout=60_000)
    await page.get_by_role("tab", name="Connected").first.click(timeout=30_000)
    await page.get_by_text(model_id, exact=True).first.click(timeout=30_000)


async def _enable_mcp_tools(page) -> None:
    """Turn on the composer's MCP pill.

    Registering a server is not enough: the request carries whatever the UI's
    tool toggles say, so without this the model is offered no tools at all and
    simply answers in prose. The failure is silent -- a normal-looking reply and
    no tool card -- so the pill is asserted on afterwards.
    """
    pill = page.locator('form:has(textarea) button[aria-label="MCP servers"]').first
    if await pill.count() == 0:
        # The menu entry TOGGLES the pill, and the preference outlives a run, so
        # clicking unconditionally turns MCP back off on the second pass.
        await page.get_by_role("button", name="Tools and attachments").first.click(timeout=30_000)
        # The menu animates in; clicking before it settles is swallowed by the overlay.
        await page.wait_for_timeout(1_500)
        menu = page.locator("[data-radix-popper-content-wrapper]").last
        await menu.get_by_text("MCP", exact=True).first.click(timeout=15_000)
        await page.wait_for_timeout(1_500)
    await pill.wait_for(state="visible", timeout=15_000)


def _register_mcp_server(session: Session, script: Path) -> str:
    created = api_post(session, "/api/mcp/servers/", {
        "display_name": "ComfyUI",
        "url": f"{sys.executable} {script}",
        "is_enabled": True,
    })
    return created["id"]


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    *,
    prompt: str = "Generate an image of a sunset over water.",
    mimes: tuple = ("image/png",),
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph the tool card after a real MCP call that answers with an image."""
    # A path shared by both sides, so the registered command — and therefore every
    # fact derived from it — is identical BEFORE and AFTER.
    script = Path(tempfile.gettempdir()) / "pr9584_stub_mcp_server.py"
    script.write_text(
        STUB_MCP_SERVER.format(image_b64=IMAGE_B64, mimes=repr(list(mimes)))
    )
    script.chmod(0o755)

    _reset_connections(session)
    server_id = _register_mcp_server(session, script)
    tool_name = f"mcp__{server_id}__generate_image"

    state = _ProviderState()
    port = pick_free_ports(1, start=9300, stop=9400)[0]
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
            # The card is collapsed by default, and the result pane is what the
            # backend change rewrites, so it must be open on BOTH sides.
            await card.locator('[data-slot="tool-fallback-trigger"]').first.click()
            await page.wait_for_timeout(3_000)

            result = card.locator('[data-slot="tool-fallback-result"]').first
            images = result.locator('img[src^="data:image"]')
            image_count = await images.count()
            dims = []
            for i in range(image_count):
                dims.append(await images.nth(i).evaluate(
                    "el => [el.naturalWidth, el.naturalHeight]"))

            shot = out_dir / f"{label.lower()}_mcp_embedded_resource_image.png"
            # A FIXED clip of the chat column, not an element shot: the card grows
            # with the image it renders, so element shots differ in size between
            # the sides and the composite scales one half to illegibility. The clip
            # also starts right of the sidebar, whose recents list is per-home and
            # would otherwise read as part of the change.
            await page.screenshot(
                path=str(shot),
                clip={"x": 280, "y": 0, "width": 1000, "height": 1000},
            )

            card_text = " ".join((await card.inner_text()).split())
            stored = api_get(session, "/api/mcp/servers/")
            facts = {
                "mcp_servers_registered": len(stored),
                "provider_registered": bool(provider_id),
                "tool_offered_to_model": tool_name in state.offered_tools,
                "provider_completions": state.completions,
                "tool_result_reached_model": bool(state.tool_result_texts),
                "tool_result_text": (state.tool_result_texts or [""])[0][:200],
                "tool_result_text_len": len((state.tool_result_texts or [""])[0]),
                "declared_mimes": list(mimes),
                "result_image_count": image_count,
                "result_image_dims": dims,
                "card_text": card_text,
                "card_char_count": len(card_text),
            }
            return [shot], facts
    finally:
        httpd.shutdown()
        httpd.server_close()
