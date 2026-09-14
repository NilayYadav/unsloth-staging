# Scene: which reply a training export holds after the branch picker moved back to an earlier one.
"""A prompt with two sibling replies (the second from Regenerate), seeded byte-identically
through the chat-history API. The scene clicks the branch picker's Previous so the FIRST
reply is on screen, then downloads ShareGPT JSONL and Training JSONL from the composer's
Export chat menu and pins what each file holds next to the transcript. No model is loaded.
"""

from __future__ import annotations

import html
import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve()
WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", HERE.parents[2]))
for p in (WORKSPACE, WORKSPACE / "scripts", HERE.parents[1]):
    sys.path.insert(0, str(p))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

RUN = uuid.uuid4().hex[:10]
CREATED_AT = 1_755_000_000_000

PROMPT = "Name one fruit."
FIRST_REPLY = "FIRST REPLY: Apples. This is the reply picked with the branch picker."
RETRY_REPLY = "REGENERATED REPLY: Bananas. The branch picker moved away from this one."

PICKER = ".aui-branch-picker-root"


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


def _seed(session: Session, thread_id: str) -> None:
    api_post(session, "/api/chat/threads", {
        "id": thread_id, "title": "Regenerated reply", "modelType": "base", "modelId": "",
        "archived": False, "createdAt": CREATED_AT, "updatedAt": CREATED_AT,
    })
    rows = (
        (f"u1-{RUN}", None, "user", PROMPT),
        (f"a1-{RUN}", f"u1-{RUN}", "assistant", FIRST_REPLY),
        (f"a2-{RUN}", f"u1-{RUN}", "assistant", RETRY_REPLY),
    )
    for index, (mid, parent, role, text) in enumerate(rows):
        _put(session, f"/api/chat/threads/{thread_id}/messages/{mid}?allowGenerationEdit=true", {
            "id": mid, "threadId": thread_id, "parentId": parent, "role": role,
            "content": [{"type": "text", "text": text}],
            "createdAt": CREATED_AT + index,
        })


def _stored(session: Session, thread_id: str) -> list[dict]:
    rows = api_get(session, f"/api/chat/threads/{thread_id}/messages")
    if isinstance(rows, dict):
        rows = rows.get("messages", [])
    return rows


def _which(text: str) -> str:
    if FIRST_REPLY in text:
        return "first"
    if RETRY_REPLY in text:
        return "regenerated"
    return "other"


async def _picker_state(page) -> str:
    return " ".join((await page.locator(f"{PICKER} .aui-branch-picker-state").last.inner_text()).split())


async def _export(page, item_label: str, dest: Path) -> str:
    await page.keyboard.press("Escape")
    await page.get_by_role("button", name="Tools and attachments").first.click()
    sub = page.get_by_role("menuitem", name="Export chat").first
    try:
        await sub.wait_for(state="visible", timeout=4_000)
    except Exception:
        more = page.get_by_role("menuitem", name="More").first
        await more.hover()
        try:
            await sub.wait_for(state="visible", timeout=4_000)
        except Exception:
            await more.click()
            await sub.wait_for(state="visible", timeout=10_000)
    await sub.hover()
    await sub.click()
    item = page.get_by_role("menuitem", name=item_label, exact=True).first
    await item.wait_for(state="visible", timeout=15_000)
    async with page.expect_download(timeout=30_000) as caught:
        await item.click()
    download = await caught.value
    await download.save_as(str(dest))
    await page.keyboard.press("Escape")
    return dest.read_text()


