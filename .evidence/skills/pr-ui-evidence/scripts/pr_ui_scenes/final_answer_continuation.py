"""Scene: an answer the context window cut in half, and whether Studio can finish it (PR 10219).

Nothing is intercepted. A real GGUF is resident at a 1024-token window, and the turn below
is a real `/v1/chat/completions` against the photographed Studio: two earlier exchanges,
then a request for 3000 words. It genuinely runs out of room mid-sentence, which is the
condition `_MAX_LENGTH_CONTINUATIONS` exists for, and Studio sends the partial back with
`continue_final_message`. The earlier exchanges are load-bearing -- the retry only becomes
servable once there is older history to evict -- and no `max_tokens` is sent, because a
caller's own cap is spent by the first attempt and no continuation is owed.

The API monitor is then photographed, because that is the Studio surface that records what
the turn did.

BEFORE (merge base) the continuation goes out with `continue_final_message` and no
`add_generation_prompt`, llama-server defaults the latter to true and refuses the pair, so
the turn dies on a 400 and the caller keeps half a story. AFTER (head) the flag is turned
off alongside, the continuations are served, and the answer runs on.
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
from studio_test_kit.ui import open_chat  # noqa: E402

_FILLER = (
    "Earlier in this session we went through the bakery's inventory in detail: "
    "flour grades, hydration ratios, proofing times, oven temperatures, crumb "
    "structure, scoring patterns, starter feeding schedules and shaping technique. "
)
STORY = (
    "Now write a long, detailed story about a dragon who learns to bake bread. "
    "Write at least 3000 words. Do not stop early."
)
FLAG_ERROR = "Cannot set both add_generation_prompt and continue_final_message to true"
# A client tools array, which is what puts the route on the agentic path where the
# final-answer continuation lives. It needs a template that advertises tools, hence
# Qwen3 rather than gemma-3-270m, whose template is refused outright ("Client-supplied
# tools or tool-call history require a GGUF chat template with tool-call support").
TOOLS = [{
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}]
# Qwen3's two end tokens, held down so the answer runs to the context wall instead of
# wrapping up inside it. Same on both sides; it manufactures the length stop the
# continuation exists for rather than hoping a 0.6B model writes 3000 words.
NO_STOPPING = {"151645": -100, "151643": -100}


def _conversation() -> list[dict]:
    messages = []
    for i in range(2):
        messages.append({"role": "user", "content": f"Notes part {i}. " + _FILLER * 4})
        messages.append({"role": "assistant", "content": f"Recorded part {i}. " + _FILLER * 4})
    messages.append({"role": "user", "content": STORY})
    return messages


def _request(session: Session, path: str, payload: dict, timeout: int = 900):
    req = urllib.request.Request(
        f"{session.base_url}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {session.access_token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _load_model(session: Session, model_path: str, context_length: int,
                timeout_s: int = 1800) -> dict:
    api_post(session, "/api/inference/load",
             {"model_path": model_path, "max_seq_length": context_length, "n_parallel": 1},
             timeout=timeout_s)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = api_get(session, "/api/inference/status")
        if status.get("active_model") and not status.get("loading"):
            return status
        time.sleep(3)
    raise RuntimeError(f"{model_path} never became resident")


def _answer_of(body: str) -> str:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return ""
    choices = parsed.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message") or {}).get("content") or ""


async def drive(session: Session, out_dir: Path, label: str, model_path: str = "",
                context_length: int = 1024, **_: object) -> tuple[list[Path], dict]:
    loaded = _load_model(session, model_path, context_length)
    model_id = loaded.get("active_model") or model_path

    # A control turn the window holds comfortably: 200 on BOTH sides, or the model,
    # port or install differs and the pair proves nothing.
    control_status, _ = _request(session, "/v1/chat/completions", {
        "model": model_id,
        "messages": [{"role": "user", "content": "Say OK."}],
        "max_tokens": 8, "temperature": 0, "stream": False,
    })
    status, body = _request(session, "/v1/chat/completions", {
        "model": model_id,
        "messages": _conversation(),
        "tools": TOOLS,
        "enable_thinking": False,
        "logit_bias": NO_STOPPING,
        "temperature": 0, "seed": 1234, "stream": False,
    })
    answer = _answer_of(body)

    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(),
        [],
    )

    async with open_chat(session.base_url, init_scripts=[auth_script],
                         viewport=(1280, 900), headless=True) as sp:
        page = sp.page
        await page.goto(f"{session.base_url}/api-monitor", wait_until="domcontentloaded")
        await page.get_by_label("Search API requests").wait_for(state="visible", timeout=60_000)
        await page.wait_for_timeout(4_000)

        body_text = " ".join((await page.locator("body").inner_text()).split())
        shot = out_dir / f"{label.lower()}_final_answer_continuation.png"
        await page.screenshot(path=str(shot))

        facts = {
            "model_path": model_path,
            "active_model": model_id,
            "context_length": context_length,
            # Identical on both sides; a move here means the pair is not comparable.
            "control_http_status": control_status,
            "turn_http_status": status,
            "turn_flag_error": FLAG_ERROR in body,
            "answer_chars": len(answer),
            "answer_tail": answer[-200:],
            "response_head": body[:300],
            "ui_monitor_shows_error": "error" in body_text.lower(),
            "ui_body_char_count": len(body_text),
        }
        return [shot], facts
