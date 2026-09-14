"""Scene: a chat thread that already ran a Studio built-in tool, after the chat is
switched to a GGUF whose chat template does not advertise tools.

Once `search_conversation` runs in a thread, the client keeps the assistant `tool_calls`
turn and its `role="tool"` result and replays both with every later message. Read as a
CLIENT tool contract, that history routes the turn into the llama-server passthrough,
which a toolless template cannot serve -- so every later message in the thread 400s, and
the context bar's recount 503s beside it. gemma-3-270m-it is the repo's canonical GGUF
whose template has no tool markup, and it is 254 MiB, so the shot needs no real weights.

The thread is seeded through the real chat-history API so both sides replay identical
stored bytes, and the same conversation is also sent to /v1/chat/completions and
/v1/chat/count_tokens on the very server that was photographed, so the status codes and
the token count are measured rather than inferred.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat, send_prompt, wait_for_stream  # noqa: E402

# Unique per run. A fixed id keeps whatever a previous run's follow-up left in the thread,
# so both sides open with a stale answer already on the page and photograph history rather
# than behaviour; deleting it instead is not an option, because Studio tombstones the id and
# answers 410 to the re-create. The title is unique for the same reason -- the sidebar click
# matches on it, and a leftover thread with the same title wins .first.
_RUN = uuid.uuid4().hex[:8]
THREAD_ID = f"toolless-switch-thread-{_RUN}"
THREAD_TITLE = f"Tool history after model switch {_RUN}"
CREATED_AT = 1_755_000_000_000

TOOL_NAME = "search_conversation"
CALL_ID = "call_search_conversation"
CALL_ARGS = {"query": "seed"}
TOOL_RESULT = "Earlier in this chat the seed was 3407."
FIRST_QUESTION = "Search our chat for the seed we used"
FOLLOW_UP = "In one short sentence, what colour is the sky?"

REJECTION = "does not advertise tools"


def _request(session: Session, path: str, payload: dict, method: str = "POST",
             timeout: int = 300) -> tuple[int, str]:
    req = urllib.request.Request(
        f"{session.base_url}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {session.access_token}"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()[:4000]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:4000]


def _load_model(session: Session, model_path: str, context_length: int,
                timeout_s: int = 1800) -> dict:
    api_post(session, "/api/inference/load",
             {"model_path": model_path, "max_seq_length": context_length}, timeout=timeout_s)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = api_get(session, "/api/inference/status")
        if status.get("active_model") and not status.get("loading"):
            return status
        time.sleep(3)
    raise RuntimeError(f"{model_path} never became resident")


def _seed(session: Session) -> None:
    api_post(session, "/api/chat/threads", {
        "id": THREAD_ID, "title": THREAD_TITLE, "modelType": "base", "modelId": "",
        "createdAt": CREATED_AT, "updatedAt": CREATED_AT,
    })
    _request(session, f"/api/chat/threads/{THREAD_ID}/messages/{THREAD_ID}-q", {
        "id": f"{THREAD_ID}-q", "threadId": THREAD_ID, "role": "user",
        "content": [{"type": "text", "text": FIRST_QUESTION}],
        "createdAt": CREATED_AT,
    }, method="PUT")
    _request(session, f"/api/chat/threads/{THREAD_ID}/messages/{THREAD_ID}-a", {
        "id": f"{THREAD_ID}-a", "threadId": THREAD_ID, "role": "assistant",
        "content": [{
            "type": "tool-call", "toolCallId": CALL_ID, "toolName": TOOL_NAME,
            "argsText": json.dumps(CALL_ARGS), "args": CALL_ARGS, "result": TOOL_RESULT,
        }],
        "createdAt": CREATED_AT + 1,
    }, method="PUT")


def _replayed_messages() -> list[dict]:
    """Exactly what the client replays on the thread's next turn."""
    return [
        {"role": "user", "content": FIRST_QUESTION},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": CALL_ID, "type": "function",
            "function": {"name": TOOL_NAME, "arguments": json.dumps(CALL_ARGS)},
        }]},
        {"role": "tool", "tool_call_id": CALL_ID, "name": TOOL_NAME, "content": TOOL_RESULT},
        {"role": "user", "content": FOLLOW_UP},
    ]


