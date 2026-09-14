"""Scene: an Anthropic reply that fills the model's context window mid-sentence.

#10557. Anthropic ends such a turn with `stop_reason: "model_context_window_exceeded"`,
which Studio's map did not know, so it fell through to "stop" and the half-written answer
was painted as a finished one -- no notice, no Continue button.

The turn is driven for real through the photographed Studio's own backend. The
connection's base URL points at a stand-in Messages API held in this process, which
streams a sentence that stops mid-word and then reports exactly that stop reason.
Nothing reaches Anthropic and no key is needed, but the request is built by the real
frontend and translated by the real `_stream_anthropic`, which is where the fix lives.

The measurement is what the user is left looking at underneath the cut-off sentence.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

RUN = uuid.uuid4().hex[:10]
CREATED_AT = 1_755_000_000_000

PROVIDER_MODEL = "claude-sonnet-4-6"
CONNECTIONS_KEY = "unsloth_chat_connections_enabled"
LAST_EXTERNAL_KEY = "unsloth_chat_last_external_checkpoint"
PROVIDER_KEYS_KEY = "unsloth_chat_external_provider_keys"
STANDIN_KEY = "sk-ant-standin-not-a-real-key"

STOP_REASON = "model_context_window_exceeded"
# Ends mid-word on purpose: the whole point is that the user can see the answer was cut,
# while the UI underneath it used to say nothing at all.
PARTIAL = (
    "The three phases of the migration are, first, the dual-write window, during which "
    "both stores accept writes and the reconciler compares them nightly; second, the "
    "read cutover, where traffic shifts one shard at a time and each shard is held for a "
    "full business day before the next; and third, the decommis"
)


def _sse() -> bytes:
    """One Anthropic stream that ends on the window, the shape `_stream_anthropic`
    translates."""
    start = ("message_start", {"type": "message_start", "message": {
        "id": "msg_standin", "type": "message", "role": "assistant",
        "model": PROVIDER_MODEL, "content": [],
        "usage": {"input_tokens": 190_000, "output_tokens": 0}}})
    body = [
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": PARTIAL}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ]
    events = [start, *body,
              ("message_delta", {"type": "message_delta",
                                 "delta": {"stop_reason": STOP_REASON},
                                 "usage": {"output_tokens": 64}}),
              ("message_stop", {"type": "message_stop"})]
    return b"".join(
        f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode() for name, payload in events
    )


class _StandIn(BaseHTTPRequestHandler):
    """A Messages API whose only behaviour is running out of window."""

    received: list = []

    def log_message(self, *_args):  # noqa: D102 -- the driver owns the log
        pass

    def do_GET(self):  # noqa: N802 -- the model list some clients probe on connect
        body = json.dumps({"data": [{"id": PROVIDER_MODEL, "type": "model"}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:  # noqa: BLE001 -- an unreadable body is the finding
            body = {}
        type(self).received.append({"body": body})

        payload = _sse()
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _start_standin() -> tuple[ThreadingHTTPServer, int, list]:
    received: list = []
    handler = type("_StandInRun", (_StandIn,), {"received": received})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1], received


def _create_provider(session: Session, port: int) -> str:
    """Server-side state, not a localStorage entry: the roster the app fetches on boot
    overwrites a seeded browser key."""
    made = api_post(session, "/api/providers/", {
        "provider_type": "anthropic",
        "display_name": "Anthropic stand-in",
        "base_url": f"http://127.0.0.1:{port}/v1",
        "models": [PROVIDER_MODEL],
        "available_models": [PROVIDER_MODEL],
        "encrypted_api_key": "",
    })
    return made["id"]


def _seed_thread(session: Session, thread_id: str, external_model_id: str) -> None:
    api_post(session, "/api/chat/threads", {
        "id": thread_id, "title": "Long migration thread", "modelType": "base",
        "modelId": external_model_id, "archived": False,
        "createdAt": CREATED_AT, "updatedAt": CREATED_AT,
    })


def _connections_init_script(external_model_id: str, provider_id: str) -> str:
    """The picker restores the last external selection on boot, which puts the chat on
    the connection without driving the model menu. The key map is not decoration: with no
    key the adapter refuses the turn before sending and both sides photograph the same
    client-side refusal. The stand-in never reads it."""
    keys = json.dumps({provider_id: STANDIN_KEY})
    return (
        f"localStorage.setItem({CONNECTIONS_KEY!r}, 'true');"
        f"localStorage.setItem({LAST_EXTERNAL_KEY!r}, {external_model_id!r});"
        f"localStorage.setItem({PROVIDER_KEYS_KEY!r}, {keys!r});"
    )


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    *,
    turn_timeout_ms: int = 120_000,
    **_: object,
) -> tuple[list[Path], dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    shots: list[Path] = []

    server, port, received = _start_standin()
    try:
        thread_id = f"uidiff-ctxwindow-{RUN}"
        provider_id = _create_provider(session, port)
        external_model_id = f"external::{provider_id}::{PROVIDER_MODEL}"
        _seed_thread(session, thread_id, external_model_id)
        base = session.base_url

        auth_script = seed_init_script(
            type("A", (), {"access_token": session.access_token,
                           "refresh_token": session.refresh_token})(),
            [],
        )

        answer_text, toast_text, bar_text = "", "", ""
        bar_present = continue_present = False
        async with open_chat(
            base,
            init_scripts=[auth_script,
                          _connections_init_script(external_model_id, provider_id)],
            viewport=(1280, 900),
            headless=True,
        ) as sp:
            page = sp.page
            await page.goto(f"{base}/chat?thread={thread_id}",
                            wait_until="domcontentloaded", timeout=60_000)
            composer = page.locator("form:has(textarea) textarea").first
            await composer.wait_for(state="visible", timeout=60_000)
            await page.wait_for_timeout(3_000)

            await composer.fill("Summarise the migration plan in full.")
            await page.locator('button[aria-label="Send message"]').first.click()

            toast = page.locator("[data-sonner-toast]")
            bodies = page.locator(".aui-assistant-message-content")
            stop = page.locator('button[aria-label="Stop generating"], button:has-text("Stop")')
            deadline = time.time() + turn_timeout_ms / 1000
            while time.time() < deadline:
                if await toast.count():
                    toast_text = " ".join((await toast.first.inner_text()).split())
                if await bodies.count() and not await stop.count():
                    text = (await bodies.last.inner_text()).strip()
                    if text:
                        answer_text = text
                        break
                await page.wait_for_timeout(500)
            # The bar renders after the stream settles; give it room rather than racing it.
            await page.wait_for_timeout(4_000)

            bar = page.locator(".aui-continue-bar")
            bar_present = await bar.count() > 0
            if bar_present:
                bar_text = " ".join((await bar.first.inner_text()).split())
            # Scoped to the bar: a bare "Continue" match anywhere on the page would read
            # true on the side that has no bar and erase the delta this scene exists for.
            continue_present = (
                await bar.first.locator('button:has-text("Continue")').count() > 0
                if bar_present
                else False
            )

            shot = out_dir / f"{label.lower()}_01_cut_off_reply.png"
            await sp.screenshot(shot, full_page=False)
            shots.append(shot)


        facts = {
            "thread_id": thread_id,
            "provider_id": provider_id,
            "provider_model": PROVIDER_MODEL,
            "stop_reason_sent": STOP_REASON,
            "requests_received": len(received),
            "assistant_text": answer_text[:400],
            "assistant_ends_mid_word": answer_text.rstrip().endswith("decommis"),
            "truncation_bar_present": bar_present,
            "truncation_bar_text": bar_text[:200],
            "continue_button_present": continue_present,
            "toast_text": toast_text[:200],
        }
        facts["outcome"] = (
            f"cut reported: {bar_text[:120]}" if bar_present
            else "cut NOT reported: the half answer is painted as a finished one"
        )
        return shots, facts
    finally:
        server.shutdown()
        server.server_close()
