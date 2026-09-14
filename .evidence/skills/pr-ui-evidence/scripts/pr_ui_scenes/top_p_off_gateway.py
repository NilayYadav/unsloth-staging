# Scene for #10934: Top P set to Off on an OpenAI-compatible gateway that forbids temperature + top_p together.
"""Scene: chat with Claude through a LiteLLM/Bedrock-style OpenAI-compatible connection
with Run settings -> Top P at Off.

The connection's base URL points at a stand-in /v1/chat/completions held by the scene. It
enforces the one rule under test (a body carrying both `temperature` and `top_p` is a 400
with Bedrock's message) and otherwise streams a normal answer. The request is built by the
real frontend and forwarded by the real backend route of the photographed Studio.
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
STANDIN_KEY = "sk-litellm-standin-not-a-real-key"

PROMPT = "Say hello in one short sentence."
ANSWER = "Hello from Claude Sonnet 4.6 through the gateway."
BEDROCK_ERROR = (
    'litellm.BadRequestError: BedrockException - {"message":"The model returned the '
    "following errors: `temperature` and `top_p` cannot both be specified for this "
    'model. Please use only one."}'
)


def _sse(answer: str) -> bytes:
    chunks = [
        {"id": "chatcmpl-standin", "object": "chat.completion.chunk", "model": PROVIDER_MODEL,
         "choices": [{"index": 0, "delta": {"role": "assistant", "content": answer}}]},
        {"id": "chatcmpl-standin", "object": "chat.completion.chunk", "model": PROVIDER_MODEL,
         "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    return b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"


class _StandIn(BaseHTTPRequestHandler):
    received: list = []

    def log_message(self, *_args):
        pass

    def _send(self, status: int, ctype: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        body = json.dumps({"object": "list", "data": [{"id": PROVIDER_MODEL, "object": "model"}]})
        self._send(200, "application/json", body.encode())

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:  # noqa: BLE001
            body = {}
        record = {"path": self.path, "body": body, "status": 200, "error": None}
        type(self).received.append(record)
        if "temperature" in body and "top_p" in body:
            record["status"] = 400
            record["error"] = BEDROCK_ERROR
            payload = json.dumps({"error": {"message": BEDROCK_ERROR, "type": None,
                                            "param": None, "code": "400"}}).encode()
            return self._send(400, "application/json", payload)
        if body.get("stream"):
            return self._send(200, "text/event-stream", _sse(ANSWER))
        payload = json.dumps({"id": "chatcmpl-standin", "object": "chat.completion",
                              "model": PROVIDER_MODEL,
                              "choices": [{"index": 0, "finish_reason": "stop",
                                           "message": {"role": "assistant", "content": ANSWER}}]})
        return self._send(200, "application/json", payload.encode())


def _start_standin() -> tuple[ThreadingHTTPServer, int, list]:
    received: list = []
    handler = type("_StandInRun", (_StandIn,), {"received": received})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1], received


def _create_provider(session: Session, port: int) -> str:
    made = api_post(session, "/api/providers/", {
        "provider_type": "custom",
        "display_name": "LiteLLM gateway",
        "base_url": f"http://127.0.0.1:{port}/v1",
        "models": [PROVIDER_MODEL],
        "available_models": [PROVIDER_MODEL],
        "encrypted_api_key": "",
    })
    return made["id"]


def _seed_thread(session: Session, thread_id: str, external_model_id: str) -> None:
    api_post(session, "/api/chat/threads", {
        "id": thread_id, "title": "Claude via LiteLLM", "modelType": "base",
        "modelId": external_model_id, "archived": False,
        "createdAt": CREATED_AT, "updatedAt": CREATED_AT,
    })


def _connections_init_script(external_model_id: str, provider_id: str) -> str:
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
        thread_id = f"uidiff-topp-off-{RUN}"
        provider_id = _create_provider(session, port)
        external_model_id = f"external::{provider_id}::{PROVIDER_MODEL}"
        _seed_thread(session, thread_id, external_model_id)
        base = session.base_url
        auth_script = seed_init_script(
            type("A", (), {"access_token": session.access_token,
                           "refresh_token": session.refresh_token})(),
            [],
        )

        answer_text, toast_text, alert_text = "", "", ""
        async with open_chat(
            base,
            init_scripts=[auth_script, _connections_init_script(external_model_id, provider_id)],
            viewport=(1440, 900),
            headless=True,
        ) as sp:
            page = sp.page
            await page.goto(f"{base}/chat?thread={thread_id}",
                            wait_until="domcontentloaded", timeout=60_000)
            composer = page.locator("form:has(textarea) textarea").first
            await composer.wait_for(state="visible", timeout=60_000)
            await page.wait_for_timeout(3_000)

            opener = page.locator('button[aria-label="Open run settings"]').first
            if await opener.count():
                await opener.click()
            top_p_input = page.locator('input[aria-label="Top P"]').first
            await top_p_input.wait_for(state="visible", timeout=30_000)
            top_p_row = page.locator("div.space-y-3\\.5").filter(has=top_p_input).first
            thumb = top_p_row.locator('[role="slider"]').first
            await thumb.focus()
            await page.keyboard.press("End")
            await page.wait_for_timeout(500)
            top_p_display = await top_p_input.input_value()
            if top_p_display != "Off":
                raise RuntimeError(f"Top P control reads {top_p_display!r}, expected 'Off'")

            await composer.fill(PROMPT)
            await page.locator('button[aria-label="Send message"]').first.click()

            toast = page.locator("[data-sonner-toast]")
            bodies = page.locator(".aui-assistant-message-content")
            stop = page.locator('button[aria-label="Stop generating"]')
            deadline = time.time() + turn_timeout_ms / 1000
            while time.time() < deadline:
                if await toast.count():
                    toast_text = " ".join((await toast.first.inner_text()).split())
                if await bodies.count() and not await stop.count():
                    text = " ".join((await bodies.last.inner_text()).split())
                    if text:
                        answer_text = text
                if (toast_text or answer_text) and received:
                    break
                await page.wait_for_timeout(500)
            await page.wait_for_timeout(2_000)
            alerts = page.locator('[role="alert"]')
            if await alerts.count():
                alert_text = " ".join((await alerts.last.inner_text()).split())
            if not toast_text and await toast.count():
                toast_text = " ".join((await toast.first.inner_text()).split())

            shot = out_dir / f"{label.lower()}_01_top_p_off_send.png"
            await sp.screenshot(shot, full_page=False)
            shots.append(shot)

        chats = [r for r in received if r["path"].endswith("/chat/completions")]
        last = chats[-1]["body"] if chats else {}
        statuses = [r["status"] for r in chats]
        errors = [r["error"] for r in chats if r["error"]]
        visible = " ".join(t for t in (answer_text, toast_text, alert_text) if t)
        facts = {
            "provider_type": "custom",
            "provider_model": PROVIDER_MODEL,
            "top_p_control_display": top_p_display,
            "chat_requests_received": len(chats),
            "provider_statuses": statuses,
            "temperature_sent": last.get("temperature"),
            "top_p_in_body": "top_p" in last,
            "top_p_sent": last.get("top_p"),
            "provider_error": errors[0] if errors else None,
            "assistant_text": answer_text[:400],
            "assistant_answered": ANSWER in answer_text,
            "toast_text": toast_text[:400],
            "alert_text": alert_text[:400],
            "error_visible": "cannot both be specified" in visible,
        }
        return shots, facts
    finally:
        server.shutdown()
        server.server_close()