def _turn_probe(session: Session, model_id: str) -> tuple[int, str]:
    return _request(session, "/v1/chat/completions", {
        "model": model_id, "messages": _replayed_messages(),
        "studio_tool_history": True, "temperature": 0, "max_tokens": 48, "stream": False,
    })


def _count_probe(session: Session, model_id: str) -> tuple[int, str]:
    """What the context bar asks for while the same thread is open."""
    return _request(session, "/v1/chat/count_tokens", {
        "model": model_id, "messages": _replayed_messages(),
        "studio_tool_history": True,
    })


async def drive(session: Session, out_dir: Path, label: str, model_path: str = "",
                context_length: int = 4096, **_: object) -> tuple[list[Path], dict]:
    loaded = _load_model(session, model_path, context_length)
    model_id = loaded.get("active_model") or model_path
    _seed(session)

    turn_status, turn_body = _turn_probe(session, model_id)
    count_status, count_body = _count_probe(session, model_id)
    try:
        counted = json.loads(count_body).get("input_tokens")
    except json.JSONDecodeError:
        counted = None
    answer = ""
    if turn_status == 200:
        try:
            answer = (json.loads(turn_body)["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            answer = ""

    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(),
        [],
    )

    async with open_chat(session.base_url, init_scripts=[auth_script],
                         viewport=(1280, 900), headless=True) as sp:
        page = sp.page
        entry = page.get_by_text(THREAD_TITLE, exact=True).first
        await entry.wait_for(state="visible", timeout=90_000)
        await entry.click()
        # The seeded tool card is what proves the thread -- and its history -- is open.
        await page.get_by_text(f"Used tool: {TOOL_NAME}").first.wait_for(
            state="visible", timeout=60_000)
        await page.wait_for_timeout(2_000)

        await send_prompt(sp, FOLLOW_UP)

        # A missed composer and a rejected turn both look like "no answer", so the sent
        # bubble is asserted on its own before anything is concluded from the reply.
        follow_up_bubble = page.get_by_text(FOLLOW_UP, exact=False).first
        try:
            await follow_up_bubble.wait_for(state="visible", timeout=60_000)
            sent = True
        except Exception:
            sent = False

        # The rejection surfaces as a toast that dismisses itself, so it has to be caught
        # while it is up rather than read off a settled page 8 seconds later.
        toast_seen = False
        rejection_seen = False
        answered = False
        shot = out_dir / f"{label.lower()}_toolless_switch_tool_history.png"
        shot_taken = False
        deadline = time.time() + 90
        while time.time() < deadline:
            body_now = " ".join((await page.locator("body").inner_text()).split())
            if "Generation failed" in body_now:
                toast_seen = True
                if not shot_taken:
                    await page.screenshot(path=str(shot))
                    shot_taken = True
            if REJECTION in body_now:
                rejection_seen = True
            if answer and body_now.count(answer) >= 1 and FOLLOW_UP in body_now:
                answered = True
                if not shot_taken:
                    await page.screenshot(path=str(shot))
                    shot_taken = True
            if shot_taken and (answered or toast_seen):
                break
            await page.wait_for_timeout(500)

        await page.wait_for_timeout(3_000)
        if not shot_taken:
            await page.screenshot(path=str(shot))

        body_text = " ".join((await page.locator("body").inner_text()).split())

        facts = {
            "model_path": model_path,
            "active_model": model_id,
            "turn_http_status": turn_status,
            "turn_rejected_for_tools": REJECTION in turn_body,
            "turn_answer": answer,
            "turn_body_head": turn_body[:300],
            "count_http_status": count_status,
            "count_input_tokens": counted,
            "count_body_head": count_body[:200],
            # Proves the send landed: a missed composer looks like "no answer" too.
            "ui_follow_up_sent": sent,
            "ui_generation_failed_toast": toast_seen,
            "ui_shows_tool_rejection": rejection_seen,
            "ui_assistant_answered": answered,
            "ui_body_char_count": len(body_text),
            "ui_body_tail": body_text[-400:],
        }
        return [shot], facts
