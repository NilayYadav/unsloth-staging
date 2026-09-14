# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: what a chat transcript looks like after a message was edited.

Three modes over ONE surface (the rendered transcript plus what the backend holds for
it), because all three defects are "edit a message, reload, look at the thread":

  root_branches    a chat whose FIRST user message was edited stores a second root.
                   Rebuilt as a linear list, both branches render as one conversation
                   and the branch picker is gone.
  edit_metadata    saving an assistant edit rewrites the row without its metadata, so
                   the reload loses the Max Tokens notice, the Continue button and the
                   tok/s line.
  edit_tool_order  saving an assistant edit that used a tool rebuilds
                   [text, tool-call, text] as [merged text, tool-call].

Both sides are seeded through the real chat-history API with byte-identical rows, so
the build under test is the only variable. No model is loaded and nothing is generated:
every message is written by this scene, which is what makes the pair deterministic.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

RUN = uuid.uuid4().hex[:10]
CREATED_AT = 1_755_000_000_000

# The first prompt and its answer, then the edited prompt and ITS answer. Worded so the
# picture reads without the diff: the abandoned pair says so in its own text.
ABANDONED_PROMPT = "Write me a haiku about winter."
ABANDONED_REPLY = "ABANDONED BRANCH -- this answer belongs to the discarded first prompt."
EDITED_PROMPT = "Write me a haiku about summer."
EDITED_REPLY = "SELECTED BRANCH -- this answer belongs to the edited prompt."

TRUNCATED_REPLY = "The three causes are, first, the supply constraint that began in"
INCOMPLETE_NOTICE = "Response hit the Max Tokens limit"

TOOL_PROSE_BEFORE = "Let me search the web for that."
TOOL_PROSE_AFTER = "Search finished -- here is what it returned."

BRANCH_PICKER = ".aui-branch-picker-root"
MESSAGE_ROOT = "[data-role]"


def _put(session: Session, path: str, payload: dict, timeout: int = 120) -> dict:
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


def _thread(session: Session, thread_id: str, title: str) -> dict:
    return api_post(
        session,
        "/api/chat/threads",
        {
            "id": thread_id, "title": title, "modelType": "base", "modelId": "",
            "archived": False, "createdAt": CREATED_AT, "updatedAt": CREATED_AT,
        },
    )


def _message(session: Session, thread_id: str, message: dict) -> dict:
    return _put(
        session,
        f"/api/chat/threads/{thread_id}/messages/{message['id']}?allowGenerationEdit=true",
        message,
    )


def _seed_root_branches(session: Session, thread_id: str) -> None:
    """A(root) -> replyA, then B(root) -> replyB: exactly what editing the first
    message of a chat leaves behind, since assistant-ui appends the edit with the
    edited message's own parentId."""
    _thread(session, thread_id, "Edited first message")
    for index, (mid, parent, role, text) in enumerate((
        (f"a-user-{RUN}", None, "user", ABANDONED_PROMPT),
        (f"a-reply-{RUN}", f"a-user-{RUN}", "assistant", ABANDONED_REPLY),
        (f"b-user-{RUN}", None, "user", EDITED_PROMPT),
        (f"b-reply-{RUN}", f"b-user-{RUN}", "assistant", EDITED_REPLY),
    )):
        _message(session, thread_id, {
            "id": mid, "threadId": thread_id, "parentId": parent, "role": role,
            "content": [{"type": "text", "text": text}],
            "createdAt": CREATED_AT + index,
        })


