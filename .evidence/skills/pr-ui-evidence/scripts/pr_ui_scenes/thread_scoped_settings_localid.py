# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: a chat created in the app, whose row id keeps its `__LOCALID_` prefix.

assistant-ui mints `__LOCALID_<id>` for a thread before its first send, the thread list
adapter hands that same string back as the remoteId, and the backend stores it as the
row's primary key. Two places read the prefix as "this chat has no row yet", which is
true only until the first send, so both kept firing for the life of every chat Studio
creates.

Both sides are seeded through the real chat-history API with byte-identical rows, so the
frontend build is the only variable. The seeding uses ids of the shape the app actually
mints, which is the whole point: seeded uuids never reach the branch.
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

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

# The prefix is the subject, so it is spelled out here rather than imported. The suffix is
# minted once per run and shared by both sides: a home is reused across runs, and a deleted
# id cannot be re-created (the backend answers 410 for a tombstoned thread), so fixed ids
# would either photograph the previous run's snapshot or fail to seed at all.
RUN = uuid.uuid4().hex[:10]
CHAT_A = f"__LOCALID_pr9639a{RUN}"
CHAT_B = f"__LOCALID_pr9639b{RUN}"
FORKS = (f"__LOCALID_pr9639f1{RUN}", f"__LOCALID_pr9639f2{RUN}")

TITLE_A = "Chat A app-created"
TITLE_B = "Chat B app-created"
USER_MSG = "pr9639-a-user"
REPLY_MSG = "pr9639-a-reply"
CREATED_AT = 1_755_000_000_000

SEARCH_PILL = 'button[data-pill-label="Search"]:visible'
PERMISSION_PILL = 'button[aria-label="Permission level for tool calls"]:visible'
FORK_BADGE = 'span[title*="from this message"]'


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


def _thread(session: Session, thread_id: str, title: str, **extra) -> dict:
    return api_post(
        session,
        "/api/chat/threads",
        {
            "id": thread_id,
            "title": title,
            "modelType": "base",
            "modelId": "",
            "archived": False,
            "createdAt": CREATED_AT,
            "updatedAt": CREATED_AT,
            **extra,
        },
    )


def _seed(session: Session) -> None:
    _thread(session, CHAT_A, TITLE_A)
    _thread(session, CHAT_B, TITLE_B)
    # A ends on an assistant turn: the fork badge lives in a message action bar, and the
    # bar autohides on everything but the newest reply.
    _put(
        session,
        f"/api/chat/threads/{CHAT_A}/messages/{USER_MSG}",
        {
            "id": USER_MSG, "threadId": CHAT_A, "role": "user",
            "content": [{"type": "text", "text": "what does this chat remember?"}],
            "createdAt": CREATED_AT,
        },
    )
    _put(
        session,
        f"/api/chat/threads/{CHAT_A}/messages/{REPLY_MSG}",
        {
            "id": REPLY_MSG, "threadId": CHAT_A, "role": "assistant",
            "content": [{"type": "text", "text": "Its own pills, and the forks made from it."}],
            "createdAt": CREATED_AT + 1,
        },
    )
    # Two real forks of that reply, so the badge has something true to show.
    for index, fork_id in enumerate(FORKS):
        _thread(
            session, fork_id, f"Fork {index + 1} of A",
            forkedFromThreadId=CHAT_A, forkedFromMessageId=REPLY_MSG,
        )


async def _pill_active(page, selector: str) -> str | None:
    return await page.locator(selector).first.get_attribute("data-active")


async def _settle(page, timeout_ms: int = 60_000) -> None:
    """Composer up, then long enough for the snapshot GET and the debounced write."""
    await page.locator(SEARCH_PILL).first.wait_for(state="visible", timeout=timeout_ms)
    await page.wait_for_timeout(1_800)


