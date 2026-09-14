# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: reopening a thread whose model answered with a tool argument that is not a string.

Both sides are seeded with the SAME stored conversation through the real chat-history
API, so the persisted bytes are identical and the frontend build is the only variable.
Reopening is the path on purpose: the argument is saved with the message, so a thread
that killed Studio once kills it again on every visit until the card stops calling a
string method on whatever the model sent.

A merge-base build reads `args.code` and calls `.split("\n")` on it. The throw has no
boundary above it -- the nearest catcher is the router's -- so the whole application is
replaced with "Something went wrong!".
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

THREAD_ID = "pr9641-tool-arg-thread"
THREAD_TITLE = "Tool arg coercion evidence"
CREATED_AT = 1_755_000_000_000

# 42 is what a local model emits when it decides `code` is a number. The card asks it
# for .split, .slice, .replace and .trim, none of which a number has.
NUMERIC = 42

# One card per crash site the PR touches: `python` is the one the PR opened against,
# `code_execution` is the one it left behind and the review added.
CALLS = (
    ("python", "call_pr9641_python", {"code": NUMERIC}),
    ("code_execution", "call_pr9641_codeexec", {"kind": "bash", "command": NUMERIC}),
)


def _put(session: Session, path: str, payload: dict, timeout: int = 120) -> dict:
    """Authenticated PUT. The message store has no POST route, and seeding through the
    real endpoint is what keeps both sides reading identical stored bytes."""
    req = urllib.request.Request(
        f"{session.base_url}{path}",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {session.access_token}",
        },
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _seed(session: Session) -> list[dict]:
    api_post(
        session,
        "/api/chat/threads",
        {
            "id": THREAD_ID,
            "title": THREAD_TITLE,
            "modelType": "base",
            "modelId": "",
            "createdAt": CREATED_AT,
            "updatedAt": CREATED_AT,
        },
    )
    saved = []
    for index, (tool_name, call_id, args) in enumerate(CALLS):
        message_id = f"pr9641-{tool_name}-message"
        saved.append(
            _put(
                session,
                f"/api/chat/threads/{THREAD_ID}/messages/{message_id}",
                {
                    "id": message_id,
                    "threadId": THREAD_ID,
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool-call",
                            "toolCallId": call_id,
                            "toolName": tool_name,
                            "argsText": json.dumps(args),
                            "args": args,
                            "result": "ok",
                        }
                    ],
                    "createdAt": CREATED_AT + index,
                },
            )
        )
    return saved


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph what a stored non-string tool argument does to the thread."""
    saved = _seed(session)
    stored = api_get(session, f"/api/chat/threads/{THREAD_ID}/messages")

    auth_script = seed_init_script(
        type(
            "A",
            (),
            {"access_token": session.access_token, "refresh_token": session.refresh_token},
        )(),
        [],
    )

    async with open_chat(
        session.base_url,
        init_scripts=[auth_script],
        viewport=(1280, 900),
        headless=True,
    ) as sp:
        page = sp.page
        # Collected rather than asserted: the render throw is the subject of the shot,
        # so the page erroring is a result to record, not a scene failure.
        page_errors: list[str] = []
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        entry = page.get_by_text(THREAD_TITLE, exact=True).first
        await entry.wait_for(state="visible", timeout=90_000)
        await entry.click()
        # Long enough for the cards to mount, or for the router boundary to swap the
        # whole application out. Neither side is waited on by a locator: on the crashing
        # side the locator that would prove success is exactly what never appears.
        await page.wait_for_timeout(6_000)

        body_text = " ".join((await page.locator("body").inner_text()).split())
        crashed = "Something went wrong" in body_text
        cards = page.locator('[data-slot="tool-fallback-root"]')
        card_count = await cards.count()

        card_texts: dict[str, str] = {}
        for index in range(card_count):
            text = " ".join((await cards.nth(index).inner_text()).split())
            card_texts[f"card_{index}"] = text

        shot = out_dir / f"{label.lower()}_tool_arg_non_string.png"
        await page.screenshot(path=str(shot))

        joined = " ".join(card_texts.values())
        facts = {
            "seeded_message_ids": [m.get("id") for m in saved],
            "stored_message_count": len(stored.get("messages", [])),
            "tool_calls_seeded": [name for name, _, _ in CALLS],
            "numeric_argument": NUMERIC,
            "app_crashed": crashed,
            "crash_banner": "Something went wrong" if crashed else None,
            "tool_cards_rendered": card_count,
            "card_texts": card_texts,
            "python_card_shows_argument": "42" in joined,
            "render_errors": page_errors[:4],
            "render_error_count": len(page_errors),
            "body_char_count": len(body_text),
        }
        return [shot], facts
