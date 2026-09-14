"""Scene: the web_search cards after the model reads pages with unusable charset labels.

The change under review is in the BACKEND (`_fetch_url_raw`), so nothing may be
seeded: a stored conversation replays bytes the frontend already has and
photographs identically on both sides. The whole path has to run for real -- the
model emits `web_search`, Studio fetches the real page over the network, the
backend decides what to make of the declared charset, and the SPA draws the card.

The page has to be PUBLIC: `_validate_and_resolve_host` refuses any non-global
address, so a local fixture server cannot be the thing fetched. httpbin's
`/response-headers` echoes whatever Content-Type the query string asks for, which
gives a real public URL that declares a charset Python cannot decode with, on
demand and identically for both sides.

Four calls, in this order, because the last one is the control:

  1. charset=unicode    -- `codecs.lookup` raises LookupError
  2. charset=base64     -- passes lookup, `bytes.decode` refuses a non-text codec
  3. charset=idna       -- passes lookup and a strict decode, rejects errors="replace"
  4. charset=utf-8      -- an ordinary label, must read fine on BOTH sides

A side that serves the fourth and not the first three has a charset bug, not a
network problem, and all four cards sit in one screenshot saying so.

Both Studios build the same frontend (the diff is one backend file), so the card
renderer is constant and the only thing that can move the picture is what the
backend made of the declared charset.
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

MODEL_ID = "web-reader"

_BASE = "https://httpbin.org/response-headers"


def _url(charset: str) -> str:
    return f"{_BASE}?Content-Type=text%2Fhtml%3B%20charset%3D{charset}&MARKERWORD=present"


CASES = [
    ("unicode", _url("unicode")),
    ("base64", _url("base64")),
    ("idna", _url("idna")),
    ("utf-8", _url("utf-8")),
]

FAILURE_MARK = "Failed to fetch URL"
ANSWER_MARK = "Of the four labels"
PAGE_MARK = "MARKERWORD"


class _ProviderState:
    """What the fake provider saw, so the scene can prove the fetch actually ran."""

    def __init__(self) -> None:
        self.completions = 0
        self.offered_tools: list[str] = []
        self.tool_result_texts: list[str] = []
        self.calls_emitted: list[str] = []


def _sse(chunk: dict) -> bytes:
    return b"data: " + json.dumps(chunk).encode() + b"\n\n"


def _delta(delta: dict, finish=None) -> dict:
    return {
        "id": "chatcmpl-pr10221",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _answer_for(texts: list[str]) -> str:
    """What a model would say having been handed these results.

    Deliberately mechanical: the sentence is a function of the tool results
    alone, so the assistant bubble in the screenshot is the backend's own output
    quoted back, not a stand-in's opinion about it.
    """
    read, lost = [], []
    for (label, _), text in zip(CASES, texts):
        (read if PAGE_MARK in (text or "") else lost).append(label)
    parts = [f"Of the four labels I was asked to read, {len(read)} returned the page"]
    if read:
        parts.append(" (" + ", ".join(read) + ")")
    if lost:
        parts.append(f" and {len(lost)} failed to fetch (" + ", ".join(lost) + ")")
    return "".join(parts) + "."


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

        def _emit_call(self, index: int, url: str) -> None:
            state.calls_emitted.append(url)
            self.wfile.write(_sse(_delta({"tool_calls": [{
                "index": 0, "id": f"call_pr10221_{index}", "type": "function",
                "function": {"name": "web_search", "arguments": ""}}]})))
            self.wfile.write(_sse(_delta({"tool_calls": [{
                "index": 0,
                "function": {"arguments": json.dumps({"url": url})}}]})))
            self.wfile.write(_sse(_delta({}, finish="tool_calls")))

        def do_POST(self):  # noqa: N802
            payload = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"
            )
            state.completions += 1
            for spec in payload.get("tools") or []:
                # chat/completions nests the name under "function"; the Responses
                # API puts it at the top level. Read both so an empty list is a
                # real "no tools offered" rather than a parser miss.
                name = (spec.get("function") or {}).get("name") or spec.get("name")
                if name:
                    state.offered_tools.append(name)
            tool_msgs = [m for m in payload.get("messages", []) if m.get("role") == "tool"]
            texts = []
            for m in tool_msgs:
                content = m.get("content")
                texts.append(content if isinstance(content, str) else json.dumps(content))
            # Rebuilt from the request rather than appended to, because the client
            # may retry a turn and a growing list would then double-count.
            state.tool_result_texts = texts

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(_sse(_delta({"role": "assistant"})))
            if len(texts) < len(CASES):
                self._emit_call(len(texts), CASES[len(texts)][1])
            else:
                for word in _answer_for(texts).split(" "):
                    self.wfile.write(_sse(_delta({"content": word + " "})))
                self.wfile.write(_sse(_delta({}, finish="stop")))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return Handler


def _reset_connections(session: Session) -> None:
    """Drop any provider row this home already holds.

    A home is reused across runs, and a leftover row pointing at a port nothing
    is listening on wins the model lookup: the chat then fails to connect and no
    card is ever drawn, which reads exactly like the tool not being called.
    """
    for row in api_get(session, "/api/providers/"):
        req = urllib.request.Request(
            f"{session.base_url}/api/providers/{row['id']}",
            headers={"Authorization": f"Bearer {session.access_token}"},
            method="DELETE",
        )
        urllib.request.urlopen(req, timeout=60)


def _register_provider(session: Session, port: int) -> str:
    """Save the fake endpoint through the REAL provider API.

    Seeding localStorage is not enough: the picker's "Connected" tab only exists
    once the backend holds a provider row. Registered as "custom", not "openai":
    the openai type routes to /v1/responses and this stand-in speaks
    chat/completions.
    """
    created = api_post(session, "/api/providers/", {
        "provider_type": "custom",
        "display_name": "Local Web Reader",
        "base_url": f"http://127.0.0.1:{port}/v1",
        "models": [MODEL_ID],
        "available_models": [MODEL_ID],
    })
    return created["id"]


async def _select_connected_model(page, model_id: str) -> None:
    await page.get_by_role("button", name="Select model").first.click(timeout=60_000)
    await page.get_by_role("tab", name="Connected").first.click(timeout=30_000)
    await page.get_by_text(model_id, exact=True).first.click(timeout=30_000)


SEARCH_PILL = 'form:has(textarea) button[data-pill-label="Search"]'


async def _enable_search(page) -> tuple[str, bool]:
    """Turn the composer's Search pill on and prove it.

    Not studio_test_kit.set_pill: that reads ``aria-pressed``, which this pill does
    not carry -- it reports state through ``data-active`` and puts the verb in its
    aria-label. Reading the missing attribute makes "already on" indistinguishable
    from "off", so the helper would toggle Search OFF on a home that had it on and
    the model would be offered no tools at all.
    """
    pill = page.locator(SEARCH_PILL).first
    if await pill.count() == 0:
        # Unpinned pills live in the + menu instead of on the composer row. The
        # menu entry TOGGLES, so it is only clicked when the pill is genuinely
        # absent, never as a second attempt at one that is already there.
        await page.get_by_role(
            "button", name="Tools and attachments"
        ).first.click(timeout=30_000)
        # The menu animates in; a click before it settles is eaten by the overlay.
        await page.wait_for_timeout(1_500)
        menu = page.locator("[data-radix-popper-content-wrapper]").last
        await menu.get_by_text("Search", exact=True).first.click(timeout=15_000)
        await page.wait_for_timeout(1_500)
    await pill.wait_for(state="visible", timeout=60_000)
    disabled = await pill.is_disabled()
    if not disabled and await pill.get_attribute("data-active") != "true":
        await pill.click(timeout=30_000)
        await page.wait_for_timeout(1_000)
    return (await pill.get_attribute("data-active") or ""), disabled


async def _run_to_completion(page, seconds: int = 420) -> int:
    """Approve every paused call until the turn stops streaming.

    `web_search` carrying a `url` fetches that page, so the confirm gate pauses it
    for an Allow on both sides. Left unclicked the run simply never finishes and
    the cards stay empty, which photographs as "the tool returned nothing".
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
        # Keyed on the sentence the stand-in only writes once ALL results are in,
        # not on "approvals happened": a side that needed no approval would
        # otherwise spin here for the whole budget and then photograph mid-run.
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
        "Read these four URLs in order and tell me which ones you could actually read: "
        + " then ".join(url for _, url in CASES)
    ),
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph the web_search cards after a real fetch of four charset labels."""
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
            # Without the Search pill the request carries no tools at all and the
            # model just answers in prose: a normal-looking reply, no card, and
            # nothing to compare. Asserted on afterwards for that reason.
            search_pill_active, search_pill_disabled = await _enable_search(page)

            await send_prompt(sp, prompt)
            approvals = await _run_to_completion(page)

            # Once the answer lands, the finished calls fold into one "N tool
            # calls" group and the individual cards leave the DOM entirely. The
            # group has to be reopened first or the card locator times out on a
            # run that worked perfectly.
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
            # The card closes itself once the assistant's text arrives, and the
            # result pane is what the backend change rewrites, so every card has
            # to be reopened on BOTH sides.
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

            shot = out_dir / f"{label.lower()}_web_fetch_unusable_charset.png"
            # A FIXED clip of the chat column, not element shots: the cards grow
            # with the text they render, so element shots differ in size between
            # the sides and the composite scales one half to illegibility. The clip
            # also starts right of the sidebar, whose recents list is per-home.
            await page.screenshot(
                path=str(shot),
                clip={"x": 280, "y": 0, "width": 1000, "height": 1400},
            )

            texts = state.tool_result_texts
            facts: dict = {
                "provider_registered": bool(provider_id),
                "search_pill_active": search_pill_active,
                "search_pill_disabled": search_pill_disabled,
                "web_search_offered_to_model": "web_search" in state.offered_tools,
                "provider_completions": state.completions,
                "urls_requested": state.calls_emitted,
                "approvals_clicked": approvals,
                "tool_cards_rendered": card_count,
                "tool_group_label": group_label,
                "card_texts": card_texts,
                "page_shows_fetch_failure": FAILURE_MARK in body_text,
            }
            read_labels, lost_labels = [], []
            for index, (charset, _) in enumerate(CASES):
                text = texts[index] if index < len(texts) else ""
                got_page = PAGE_MARK in text
                facts[f"{charset}_read_the_page"] = got_page
                facts[f"{charset}_failed"] = text.strip().startswith(FAILURE_MARK)
                facts[f"{charset}_result_len"] = len(text)
                facts[f"{charset}_result_head"] = " ".join(text.split())[:200]
                (read_labels if got_page else lost_labels).append(charset)
            facts["labels_that_read_the_page"] = read_labels
            facts["labels_that_failed"] = lost_labels
            facts["pages_read"] = len(read_labels)
            return [shot], facts
    finally:
        httpd.shutdown()
        httpd.server_close()
