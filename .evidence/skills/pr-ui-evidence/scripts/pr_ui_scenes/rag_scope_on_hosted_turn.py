# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: the rag_scope the built bundle actually puts on a hosted-model request.

The defect is a request field, so the evidence is the request. The chat is driven for
real -- a thread whose model is an external connection, Docs on, a prompt typed into the
composer and sent -- and the POST to /v1/chat/completions is intercepted in the browser
and answered with a canned stream. Nothing reaches a provider and no model is loaded;
what is read is the body the frontend built.

`context_length` is filled from `runtime.loadedContextLength ?? params.maxSeqLength` on
the base build, so the leak shows through `maxSeqLength` alone: a resident GGUF makes it
worse but is not needed to expose it, and needing no weights is what keeps the pair
deterministic.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

RUN = uuid.uuid4().hex[:10]
CREATED_AT = 1_755_000_000_000

PROVIDER_MODEL = "gpt-5-hosted-stand-in"
CONNECTIONS_KEY = "unsloth_chat_connections_enabled"
LAST_EXTERNAL_KEY = "unsloth_chat_last_external_checkpoint"


def _create_provider(session: Session) -> str:
    """The connection is server-side state, not a localStorage entry: seeding the browser
    key is overwritten by the roster the app fetches on boot."""
    made = api_post(session, "/api/providers/", {
        "provider_type": "openai",
        "display_name": "Hosted stand-in",
        "base_url": "http://127.0.0.1:9/v1",
        "models": [PROVIDER_MODEL],
        "available_models": [PROVIDER_MODEL],
        "encrypted_api_key": "",
    })
    return made["id"]

# A stream that ends immediately: the scene only needs the request, and a body the
# frontend can parse keeps it from painting an error over the shot.
CANNED_STREAM = (
    'data: {"id":"x","object":"chat.completion.chunk","choices":'
    '[{"index":0,"delta":{"content":"Read from the attached document."},'
    '"finish_reason":null}]}\n\n'
    'data: {"id":"x","object":"chat.completion.chunk","choices":'
    '[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)


def _seed_thread(session: Session, thread_id: str, external_model_id: str) -> None:
    api_post(session, "/api/chat/threads", {
        "id": thread_id, "title": "Hosted turn with a document", "modelType": "base",
        "modelId": external_model_id, "archived": False,
        "createdAt": CREATED_AT, "updatedAt": CREATED_AT,
        # Docs on for THIS chat, which is what puts rag_scope in the body at all.
        "settings": {"ragEnabled": True},
    })


def _connections_init_script(external_model_id: str) -> str:
    """The picker restores the last external selection from here on boot, which is what
    puts the chat on the hosted connection without driving the model menu."""
    return (
        f"localStorage.setItem({CONNECTIONS_KEY!r}, 'true');"
        f"localStorage.setItem({LAST_EXTERNAL_KEY!r}, {external_model_id!r});"
    )


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    **_: object,
) -> tuple[list[Path], dict]:
    thread_id = f"uidiff-ragscope-{RUN}"
    provider_id = _create_provider(session)
    external_model_id = f"external::{provider_id}::{PROVIDER_MODEL}"
    _seed_thread(session, thread_id, external_model_id)
    base = session.base_url

    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(),
        [],
    )

    captured: list[dict] = []
    shots: list[Path] = []

    async with open_chat(base, init_scripts=[auth_script, _connections_init_script(external_model_id)],
                         viewport=(1280, 900), headless=True) as sp:
        page = sp.page

        async def intercept(route):
            request = route.request
            try:
                captured.append(json.loads(request.post_data or "{}"))
            except Exception:  # noqa: BLE001 -- a body we cannot read is the finding
                captured.append({"__unparsable__": request.post_data})
            await route.fulfill(
                status=200,
                headers={"Content-Type": "text/event-stream"},
                body=CANNED_STREAM,
            )

        await page.route("**/v1/chat/completions", intercept)
        await page.goto(f"{base}/chat?thread={thread_id}", wait_until="domcontentloaded",
                        timeout=60_000)
        composer = page.locator("form:has(textarea) textarea").first
        await composer.wait_for(state="visible", timeout=60_000)
        await page.wait_for_timeout(3_000)

        await composer.click()
        await composer.fill("Summarise the attached contract and list every deadline.")
        await page.wait_for_timeout(600)
        await composer.press("Enter")
        # The send is what builds the body; give the adapter room to resolve the
        # project probe and the thread claim before it posts.
        for _ in range(40):
            if captured:
                break
            await page.wait_for_timeout(1_000)
        # Wait for the canned reply to paint, on both sides, or the slower side is
        # photographed mid-send and the pair differs by render timing rather than by
        # the field under review.
        await page.get_by_text("Read from the attached document.").first.wait_for(
            state="visible", timeout=60_000)
        await page.wait_for_timeout(1_500)

        # The field under review is in the request, not on screen. Draw the body that
        # was actually intercepted on this side so the composite shows it; this is the
        # captured payload verbatim, not a re-derivation.
        await page.evaluate(
            """([label, scope]) => {
              const panel = document.createElement('div');
              panel.style.cssText = 'position:fixed;left:24px;bottom:120px;z-index:2147483647;'
                + 'max-width:620px;padding:14px 18px;border-radius:12px;'
                + 'background:#0b1020;color:#e6edf3;font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;'
                + 'box-shadow:0 10px 30px rgba(0,0,0,.35);white-space:pre;';
              panel.textContent = label + ' rag_scope on the POST to /v1/chat/completions\\n\\n'
                + JSON.stringify(scope, null, 2);
              document.body.appendChild(panel);
            }""",
            [label.upper(), (captured[0] if captured else {}).get("rag_scope") or {}],
        )
        await page.wait_for_timeout(300)

        shot = out_dir / f"{label.lower()}_01_hosted_turn_sent.png"
        await page.screenshot(path=str(shot))
        shots.append(shot)

    body = captured[0] if captured else {}
    scope = body.get("rag_scope") or {}
    facts = {
        "thread_id": thread_id,
        "requests_captured": len(captured),
        "request_model": body.get("model"),
        "provider_id": provider_id,
        "request_is_external": body.get("model") == PROVIDER_MODEL,
        "rag_scope_present": "rag_scope" in body,
        "rag_scope_keys": sorted(scope),
        "rag_scope_context_length": scope.get("context_length"),
        "context_length_sent": "context_length" in scope,
        "rag_scope": scope,
    }
    return shots, facts
