# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: the chat after a terminal call printed a line and then ran past its timeout.

The change under review is one BACKEND branch (`_bash_exec` / `_python_exec`, the
`if timed_out:` arm), so nothing may be seeded: a stored thread replays bytes the
frontend already has and photographs identically on both sides. The whole path has
to run for real -- the model emits `terminal`, Studio spawns the command, the drain
captures `progress` on stdout, the wall-clock limit fires, and the timeout arm either
does or does not put the captured text in front of the model.

`echo progress; sleep 300` is the smallest honest shape of the bug: the command has
already produced its output when the limit fires, and the file-sentinel half of that
same branch already survives a timeout, so only the printed text is in question.

The per-call limit is the composer's own "Max Tool Call Duration", whose floor is one
minute, seeded through the same localStorage key the settings sheet writes. So the
run really does hang for 60 seconds on each side; a shorter scene would not be the
path a user is on.

The stand-in provider answers with a sentence that is a pure function of what it was
actually handed, so the assistant bubble in the screenshot is the backend's own tool
message quoted back rather than a stand-in's opinion about it. Both Studios build the
same frontend (the diff is one backend file), so the only thing that can move the
picture is what the timeout branch returned.
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

MODEL_ID = "hanging-command-runner"

# Printed immediately, then the command outlives any limit the composer can set. The
# word is the whole measurement: it is on stdout, captured, before the kill.
PRINTED = "progress"
COMMAND = f"echo {PRINTED}; sleep 300"

# One minute is the slider's floor (chat-settings-sheet.tsx: "1 minute"), stored in
# MINUTES and sent as `mins * 60`, so this is the shortest real timeout a user has.
TIMEOUT_MINUTES = 1
TIMEOUT_SECONDS = TIMEOUT_MINUTES * 60
TIMEOUT_SENTENCE = f"Execution timed out after {TIMEOUT_SECONDS} seconds."

ANSWER_MARK = "Studio handed me"


class _ProviderState:
    """What the stand-in saw, so the scene can prove the command really ran."""

    def __init__(self) -> None:
        self.completions = 0
        self.offered_tools: list[str] = []
        self.tool_result_texts: list[str] = []
        self.commands_emitted: list[str] = []


def _sse(chunk: dict) -> bytes:
    return b"data: " + json.dumps(chunk).encode() + b"\n\n"


