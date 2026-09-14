"""Scene: a follow-up turn on a thread that already holds a tool call, under a GGUF whose
embedded chat template llama-server's Jinja cannot parse.

The template is the real 252-line zai-org/GLM-5.3 one (md5 e4c9e100...), carried by a
0.5B GGUF so the shot needs no 100GB of weights: the defect is in the template, and
llama-server reads it out of the GGUF either way. Line 97 uses numeric member access
(`m.content.0.output`), which throws inside llama.cpp's capability probe; the probe
swallows it, `supports_object_arguments` stays false, the replayed call's arguments are
never decoded back into an object, and line 157's `arguments.items()` dies on the string.

The thread is seeded through the real chat-history API so both sides replay identical
stored bytes, and the same conversation is also sent to /v1/chat/completions on the very
server that was photographed, so the status code is measured rather than inferred.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat, send_prompt, wait_for_stream  # noqa: E402

THREAD_ID = "glm53-tool-replay-thread"
THREAD_TITLE = "Tool call replay evidence"
CREATED_AT = 1_755_000_000_000

TOOL_NAME = "render_html"
CALL_ID = "call_glm53_render"
CALL_ARGS = {"code": "<h1>hi</h1>"}
TOOL_RESULT = "rendered"
FOLLOW_UP = "In one sentence, what did you just do?"


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
        "content": [{"type": "text", "text": "Render a card that says hi"}],
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


def _replay_probe(session: Session, model_id: str) -> tuple[int, str]:
    """The same replayed history, straight at the photographed server."""
    return _request(session, "/v1/chat/completions", {
        "model": model_id,
        "messages": [
            {"role": "user", "content": "Render a card that says hi"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": CALL_ID, "type": "function",
                "function": {"name": TOOL_NAME, "arguments": json.dumps(CALL_ARGS)},
            }]},
            {"role": "tool", "tool_call_id": CALL_ID, "name": TOOL_NAME,
             "content": TOOL_RESULT},
            {"role": "user", "content": FOLLOW_UP},
        ],
        "max_tokens": 24, "stream": False,
    })


async def drive(session: Session, out_dir: Path, label: str, model_path: str = "",
                context_length: int = 4096, **_: object) -> tuple[list[Path], dict]:
    loaded = _load_model(session, model_path, context_length)
    model_id = loaded.get("active_model") or model_path
    _seed(session)

    status_code, body = _replay_probe(session, model_id)
    parser_error = "Unable to generate parser for this template" in body
    items_error = "hint: 'items'" in body

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
        await page.wait_for_timeout(2_000)

        # The kit's composer locator, not get_by_role("textbox"): the sidebar search
        # input precedes the composer in the DOM and silently wins .first.
        await send_prompt(sp, FOLLOW_UP)
        try:
            await wait_for_stream(sp, timeout_ms=120_000)
        except Exception:
            # The failing side never shows a stop button; the settle below is what
            # gives its error time to land.
            pass
        await page.wait_for_timeout(8_000)

        body_text = " ".join((await page.locator("body").inner_text()).split())
        shot = out_dir / f"{label.lower()}_glm53_tool_call_replay.png"
        await page.screenshot(path=str(shot))

        facts = {
            "model_path": model_path,
            "active_model": model_id,
            "replay_http_status": status_code,
            "replay_parser_error": parser_error,
            "replay_items_error": items_error,
            "replay_body_head": body[:300],
            "ui_shows_parser_error": "Unable to generate parser" in body_text,
            # Proves the send landed: a missed composer looks like "no answer" too.
            "ui_follow_up_sent": FOLLOW_UP in body_text,
            "ui_body_char_count": len(body_text),
            "ui_body_tail": body_text[-400:],
        }
        return [shot], facts