def _seed_edit_metadata(session: Session, thread_id: str) -> None:
    """A reply that stopped on Max Tokens, with the timing and usage a real turn carries."""
    _thread(session, thread_id, "Reply cut off by Max Tokens")
    _message(session, thread_id, {
        "id": f"m-user-{RUN}", "threadId": thread_id, "parentId": None, "role": "user",
        "content": [{"type": "text", "text": "Explain the three causes in detail."}],
        "createdAt": CREATED_AT,
    })
    _message(session, thread_id, {
        "id": f"m-reply-{RUN}", "threadId": thread_id, "parentId": f"m-user-{RUN}", "role": "assistant",
        "content": [{"type": "text", "text": TRUNCATED_REPLY}],
        "metadata": {
            "incomplete": {"reason": "length"},
            # `fits: false` is what stops Studio auto-continuing the turn on load, which
            # would load a model, start generating, and hide the action bar this scene
            # has to click. It is also one of the fields the save must not drop.
            "contextTruncation": {"fits": False, "dropped": 4, "prompt_target": 3000},
            "timing": {"tokensPerSecond": 42.5, "durationMs": 1200,
                       "completionTokens": 51},
            "contextUsage": {"promptTokens": 900, "completionTokens": 51,
                             "totalTokens": 951, "cachedTokens": 0,
                             "contextLength": 4096, "modelId": ""},
        },
        "createdAt": CREATED_AT + 1,
    })


def _seed_edit_tool_order(session: Session, thread_id: str) -> None:
    """A reply that announces a tool, calls it, then reports the result."""
    _thread(session, thread_id, "Reply that used a tool")
    _message(session, thread_id, {
        "id": f"t-user-{RUN}", "threadId": thread_id, "parentId": None, "role": "user",
        "content": [{"type": "text", "text": "What is the latest on this?"}],
        "createdAt": CREATED_AT,
    })
    _message(session, thread_id, {
        "id": f"t-reply-{RUN}", "threadId": thread_id, "parentId": f"t-user-{RUN}", "role": "assistant",
        "content": [
            {"type": "text", "text": TOOL_PROSE_BEFORE},
            {
                "type": "tool-call", "toolCallId": "call-1", "toolName": "web_search",
                "args": {"query": "latest on this"}, "argsText": '{"query": "latest on this"}',
                "result": {"results": [{"title": "A source", "url": "https://example.com"}]},
            },
            {"type": "text", "text": TOOL_PROSE_AFTER},
        ],
        "createdAt": CREATED_AT + 1,
    })


SEEDERS = {
    "root_branches": _seed_root_branches,
    "edit_metadata": _seed_edit_metadata,
    "edit_tool_order": _seed_edit_tool_order,
}


def _stored(session: Session, thread_id: str) -> list[dict]:
    rows = api_get(session, f"/api/chat/threads/{thread_id}/messages")
    if isinstance(rows, dict):
        rows = rows.get("messages", [])
    return sorted(rows, key=lambda r: r.get("createdAt", 0))


async def _settle(page, expect_text: str | None = None,
                  timeout_ms: int = 60_000) -> None:
    """Composer up, then the transcript actually painted.

    Waiting on the composer alone is not enough: on a cold install the messages arrive
    seconds after it, and the facts read in that window report an EMPTY thread -- which
    on one side only reads as a difference the build caused.
    """
    await page.locator("form:has(textarea) textarea").first.wait_for(
        state="visible", timeout=timeout_ms
    )
    if expect_text:
        await page.get_by_text(expect_text, exact=False).first.wait_for(
            state="visible", timeout=timeout_ms
        )
    await page.wait_for_timeout(2_500)


async def _transcript(page) -> list[str]:
    """The visible message bubbles, in order, as the reader sees them."""
    out: list[str] = []
    for i in range(await page.locator(MESSAGE_ROOT).count()):
        node = page.locator(MESSAGE_ROOT).nth(i)
        if not await node.is_visible():
            continue
        text = " ".join((await node.inner_text()).split())
        if text:
            out.append(text[:300])
    return out