async def _pin_panel(page, label: str, picker: str, sharegpt: list[str], training: list[str]) -> None:
    def rows(values: list[str]) -> str:
        if not values:
            return "<div class='v'>(none)</div>"
        return "".join(
            f"<div class='v {_which(v)}'>{html.escape(v)}</div>" for v in values
        )

    body = (
        "<style>#uidiff-panel{position:fixed;right:16px;bottom:120px;width:470px;z-index:99999;"
        "background:#111827;color:#f9fafb;font:13px/1.45 ui-monospace,Menlo,monospace;"
        "border:2px solid #6b7280;border-radius:10px;padding:12px 14px;box-shadow:0 8px 30px #0006}"
        "#uidiff-panel h4{margin:8px 0 4px;font-size:12px;color:#9ca3af;font-weight:600}"
        "#uidiff-panel .t{font-size:15px;font-weight:700;margin-bottom:4px}"
        "#uidiff-panel .v{padding:4px 6px;border-radius:5px;margin:3px 0}"
        "#uidiff-panel .first{background:#065f46}#uidiff-panel .regenerated{background:#7f1d1d}</style>"
        f"<div class='t'>{html.escape(label)} &mdash; on screen: reply {html.escape(picker)}</div>"
        f"<h4>ShareGPT JSONL export, gpt turns</h4>{rows(sharegpt)}"
        f"<h4>Training JSONL export, assistant turns</h4>{rows(training)}"
    )
    await page.evaluate(
        "(b) => { const d = document.createElement('div'); d.id = 'uidiff-panel'; d.innerHTML = b; document.body.appendChild(d); }",
        body,
    )


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    **_: object,
) -> tuple[list[Path], dict]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    thread_id = f"uidiff-sharegpt-branch-{RUN}"
    _seed(session, thread_id)
    base = session.base_url
    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(),
        [],
    )
    facts: dict = {"thread_id": thread_id, "stored_row_count": len(_stored(session, thread_id))}

    async with open_chat(base, init_scripts=[auth_script], viewport=(1280, 900),
                         headless=True) as sp:
        page = sp.page
        await page.goto(f"{base}/chat?thread={thread_id}", wait_until="domcontentloaded",
                        timeout=60_000)
        await page.locator("form:has(textarea) textarea").first.wait_for(state="visible", timeout=90_000)
        await page.get_by_text(RETRY_REPLY[:30]).first.wait_for(state="visible", timeout=60_000)
        await page.wait_for_timeout(2_000)
        facts["picker_state_on_load"] = await _picker_state(page)

        await page.locator('[data-role="assistant"]').last.hover()
        previous = page.locator(PICKER).last.get_by_role("button", name="Previous")
        try:
            await previous.click(timeout=5_000)
        except Exception:
            await previous.click(force=True, timeout=5_000)
        await page.get_by_text(FIRST_REPLY[:30]).first.wait_for(state="visible", timeout=30_000)
        await page.wait_for_timeout(1_000)
        facts["picker_state_after_previous"] = await _picker_state(page)
        assistant_text = " ".join((await page.locator('[data-role="assistant"]').last.inner_text()).split())
        facts["on_screen_reply"] = _which(assistant_text)

        sharegpt_raw = await _export(page, "ShareGPT JSONL", out_dir / f"{label.lower()}_sharegpt.jsonl")
        training_raw = await _export(page, "Training JSONL", out_dir / f"{label.lower()}_training.jsonl")
        sharegpt = [t["value"] for t in json.loads(sharegpt_raw.strip())["conversations"] if t["from"] == "gpt"]
        training = [
            m["content"] for m in json.loads(training_raw.strip())["messages"] if m["role"] == "assistant"
        ]
        facts.update({
            "sharegpt_raw": sharegpt_raw,
            "training_raw": training_raw,
            "sharegpt_gpt_turns": [_which(v) for v in sharegpt],
            "training_assistant_turns": [_which(v) for v in training],
            "sharegpt_matches_screen": [_which(v) for v in sharegpt] == [facts["on_screen_reply"]],
            "training_matches_screen": [_which(v) for v in training] == [facts["on_screen_reply"]],
        })

        await _pin_panel(page, label, facts["picker_state_after_previous"], sharegpt, training)
        await page.wait_for_timeout(300)
        shot = out_dir / f"{label.lower()}_01_export_after_previous.png"
        await page.screenshot(path=str(shot))

    return [shot], facts