async def _globals(page) -> dict:
    return await page.evaluate(
        """(keys) => Object.fromEntries(keys.map((k) => [k, localStorage.getItem(k)]))""",
        ["unsloth_chat_tools_enabled", "unsloth_chat_code_tools_enabled",
         "unsloth_chat_permission_mode"],
    )


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph what an app-created chat's own settings and fork badge do."""
    _seed(session)
    base = session.base_url

    auth_script = seed_init_script(
        type(
            "A",
            (),
            {"access_token": session.access_token, "refresh_token": session.refresh_token},
        )(),
        [],
    )

    shots: list[Path] = []
    async with open_chat(
        base, init_scripts=[auth_script], viewport=(1280, 900), headless=True,
    ) as sp:
        page = sp.page

        # Land the installation default on a known value from a chat with no row, which
        # is the one place every build agrees an edit belongs.
        await page.goto(f"{base}/chat", wait_until="domcontentloaded", timeout=60_000)
        await _settle(page)
        # Clear the installation slots outright rather than toggling them back: a reused home
        # carries whatever the last run left, and "unchanged" is one of the facts being read.
        await page.evaluate(
            """(keys) => { for (const k of keys) localStorage.removeItem(k); }""",
            ["unsloth_chat_tools_enabled", "unsloth_chat_code_tools_enabled",
             "unsloth_chat_permission_mode", "unsloth_chat_confirm_tool_calls"],
        )
        await page.reload(wait_until="domcontentloaded")
        await _settle(page)
        globals_before = await _globals(page)

        # Chat A: turn Search on INSIDE the chat. This is the edit the whole PR is about.
        await page.goto(f"{base}/chat?thread={CHAT_A}", wait_until="domcontentloaded",
                        timeout=60_000)
        await _settle(page)
        a_pill_on_open = await _pill_active(page, SEARCH_PILL)
        await page.locator(SEARCH_PILL).first.click()
        await page.wait_for_timeout(2_500)
        a_pill_after_edit = await _pill_active(page, SEARCH_PILL)
        globals_after = await _globals(page)
        stored_a = api_get(session, f"/api/chat/threads/{CHAT_A}").get("settings")
        shot_a = out_dir / f"{label.lower()}_01_chat_a_search_on.png"
        await page.screenshot(path=str(shot_a))
        shots.append(shot_a)

        # Chat B: never edited. Whatever it shows, it inherited.
        await page.goto(f"{base}/chat?thread={CHAT_B}", wait_until="domcontentloaded",
                        timeout=60_000)
        await _settle(page)
        b_pill = await _pill_active(page, SEARCH_PILL)
        shot_b = out_dir / f"{label.lower()}_02_chat_b_untouched.png"
        await page.screenshot(path=str(shot_b))
        shots.append(shot_b)

        # Back to A for the fork badge, which needs the reply's action bar on screen.
        await page.goto(f"{base}/chat?thread={CHAT_A}", wait_until="domcontentloaded",
                        timeout=60_000)
        await _settle(page)
        reply = page.get_by_text("Its own pills, and the forks made from it.").first
        await reply.wait_for(state="visible", timeout=60_000)
        await reply.hover()
        await page.wait_for_timeout(2_000)
        badge = page.locator(FORK_BADGE).first
        badge_count = await page.locator(FORK_BADGE).count()
        badge_text = (await badge.inner_text()).strip() if badge_count else None
        badge_title = (await badge.get_attribute("title")) if badge_count else None
        shot_f = out_dir / f"{label.lower()}_03_fork_badge.png"
        await page.screenshot(path=str(shot_f))
        shots.append(shot_f)

    # The backend's own answer, identical on both sides: only the UI reading it differs.
    forks_api = api_get(session, f"/api/chat/threads/{CHAT_A}/forks")

    facts = {
        "chat_a_id": CHAT_A,
        "chat_b_id": CHAT_B,
        "globals_before_edit": globals_before,
        "globals_after_edit": globals_after,
        "installation_default_moved": globals_before != globals_after,
        "chat_a_pill_on_open": a_pill_on_open,
        "chat_a_pill_after_edit": a_pill_after_edit,
        "chat_a_stored_settings": stored_a,
        "chat_a_stored_tools_enabled": (stored_a or {}).get("toolsEnabled"),
        "chat_b_search_pill_active": b_pill,
        "chat_b_inherited_the_edit": b_pill == "true",
        "fork_badge_present": bool(badge_count),
        "fork_badge_text": badge_text,
        "fork_badge_title": badge_title,
        "forks_api_counts": forks_api.get("counts"),
    }
    return shots, facts
