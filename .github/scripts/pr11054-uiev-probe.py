#!/usr/bin/env python3
"""PR 11054 probe: attachments in the edit message box.

Seeds two threads through the chat-history API (no model), then drives the real edit flow.
Exit 1 on any failed expectation; facts and screenshots are always written.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import httpx
from PIL import Image, ImageDraw
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_test_kit.auth import login, seed_init_script  # noqa: E402

SIDE = "head" if os.environ.get("GITHUB_REF_NAME", "").endswith("-head") else "base"
ART = Path(os.environ.get("STUDIO_ARTIFACT_DIR", "artifacts")).resolve() / SIDE
ART.mkdir(parents=True, exist_ok=True)
HOME = Path(os.environ["UNSLOTH_STUDIO_HOME"]).resolve()

T0 = 1_755_000_000_000
PROMPT = "Compare these quarterly notes with the chart."
MARKER = "PASTE-MARKER-7Q"
PASTE_BODY = "\n".join(
    f"Line {i:03d}: revenue grew in region {i % 7}. {MARKER}" for i in range(120)
)
PASTE_NAME = "Quarterly notes"
PASTE_ID = "att-paste-11054"
IMAGE_ID = "att-image-11054"
THREAD = "pr11054-edit-attachments"
PASTE_ONLY_THREAD = "pr11054-paste-only"

EDIT_ROOT = ".aui-edit-composer-root"
failures: list[str] = []
facts: dict = {"side": SIDE, "ref": os.environ.get("GITHUB_REF_NAME"), "sha": os.environ.get("GITHUB_SHA")}


def check(ok: bool, message: str) -> None:
    print(("PASS " if ok else "FAIL ") + message, flush=True)
    if not ok:
        failures.append(message)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def find_bin() -> Path:
    for c in [HOME / "bin" / "unsloth", HOME / "unsloth_studio" / "bin" / "unsloth", *HOME.glob(".venv*/*/unsloth")]:
        if c.is_file():
            return c
    raise SystemExit(f"unsloth CLI not found under {HOME}")


def wait_health(base: str, timeout_s: int = 240) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=3) as r:
                if r.status < 500:
                    return
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        time.sleep(2)
    raise SystemExit("Studio never became healthy")


def read_password(log: Path) -> str:
    deadline = time.time() + 120
    while time.time() < deadline:
        p = HOME / "auth" / ".bootstrap_password"
        if p.is_file() and p.read_text().strip():
            return p.read_text().strip()
        m = re.search(r"(?i)(?:bootstrap|initial|generated)\s*password(?:\s+is)?\s*[:=]?\s+(\S+)", log.read_text(errors="ignore"))
        if m:
            return m.group(1).strip(".,")
        time.sleep(2)
    raise SystemExit("bootstrap password not found")


def png_data_url() -> str:
    img = Image.new("RGB", (160, 110), (245, 245, 245))
    d = ImageDraw.Draw(img)
    for i, h in enumerate((40, 70, 55, 95)):
        d.rectangle([18 + i * 34, 100 - h, 42 + i * 34, 100], fill=(40, 110, 220))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def paste_attachment() -> dict:
    size = len(PASTE_BODY.encode())
    return {
        "id": PASTE_ID, "type": "document", "name": PASTE_NAME, "contentType": "text/plain",
        "content": [{"type": "text", "text": f"<pasted_text name={PASTE_NAME} bytes={size}>\n{PASTE_BODY}\n</pasted_text>"}],
        "status": {"type": "complete"},
    }


class Api:
    def __init__(self, base: str, token: str):
        self.c = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=60)

    def thread(self, tid: str, title: str) -> None:
        self.c.post("/api/chat/threads", json={
            "id": tid, "title": title, "modelType": "base", "modelId": "",
            "archived": False, "createdAt": T0, "updatedAt": T0,
        }).raise_for_status()

    def message(self, tid: str, msg: dict) -> None:
        self.c.put(f"/api/chat/threads/{tid}/messages/{msg['id']}?allowGenerationEdit=true",
                   json={"threadId": tid, **msg}).raise_for_status()

    def rows(self, tid: str) -> list[dict]:
        r = self.c.get(f"/api/chat/threads/{tid}/messages")
        r.raise_for_status()
        return sorted(r.json().get("messages", []), key=lambda m: m.get("createdAt", 0))


def seed(api: Api) -> None:
    api.thread(THREAD, "Edit with attachments")
    api.message(THREAD, {
        "id": "u1-11054", "parentId": None, "role": "user", "createdAt": T0,
        "content": [{"type": "text", "text": PROMPT}],
        "attachments": [paste_attachment(), {
            "id": IMAGE_ID, "type": "image", "name": "chart.png", "contentType": "image/png",
            "content": [{"type": "image", "image": png_data_url()}], "status": {"type": "complete"},
        }],
    })
    api.message(THREAD, {
        "id": "a1-11054", "parentId": "u1-11054", "role": "assistant", "createdAt": T0 + 1,
        "content": [{"type": "text", "text": "Seeded reply: the notes and the chart agree."}],
    })
    api.thread(PASTE_ONLY_THREAD, "Paste only")
    api.message(PASTE_ONLY_THREAD, {
        "id": "u2-11054", "parentId": None, "role": "user", "createdAt": T0,
        "content": [], "attachments": [paste_attachment()],
    })
    api.message(PASTE_ONLY_THREAD, {
        "id": "a2-11054", "parentId": "u2-11054", "role": "assistant", "createdAt": T0 + 1,
        "content": [{"type": "text", "text": "Seeded reply to a paste-only message."}],
    })


async def open_edit(page) -> None:
    user = page.locator(".aui-user-message-root").first
    await user.scroll_into_view_if_needed()
    for _ in range(4):
        await user.hover()
        await page.wait_for_timeout(300)
        btn = user.locator(".aui-user-action-edit").first
        try:
            await btn.click(timeout=3_000)
        except Exception:
            await btn.click(force=True, timeout=3_000)
        try:
            await page.locator(EDIT_ROOT).first.wait_for(state="visible", timeout=5_000)
            await page.wait_for_timeout(800)
            return
        except Exception:
            continue
    raise RuntimeError("edit composer did not open")


async def edit_state(page) -> dict:
    root = page.locator(EDIT_ROOT).first
    return {
        "attachments": await root.locator(".aui-attachment-root").count(),
        "remove_buttons": await root.locator(".aui-attachment-tile-remove").count(),
        "pasted_chips": await root.locator(".aui-pasted-text-chip").count(),
        "image_tiles": await root.locator(".aui-attachment-tile").count(),
        "text": await root.locator("textarea").first.input_value(),
    }


async def shot(page, name: str, locator=None) -> None:
    path = ART / f"{SIDE}_{name}.png"
    if locator is not None:
        await locator.screenshot(path=str(path))
    else:
        await page.screenshot(path=str(path))


async def goto_thread(page, base: str, tid: str, anchor: str) -> None:
    await page.goto(f"{base}/chat?thread={tid}", wait_until="domcontentloaded", timeout=60_000)
    await page.get_by_text(anchor, exact=False).first.wait_for(state="visible", timeout=90_000)
    await page.wait_for_timeout(2_500)


async def drive(base: str, init: str, api: Api) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        await ctx.add_init_script(init)
        page = await ctx.new_page()
        console_errors: list[str] = []
        page.on("console", lambda m: console_errors.append(m.text[:300]) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {str(e)[:300]}"))
        runs: list[str] = []
        page.on("request", lambda r: runs.append(r.post_data or "") if r.method == "POST" and "/api/inference/chat-runs" in r.url else None)

        await goto_thread(page, base, THREAD, PROMPT)
        user = page.locator(".aui-user-message-root").first
        facts["transcript_attachments"] = await user.locator(".aui-attachment-root").count()
        check(facts["transcript_attachments"] == 2, f"seeded message renders 2 attachments in the transcript (got {facts['transcript_attachments']})")

        await open_edit(page)
        facts["edit_open"] = await edit_state(page)
        await shot(page, "01_edit_open", page.locator(".aui-edit-composer-wrapper").first)
        await shot(page, "01_edit_open_page")
        check(facts["edit_open"]["attachments"] == 2, f"edit box shows the 2 attachments it will resend (got {facts['edit_open']['attachments']})")
        check(facts["edit_open"]["remove_buttons"] == 2, f"edit box offers a remove button per attachment (got {facts['edit_open']['remove_buttons']})")

        # Cancel after removing must keep both attachments on the message.
        if facts["edit_open"]["remove_buttons"]:
            await page.locator(f"{EDIT_ROOT} .aui-attachment-root:has(.aui-pasted-text-chip) .aui-attachment-tile-remove").first.click()
            await page.wait_for_timeout(500)
            facts["cancel_after_remove_edit_count"] = (await edit_state(page))["attachments"]
        await page.locator(EDIT_ROOT).get_by_role("button", name="Cancel").click()
        await page.wait_for_timeout(1_000)
        facts["after_cancel_transcript_attachments"] = await page.locator(".aui-user-message-root").first.locator(".aui-attachment-root").count()
        check(facts["after_cancel_transcript_attachments"] == 2, f"Cancel keeps both attachments (got {facts['after_cancel_transcript_attachments']})")

        # Unchanged Update (text touched and restored) must not branch.
        await open_edit(page)
        box = page.locator(f"{EDIT_ROOT} textarea").first
        await box.fill(PROMPT + " x")
        await box.fill(PROMPT)
        await page.locator(EDIT_ROOT).get_by_role("button", name="Update").click()
        await page.wait_for_timeout(2_500)
        facts["noop_update_edit_closed"] = await page.locator(EDIT_ROOT).count() == 0
        facts["noop_update_user_rows"] = sum(1 for r in api.rows(THREAD) if r["role"] == "user")
        facts["noop_update_chat_runs"] = len(runs)
        check(facts["noop_update_edit_closed"] and facts["noop_update_user_rows"] == 1 and not runs,
              f"unchanged Update closes without a new message or run (closed={facts['noop_update_edit_closed']}, user rows={facts['noop_update_user_rows']}, runs={len(runs)})")

        # Remove the paste, Update: the new user message must not carry it.
        await open_edit(page)
        remove = page.locator(f"{EDIT_ROOT} .aui-attachment-root:has(.aui-pasted-text-chip) .aui-attachment-tile-remove").first
        if await remove.count():
            await remove.click()
            await page.wait_for_timeout(600)
        facts["after_remove"] = await edit_state(page)
        await shot(page, "02_paste_removed", page.locator(".aui-edit-composer-wrapper").first)
        await page.locator(EDIT_ROOT).get_by_role("button", name="Update").click()

        new_user: dict | None = None
        deadline = time.time() + 45
        while time.time() < deadline and new_user is None:
            await page.wait_for_timeout(1_500)
            new_user = next((r for r in api.rows(THREAD) if r["role"] == "user" and r["id"] != "u1-11054"), None)
        await page.wait_for_timeout(2_000)
        facts["update_new_user_row"] = new_user is not None
        if new_user is not None:
            blob = json.dumps(new_user)
            facts["update_new_user_attachment_ids"] = [a.get("id") for a in (new_user.get("attachments") or [])]
            facts["update_new_user_attachment_names"] = [a.get("name") for a in (new_user.get("attachments") or [])]
            facts["update_new_user_has_paste_marker"] = MARKER in blob
            facts["update_new_user_has_image"] = "data:image/png" in blob
        facts["chat_runs_requests"] = len(runs)
        facts["chat_runs_have_paste_marker"] = any(MARKER in r for r in runs)
        facts["chat_runs_have_image"] = any("data:image/png" in r for r in runs)
        visible_user = page.locator(".aui-user-message-root").first
        facts["after_update_transcript_attachments"] = await visible_user.locator(".aui-attachment-root").count()
        await shot(page, "03_after_update_page")
        check(bool(new_user) and not facts.get("update_new_user_has_paste_marker", True) and facts.get("update_new_user_has_image", False),
              f"Update after removing the paste sends the message without it and keeps the image "
              f"(row={bool(new_user)}, names={facts.get('update_new_user_attachment_names')}, marker={facts.get('update_new_user_has_paste_marker')})")
        if runs:
            check(not facts["chat_runs_have_paste_marker"], "the generation request no longer contains the removed paste")

        # A message that was only a long paste.
        await goto_thread(page, base, PASTE_ONLY_THREAD, "Seeded reply to a paste-only message.")
        await open_edit(page)
        facts["paste_only_edit"] = await edit_state(page)
        await shot(page, "04_paste_only_edit", page.locator(".aui-edit-composer-wrapper").first)
        check(facts["paste_only_edit"]["pasted_chips"] == 1, f"paste-only message opens with its paste chip, not an empty box (chips={facts['paste_only_edit']['pasted_chips']})")

        facts["console_errors"] = console_errors[:40]
        await ctx.close()
        await browser.close()


async def main() -> None:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = HOME.parent / "studio.log"
    env = {**os.environ, "UNSLOTH_STUDIO_HOME": str(HOME)}
    with log.open("w") as fh:
        proc = subprocess.Popen([str(find_bin()), "studio", "-H", "127.0.0.1", "-p", str(port)],
                                stdout=fh, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    try:
        wait_health(base)
        password = read_password(log)
        auth = await login(base, "unsloth", password)
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{base}/api/auth/login", json={"username": "unsloth", "password": password})
            r.raise_for_status()
            must_change = bool(r.json().get("must_change_password"))
            if must_change:
                r = await c.post(f"{base}/api/auth/change-password",
                                 headers={"Authorization": f"Bearer {auth.access_token}"},
                                 json={"current_password": password, "new_password": "UnslothStudioCI2026!"})
                r.raise_for_status()
                body = r.json()
                auth.access_token = body["access_token"]
                auth.refresh_token = body.get("refresh_token", "")
        facts["must_change_password"] = must_change
        api = Api(base, auth.access_token)
        seed(api)
        try:
            await drive(base, seed_init_script(auth, []), api)
        except Exception as exc:
            failures.append(f"driver error: {type(exc).__name__}: {exc}")
            print(f"FAIL driver error: {exc}", flush=True)
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            proc.terminate()
        facts["failures"] = failures
        (ART / f"{SIDE}_facts.json").write_text(json.dumps(facts, indent=2))
        print(json.dumps(facts, indent=2), flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