def _delta(delta: dict, finish=None) -> dict:
    return {
        "id": "chatcmpl-pr10664",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _answer_for(text: str) -> str:
    """What a model would say having been handed this tool message.

    Mechanical on purpose: each clause reports one property of the string the loop
    passed in, which is the only thing the reviewed branch decides.
    """
    saw_output = "the output it had already printed" if PRINTED in text else "nothing it printed"
    said_timeout = "and it did say the call timed out" if TIMEOUT_SENTENCE in text else \
        "and it did NOT say the call timed out"
    return f"{ANSWER_MARK} {saw_output} ({len(text)} chars), {said_timeout}."


def _make_provider(state: _ProviderState) -> type:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):  # noqa: A003 -- silence stderr spam
            pass

        def do_GET(self):  # noqa: N802
            body = json.dumps({"data": [{"id": MODEL_ID, "object": "model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _emit_call(self) -> None:
            state.commands_emitted.append(COMMAND)
            self.wfile.write(_sse(_delta({"tool_calls": [{
                "index": 0, "id": "call_pr10664_terminal", "type": "function",
                "function": {"name": "terminal", "arguments": ""}}]})))
            self.wfile.write(_sse(_delta({"tool_calls": [{
                "index": 0,
                "function": {"arguments": json.dumps({"command": COMMAND})}}]})))
            self.wfile.write(_sse(_delta({}, finish="tool_calls")))

        def do_POST(self):  # noqa: N802
            payload = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"
            )
            state.completions += 1
            for spec in payload.get("tools") or []:
                # chat/completions nests the name under "function"; the Responses API
                # puts it at the top level. Read both so an empty list is a real "no
                # tools offered" rather than a parser miss.
                name = (spec.get("function") or {}).get("name") or spec.get("name")
                if name:
                    state.offered_tools.append(name)
            tool_msgs = [m for m in payload.get("messages", []) if m.get("role") == "tool"]
            texts = []
            for m in tool_msgs:
                content = m.get("content")
                texts.append(content if isinstance(content, str) else json.dumps(content))
            # Rebuilt from the request rather than appended to: the client may retry a
            # turn and a growing list would then double-count.
            state.tool_result_texts = texts

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(_sse(_delta({"role": "assistant"})))
            if not texts:
                self._emit_call()
            else:
                for word in _answer_for(texts[-1]).split(" "):
                    self.wfile.write(_sse(_delta({"content": word + " "})))
                self.wfile.write(_sse(_delta({}, finish="stop")))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return Handler


def _reset_connections(session: Session) -> None:
    """Drop any provider row this home already holds.

    A home is reused across runs, and a leftover row pointing at a port nothing is
    listening on wins the model lookup: the chat then fails to connect and no card is
    ever drawn, which reads exactly like the tool not being called.
    """
    for row in api_get(session, "/api/providers/"):
        req = urllib.request.Request(
            f"{session.base_url}/api/providers/{row['id']}",
            headers={"Authorization": f"Bearer {session.access_token}"},
            method="DELETE",
        )
        urllib.request.urlopen(req, timeout=60)


def _register_provider(session: Session, port: int) -> str:
    """Save the stand-in endpoint through the REAL provider API.

    Seeding localStorage is not enough: the picker's "Connected" tab only exists once
    the backend holds a provider row. Registered as "custom", not "openai": the openai
    type routes to /v1/responses and this stand-in speaks chat/completions.
    """
    created = api_post(session, "/api/providers/", {
        "provider_type": "custom",
        "display_name": "Local Hanging Command Runner",
        "base_url": f"http://127.0.0.1:{port}/v1",
        "models": [MODEL_ID],
        "available_models": [MODEL_ID],
    })
    return created["id"]


def _timeout_init_script() -> str:
    """Pin "Max Tool Call Duration" to its floor before the app reads it.

    The store hydrates from this key on load, so writing it afterwards would not
    reach the request that matters. Stored in minutes, exactly as the settings sheet
    writes it.
    """
    return (
        "try { window.localStorage.setItem('unsloth_tool_call_timeout', "
        f"JSON.stringify({TIMEOUT_MINUTES})); }} catch (e) {{}}"
    )


async def _select_connected_model(page, model_id: str) -> None:
    await page.get_by_role("button", name="Select model").first.click(timeout=60_000)
    await page.get_by_role("tab", name="Connected").first.click(timeout=30_000)
    await page.get_by_text(model_id, exact=True).first.click(timeout=30_000)


CODE_PILL = 'form:has(textarea) button[data-pill-label="Code"]'


async def _enable_code(page) -> tuple[str, bool]:
    """Turn the composer's Code pill on and prove it.

    Not studio_test_kit.set_pill: that reads ``aria-pressed``, which this pill does not
    carry -- it reports state through ``data-active``. Reading the missing attribute
    makes "already on" indistinguishable from "off", so the helper would toggle Code
    OFF on a home that had it on and the model would be offered no terminal at all.
    """
    pill = page.locator(CODE_PILL).first
    if await pill.count() == 0:
        # Unpinned pills live in the + menu instead of on the composer row. The menu
        # entry TOGGLES, so it is only clicked when the pill is genuinely absent.
        await page.get_by_role(
            "button", name="Tools and attachments"
        ).first.click(timeout=30_000)
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


async def _run_to_completion(page, seconds: int = 300) -> int:
    """Approve the paused call and wait out the real 60-second hang.

    `terminal` always pauses for an Allow, and the budget has to clear the timeout
    itself plus the second completion: left short, the shot is of a card still
    spinning, which photographs identically on both sides.
    """
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
            except Exception:  # noqa: BLE001 -- resolved between count and click
                pass
        # Keyed on the sentence the stand-in only writes once the tool message is in,
        # not on "the approval happened": a side that finished early would otherwise
        # spin here for the whole budget and then photograph mid-run.
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
        "Run this in the terminal and then tell me exactly what Studio handed back "
        f"to you: {COMMAND}"
    ),
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph the chat after a terminal call overran its own time limit."""
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
            init_scripts=[seed_init_script(auth, []), _timeout_init_script()],
            viewport=(1280, 1400),
            headless=True,
        ) as sp:
            page = sp.page
            await _select_connected_model(page, MODEL_ID)
            await page.wait_for_timeout(1_500)
            # Without the Code pill the request carries no terminal at all and the
            # model just answers in prose: a normal-looking reply, no card, and
            # nothing to compare. Asserted on afterwards for that reason.
            code_pill_active, code_pill_disabled = await _enable_code(page)
            seeded_timeout = await page.evaluate(
                "() => window.localStorage.getItem('unsloth_tool_call_timeout')"
            )

            await send_prompt(sp, prompt)
            approvals = await _run_to_completion(page)

            # Once the answer lands, the finished call folds into a tool group and the
            # card leaves the DOM entirely. The group has to be reopened first or the
            # card locator times out on a run that worked perfectly.
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
            # The card closes itself once the assistant's text arrives, and the result
            # pane is the whole subject of this pair, so it is reopened on BOTH sides.
            for index in range(card_count):
                trigger = cards.nth(index).locator(
                    '[data-slot="tool-fallback-trigger"]'
                ).first
                if await trigger.count():
                    try:
                        await trigger.click(timeout=10_000)
                    except Exception:  # noqa: BLE001 -- already open
                        pass
            await page.wait_for_timeout(2_500)

            card_texts: dict[str, str] = {}
            for index in range(card_count):
                card_texts[f"card_{index}"] = " ".join(
                    (await cards.nth(index).inner_text()).split()
                )
            body_text = " ".join((await page.locator("body").inner_text()).split())

            await cards.first.scroll_into_view_if_needed()
            await page.wait_for_timeout(1_000)

            shot = out_dir / f"{label.lower()}_tool_timeout_partial_output.png"
            # A FIXED clip of the chat column, not element shots: the cards grow with
            # the text they render, so element shots differ in size between the sides
            # and the composite scales one half to illegibility. The clip also starts
            # right of the sidebar, whose recents list is per-home.
            await page.screenshot(
                path=str(shot),
                clip={"x": 280, "y": 0, "width": 1000, "height": 1400},
            )

            text = state.tool_result_texts[-1] if state.tool_result_texts else ""
            facts: dict = {
                "provider_registered": bool(provider_id),
                "code_pill_active": code_pill_active,
                "code_pill_disabled": code_pill_disabled,
                "terminal_offered_to_model": "terminal" in state.offered_tools,
                "provider_completions": state.completions,
                "commands_requested": state.commands_emitted,
                "seeded_tool_call_timeout_minutes": seeded_timeout,
                "approvals_clicked": approvals,
                "tool_cards_rendered": card_count,
                "tool_group_label": group_label,
                "card_texts": card_texts,
                # The measurement. The control is the sentence: it must be on BOTH
                # sides, or the call did not time out and nothing was measured.
                "partial_output_reached_model": PRINTED in text,
                "timeout_sentence_reached_model": TIMEOUT_SENTENCE in text,
                "tool_message_len": len(text),
                "tool_message_head": " ".join(text.split())[:220],
                "card_shows_printed_output": PRINTED in " ".join(card_texts.values()),
                "ui_answer_says_output_kept": (
                    "the output it had already printed" in body_text
                ),
                "ui_answer_says_output_lost": "nothing it printed" in body_text,
            }
            return [shot], facts
    finally:
        httpd.shutdown()
        httpd.server_close()