async def _save_edit_in_place(page) -> None:
    """Open Edit response on the one reply and press Save without changing a character."""
    edit = page.get_by_role("button", name="Edit response").first
    await edit.scroll_into_view_if_needed()
    await edit.hover()
    await page.wait_for_timeout(500)
    await edit.click()
    editor = page.locator("textarea").filter(has_not_text="").first
    await page.locator("form:has(textarea) textarea, textarea").first.wait_for(
        state="visible", timeout=30_000
    )
    await page.wait_for_timeout(1_000)
    save = page.get_by_role("button", name="Save", exact=True).first
    await save.wait_for(state="visible", timeout=30_000)
    await save.click()
    await page.wait_for_timeout(3_500)
    del editor


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    mode: str = "root_branches",
    **_: object,
) -> tuple[list[Path], dict]:
    anchor = {
        "root_branches": EDITED_REPLY,
        "edit_metadata": TRUNCATED_REPLY,
        "edit_tool_order": TOOL_PROSE_BEFORE,
    }[mode]
    thread_id = f"uidiff-{mode.replace('_', '-')}-{RUN}"
    SEEDERS[mode](session, thread_id)
    base = session.base_url

    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(),
        [],
    )

    seeded = _stored(session, thread_id)
    shots: list[Path] = []
    facts: dict = {
        "mode": mode,
        "thread_id": thread_id,
        "seeded_parent_ids": [r.get("parentId") for r in seeded],
        "seeded_row_count": len(seeded),
    }

    async with open_chat(base, init_scripts=[auth_script], viewport=(1280, 900),
                         headless=True) as sp:
        page = sp.page
        await page.goto(f"{base}/chat?thread={thread_id}", wait_until="domcontentloaded",
                        timeout=60_000)
        await _settle(page, anchor)

        if mode == "root_branches":
            transcript = await _transcript(page)
            joined = " || ".join(transcript)
            facts.update({
                "rendered_bubbles": len(transcript),
                "transcript": transcript,
                "branch_picker_count": await page.locator(BRANCH_PICKER).count(),
                "abandoned_branch_on_screen": ABANDONED_REPLY[:24] in joined,
                "selected_branch_on_screen": EDITED_REPLY[:22] in joined,
                "both_branches_rendered_as_one_conversation":
                    ABANDONED_REPLY[:24] in joined and EDITED_REPLY[:22] in joined,
            })
            shot = out_dir / f"{label.lower()}_01_transcript_after_reload.png"
            await page.screenshot(path=str(shot))
            shots.append(shot)
        else:
            before_edit = await _transcript(page)
            facts["transcript_before_edit"] = before_edit
            if mode == "edit_metadata":
                facts["notice_before_edit"] = await page.get_by_text(
                    INCOMPLETE_NOTICE).count()
                facts["continue_button_before_edit"] = await page.get_by_role(
                    "button", name="Continue").count()
            shot0 = out_dir / f"{label.lower()}_01_before_edit.png"
            await page.screenshot(path=str(shot0))
            shots.append(shot0)

            await _save_edit_in_place(page)
            facts["stored_after_save"] = _stored(session, thread_id)

            # The reload is the whole point: nothing looks wrong until the thread is
            # rebuilt from what the save actually wrote.
            await page.reload(wait_until="domcontentloaded")
            # Anchored on the REPLY, which both builds still render, never on the notice
            # or the card order -- those are the measurement. Without an anchor a page
            # measured before it paints reports "gone" on both sides, which reads as the
            # fix changing nothing.
            await _settle(page, anchor)
            after = await _transcript(page)
            facts["transcript_after_reload"] = after

            reply_row = next((r for r in _stored(session, thread_id)
                              if r.get("role") == "assistant"), {})
            facts["stored_reply_metadata"] = reply_row.get("metadata")
            facts["stored_reply_metadata_is_null"] = reply_row.get("metadata") is None
            facts["stored_reply_part_types"] = [
                p.get("type") for p in (reply_row.get("content") or [])
                if isinstance(p, dict)
            ]

            if mode == "edit_metadata":
                facts["notice_after_reload"] = await page.get_by_text(
                    INCOMPLETE_NOTICE).count()
                facts["continue_button_after_reload"] = await page.get_by_role(
                    "button", name="Continue").count()
                facts["max_tokens_notice_survived_the_edit"] = (
                    facts["notice_after_reload"] > 0
                )
            if mode == "edit_tool_order":
                joined = " || ".join(after)
                facts["prose_merged_on_screen"] = (
                    f"{TOOL_PROSE_BEFORE} {TOOL_PROSE_AFTER}" in joined
                )
                facts["card_still_between_the_two_sentences"] = (
                    facts["stored_reply_part_types"] == ["text", "tool-call", "text"]
                )

            shot1 = out_dir / f"{label.lower()}_02_after_save_and_reload.png"
            await page.screenshot(path=str(shot1))
            shots.append(shot1)

    return shots, facts
