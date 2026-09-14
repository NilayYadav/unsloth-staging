# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: attach an image, type nothing, press send on an Anthropic connection.

#10455. The composer allows an image on its own, and the turn used to go out with an
empty text block in front of the picture. Anthropic's Messages API rejects that block,
so the chat painted "Generation failed" instead of an answer.

The turn is driven for real, all the way through the photographed Studio's own backend.
The connection's base URL points at a stand-in Messages API held in this process, which
enforces the one documented rule under test -- a text block whose text is empty is a 400,
"messages.N.content.M.text: text content blocks must be non-empty" -- and otherwise
streams a normal answer. Nothing reaches Anthropic and no key is needed, so the pair is
deterministic, but the request is built by the real frontend and translated by the real
`_stream_anthropic`, which is where both halves of the fix live.

The measurement is the content array the stand-in actually received, recorded next to
what the user is left looking at.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import sys
import threading
import time
import uuid
import zlib
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

ANSWER = "A solid red square, and nothing else in the frame."
EMPTY_TEXT_ERROR = "text content blocks must be non-empty"


def _red_png(side: int = 320) -> bytes:
    """A real PNG the reviewer can see in the composer chip and in the sent turn."""
    row = b"\x00" + b"\xd0\x27\x27" * side

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * side))
            + chunk(b"IEND", b""))


def _sse(answer: str) -> bytes:
    """One Anthropic text stream, the shape `_stream_anthropic` translates."""
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_standin", "type": "message", "role": "assistant",
            "model": PROVIDER_MODEL, "content": [],
            "usage": {"input_tokens": 24, "output_tokens": 12}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": answer}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "end_turn"},
                           "usage": {"output_tokens": 12}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return b"".join(
        f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode() for name, payload in events
    )


class _StandIn(BaseHTTPRequestHandler):
    """api.anthropic.com's validation of content blocks, and nothing else."""

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
        record = {"body": body, "status": 200, "error": None}
        type(self).received.append(record)

        for m_i, message in enumerate(body.get("messages") or []):
            content = message.get("content")
            if not isinstance(content, list):
                continue
            if not content:
                return self._reject(record, m_i, None,
                                    f"messages.{m_i}.content: at least one block is required")
            for b_i, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "text" and not block.get("text"):
                    return self._reject(
                        record, m_i, b_i,
                        f"messages.{m_i}.content.{b_i}.text: {EMPTY_TEXT_ERROR}")

        payload = _sse(ANSWER)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _reject(self, record: dict, m_i: int, b_i, message: str):
        record["status"] = 400
        record["error"] = message
        payload = json.dumps({"type": "error", "error": {
            "type": "invalid_request_error", "message": message}}).encode()
        self.send_response(400)
        self.send_header("content-type", "application/json")
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
        "id": thread_id, "title": "Image with no caption", "modelType": "base",
        "modelId": external_model_id, "archived": False,
        "createdAt": CREATED_AT, "updatedAt": CREATED_AT,
    })


def _connections_init_script(external_model_id: str, provider_id: str) -> str:
    """The picker restores the last external selection on boot, which puts the chat on
    the connection without driving the model menu.

    The key map is not decoration: with no key on the connection the adapter refuses the
    turn with "Missing API key for selected connection." and nothing is ever sent, so
    both sides photograph the same client-side refusal. The stand-in never reads it."""
    keys = json.dumps({provider_id: STANDIN_KEY})
    return (
        f"localStorage.setItem({CONNECTIONS_KEY!r}, 'true');"
        f"localStorage.setItem({LAST_EXTERNAL_KEY!r}, {external_model_id!r});"
        f"localStorage.setItem({PROVIDER_KEYS_KEY!r}, {keys!r});"
    )


def _user_content(received: list) -> list | str | None:
    """The content array of the newest user message the stand-in was handed."""
    for record in reversed(received):
        for message in reversed(record["body"].get("messages") or []):
            if message.get("role") == "user":
                return message.get("content")
    return None


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
    image_path = out_dir / "red-square.png"
    image_path.write_bytes(_red_png())

    server, port, received = _start_standin()
    try:
        thread_id = f"uidiff-nocaption-{RUN}"
        provider_id = _create_provider(session, port)
        external_model_id = f"external::{provider_id}::{PROVIDER_MODEL}"
        _seed_thread(session, thread_id, external_model_id)
        base = session.base_url

        auth_script = seed_init_script(
            type("A", (), {"access_token": session.access_token,
                           "refresh_token": session.refresh_token})(),
            [],
        )

        answer_text, toast_text = "", ""
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

            async with page.expect_file_chooser(timeout=60_000) as chooser_info:
                await page.locator('button[aria-label="Tools and attachments"]').first.click()
                await page.get_by_role("menuitem", name="Add photos & files").first.click()
            chooser = await chooser_info.value
            await chooser.set_files([str(image_path)])

            # The chip proves the image is on the turn about to be sent, and the empty
            # textarea proves nothing was typed beside it -- which is the whole scene.
            chip = page.locator('button[aria-label="Image attachment: red-square.png"]').first
            await chip.wait_for(state="visible", timeout=60_000)
            typed = await composer.input_value()
            # The attachments menu is still fading over the composer; a shot taken through
            # it photographs the menu rather than the attached-but-uncaptioned turn.
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(1_500)

            composed = out_dir / f"{label.lower()}_01_image_attached_no_caption.png"
            await sp.screenshot(composed, full_page=False)
            shots.append(composed)

            await page.locator('button[aria-label="Send message"]').first.click()

            toast = page.locator("[data-sonner-toast]")
            bodies = page.locator(".aui-assistant-message-content")
            stop = page.locator('button[aria-label="Stop generating"], button:has-text("Stop")')
            deadline = time.time() + turn_timeout_ms / 1000
            while time.time() < deadline:
                if await toast.count():
                    toast_text = " ".join((await toast.first.inner_text()).split())
                    break
                if await bodies.count() and not await stop.count():
                    text = (await bodies.last.inner_text()).strip()
                    if text:
                        answer_text = text
                        break
                await page.wait_for_timeout(500)
            await page.wait_for_timeout(1_500)

            shot = out_dir / f"{label.lower()}_02_send_outcome.png"
            await sp.screenshot(shot, full_page=False)
            shots.append(shot)

        wire = _user_content(received)
        blocks = [b.get("type") for b in wire if isinstance(b, dict)] if isinstance(wire, list) else []
        empty_text = any(
            isinstance(b, dict) and b.get("type") == "text" and not b.get("text")
            for b in (wire if isinstance(wire, list) else [])
        )
        statuses = [r["status"] for r in received]
        errors = [r["error"] for r in received if r["error"]]
        facts = {
            "thread_id": thread_id,
            "provider_id": provider_id,
            "provider_model": PROVIDER_MODEL,
            "composer_text_typed": typed,
            "requests_received": len(received),
            "provider_statuses": statuses,
            "provider_rejected": 400 in statuses,
            "provider_error": errors[0] if errors else None,
            "wire_block_types": blocks,
            "wire_content": wire,
            "empty_text_block_sent": empty_text,
            "assistant_text": answer_text[:400],
            "assistant_answered": bool(answer_text),
            "toast_text": toast_text[:400],
            "turn_failed": "Generation failed" in toast_text,
        }
        facts["outcome"] = (
            f"rejected: {facts['provider_error']}" if facts["provider_rejected"]
            else f"answered: {answer_text[:120] or '(no text painted)'}"
        )
        return shots, facts
    finally:
        server.shutdown()
        server.server_close()
