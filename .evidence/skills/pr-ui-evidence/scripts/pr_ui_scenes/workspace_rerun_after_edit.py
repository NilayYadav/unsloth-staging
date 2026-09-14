"""Scene: a terminal command re-run after edit_file changed the file it reads.

A stand-in OpenAI-compatible provider scripts four real calls through the real
Studio tool loop: edit_file creates notes.txt, terminal cats it, edit_file edits
it, terminal cats it again. The final answer only quotes what the second cat
handed back, so the bubble is the backend's verdict rather than the stand-in's.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post, pick_free_ports  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat, send_prompt  # noqa: E402

MODEL_ID = "workspace-rerun-runner"
ANSWER_MARK = "Studio handed me"
BEFORE_TEXT = "version one"
AFTER_TEXT = "version two"

CALLS = [
    ("create", "edit_file", {"path": "notes.txt", "edits": [{"old_string": "", "new_string": BEFORE_TEXT + "\n"}]}),
    ("read_before_edit", "terminal", {"command": "cat notes.txt"}),
    ("edit", "edit_file", {"path": "notes.txt", "edits": [{"old_string": BEFORE_TEXT, "new_string": AFTER_TEXT}]}),
    ("read_after_edit", "terminal", {"command": "cat notes.txt"}),
]


def _call_id(index: int) -> str:
    return f"call_pr10810_{index}"


class _ProviderState:
    def __init__(self) -> None:
        self.completions = 0
        self.emitted = 0
        self.offered_tools: set[str] = set()
        self.tool_results: list[dict] = []
        self.calls_emitted: list[str] = []


def _sse(chunk: dict) -> bytes:
    return b"data: " + json.dumps(chunk).encode() + b"\n\n"


def _delta(delta: dict, finish=None) -> dict:
    return {
        "id": "chatcmpl-pr10810",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _result_for(results: list[dict], index: int) -> str:
    for row in results:
        if row["id"] == _call_id(index):
            return row["text"]
    return results[index]["text"] if index < len(results) else ""


def _answer_for(results: list[dict]) -> str:
    after = _result_for(results, 3)
    if AFTER_TEXT in after:
        verdict = f"the cat after the edit printed {AFTER_TEXT}"
    elif BEFORE_TEXT in after:
        verdict = f"the cat after the edit printed stale {BEFORE_TEXT}"
    else:
        verdict = "the cat after the edit was NOT run"
    return f"{ANSWER_MARK} {len(results)} tool results, and {verdict}."


def _make_provider(state: _ProviderState) -> type:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):  # noqa: A003
            pass

        def do_GET(self):  # noqa: N802
            body = json.dumps({"data": [{"id": MODEL_ID, "object": "model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _emit_call(self, index: int) -> None:
            _, name, arguments = CALLS[index]
            state.calls_emitted.append(f"{name} {json.dumps(arguments)}")
            self.wfile.write(_sse(_delta({"tool_calls": [{
                "index": 0, "id": _call_id(index), "type": "function",
                "function": {"name": name, "arguments": ""}}]})))
            self.wfile.write(_sse(_delta({"tool_calls": [{
                "index": 0, "function": {"arguments": json.dumps(arguments)}}]})))
            self.wfile.write(_sse(_delta({}, finish="tool_calls")))

        def _emit_text(self, text: str) -> None:
            for word in text.split(" "):
                self.wfile.write(_sse(_delta({"content": word + " "})))
            self.wfile.write(_sse(_delta({}, finish="stop")))

        def do_POST(self):  # noqa: N802
            payload = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"
            )
            state.completions += 1
            tools = payload.get("tools") or []
            for spec in tools:
                name = (spec.get("function") or {}).get("name") or spec.get("name")
                if name:
                    state.offered_tools.add(name)
            messages = payload.get("messages", [])
            results = []
            for m in messages:
                if m.get("role") != "tool":
                    continue
                content = m.get("content")
                results.append({
                    "id": m.get("tool_call_id") or "",
                    "text": content if isinstance(content, str) else json.dumps(content),
                })
            in_tool_turn = bool(tools) or bool(results)
            if in_tool_turn:
                state.tool_results = results

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(_sse(_delta({"role": "assistant"})))
            if not in_tool_turn:
                self._emit_text("Notes check")
            elif tools and state.emitted < len(CALLS):
                self._emit_call(state.emitted)
                state.emitted += 1
            else:
                self._emit_text(_answer_for(results))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return Handler


def _reset_connections(session: Session) -> None:
    for row in api_get(session, "/api/providers/"):
        req = urllib.request.Request(
            f"{session.base_url}/api/providers/{row['id']}",
            headers={"Authorization": f"Bearer {session.access_token}"},
            method="DELETE",
        )
        urllib.request.urlopen(req, timeout=60)


def _register_provider(session: Session, port: int) -> str:
    created = api_post(session, "/api/providers/", {
        "provider_type": "custom",
        "display_name": "Local Workspace Runner",
        "base_url": f"http://127.0.0.1:{port}/v1",
        "models": [MODEL_ID],
        "available_models": [MODEL_ID],
    })
    return created["id"]


async def _select_connected_model(page, model_id: str) -> None:
    await page.get_by_role("button", name="Select model").first.click(timeout=60_000)
    await page.get_by_role("tab", name="Connected").first.click(timeout=30_000)
    await page.get_by_text(model_id, exact=True).first.click(timeout=30_000)


CODE_PILL = 'form:has(textarea) button[data-pill-label="Code"]'


async def _enable_code(page) -> tuple[str, bool]:
    pill = page.locator(CODE_PILL).first
    if await pill.count() == 0:
        await page.get_by_role("button", name="Tools and attachments").first.click(timeout=30_000)
        await page.wait_for_timeout(1_500)
        menu = page.locator("[data-radix-popper-content-wrapper]").last
        await menu.get_by_text("Code", exact=True).first.click(timeout=15_000)
        await page.wait_for_timeout(1_500)
    await pill.wait_for(state="visible", timeout=60_000)
    disabled = await pill.is_disabled()
    if not disabled and await pill.get_attribute("data-active") != "true":
        await pill.click(timeout=30_000)
        await page.wait_for_timeout(1_000)
    return (await pill.get_attribute("data-active") or ""), disabled


async def _run_to_completion(page, seconds: int = 420) -> int:
    allow = page.get_by_role("button", name="Allow", exact=True)
    stop = page.locator('button[aria-label="Stop generating"]').first
    answer = page.get_by_text(ANSWER_MARK, exact=False)
    approvals = 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if await allow.count():
            try:
                await allow.first.click(timeout=5_000)
                approvals += 1
                await page.wait_for_timeout(1_000)
                continue
            except Exception:  # noqa: BLE001
                pass
        if await answer.count() and await stop.count() == 0:
            await page.wait_for_timeout(2_000)
            if await allow.count() == 0 and await stop.count() == 0:
                return approvals
        await page.wait_for_timeout(1_000)
    return approvals


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    *,
    prompt: str = (
        "Create notes.txt, cat it, edit it with edit_file, then cat it again to "
        "verify the edit and tell me what the second cat printed."
    ),
    **_: object,
) -> tuple[list[Path], dict]:
    _reset_connections(session)

    state = _ProviderState()
    port = pick_free_ports(1, start=9400, stop=9500)[0]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _make_provider(state))
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
            viewport=(1280, 1400),
            headless=True,
        ) as sp:
            page = sp.page
            await _select_connected_model(page, MODEL_ID)
            await page.wait_for_timeout(1_500)
            code_pill_active, code_pill_disabled = await _enable_code(page)

            await send_prompt(sp, prompt)
            approvals = await _run_to_completion(page)

            group = page.locator('[data-slot="tool-group-root"]').first
            group_label = ""
            if await group.count():
                trigger = group.locator('[data-slot="tool-group-trigger"]').first
                group_label = " ".join((await trigger.inner_text()).split())
                if await page.locator('[data-slot="tool-fallback-root"]').count() == 0:
                    await trigger.click(timeout=30_000)
                    await page.wait_for_timeout(2_000)

            cards = page.locator('[data-slot="tool-fallback-root"]')
            await cards.first.wait_for(state="visible", timeout=60_000)
            card_count = await cards.count()
            for index in range(card_count):
                trigger = cards.nth(index).locator('[data-slot="tool-fallback-trigger"]').first
                if await trigger.count():
                    try:
                        await trigger.click(timeout=10_000)
                    except Exception:  # noqa: BLE001
                        pass
            await page.wait_for_timeout(2_500)

            card_texts: dict[str, str] = {}
            for index in range(card_count):
                card_texts[f"card_{index}"] = " ".join((await cards.nth(index).inner_text()).split())
            body_text = " ".join((await page.locator("body").inner_text()).split())

            await cards.first.scroll_into_view_if_needed()
            await page.wait_for_timeout(1_000)

            shot = out_dir / f"{label.lower()}_workspace_rerun_after_edit.png"
            await page.screenshot(
                path=str(shot),
                clip={"x": 280, "y": 0, "width": 1000, "height": 1400},
            )

            results = state.tool_results
            before_read = _result_for(results, 1)
            after_read = _result_for(results, 3)
            facts: dict = {
                "provider_registered": bool(provider_id),
                "code_pill_active": code_pill_active,
                "code_pill_disabled": code_pill_disabled,
                "terminal_offered_to_model": "terminal" in state.offered_tools,
                "edit_file_offered_to_model": "edit_file" in state.offered_tools,
                "provider_completions": state.completions,
                "calls_emitted": state.calls_emitted,
                "approvals_clicked": approvals,
                "tool_results_returned": len(results),
                "tool_results": [
                    {"id": r["id"], "text": " ".join(r["text"].split())[:300]} for r in results
                ],
                "read_before_edit_printed_version_one": BEFORE_TEXT in before_read,
                "rerun_after_edit_executed": AFTER_TEXT in after_read,
                "rerun_after_edit_skipped_as_duplicate": (
                    AFTER_TEXT not in after_read
                    and ("identical" in after_read or "not executed" in after_read or "did not run" in after_read)
                ),
                "rerun_after_edit_result_head": " ".join(after_read.split())[:300],
                "tool_cards_rendered": card_count,
                "tool_group_label": group_label,
                "card_texts": card_texts,
                "ui_answer_says_rerun_printed_version_two": f"printed {AFTER_TEXT}" in body_text,
                "ui_answer_says_rerun_not_run": "was NOT run" in body_text,
            }
            return [shot], facts
    finally:
        httpd.shutdown()
        httpd.server_close()
