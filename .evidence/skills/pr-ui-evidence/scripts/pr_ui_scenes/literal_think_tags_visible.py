"""Scene: literal <think> text in a reply, with the Thinking toggle off (PR 10662).

Nothing is intercepted or stubbed. A real gemma-3-270m-it GGUF is resident in the
photographed Studio, loaded with a `chat_template_override` that is a genuine
hybrid `enable_thinking` template, so Studio's detection publishes the
enable_thinking style and the composer offers the Thinking toggle on BOTH sides.

The tags have to reach the model from the TEMPLATE, not from the prompt: Studio
neutralizes control markup in message content on the way in (#7066), so a user
turn saying "<think>" arrives as "< think>" and the model copies the spaced-out
form. The template is a launch file and is not neutralized, so the two
demonstration turns baked into it carry the real characters, and the model
copies those.

The template also renders IDENTICALLY whether thinking is on or off -- it reads
`enable_thinking` only so Studio can detect the style. So the prompt, and hence
the model's own output, is the same in every call below; the only thing that
differs is whether the server runs the typed-thinking splitter over the reply.

BEFORE (merge base) the reply is still run through that splitter even though the
request turned thinking off, so `<think>hi</think>` is cut out of the visible
answer and its contents move into a hidden reasoning block. AFTER (head) the
gate reads the request, the splitter does not run, and the sentence stays whole.
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
from studio_test_kit.ui import open_chat, pick_model, send_prompt, wait_for_stream  # noqa: E402

ANSWER = "Use <think>hi</think> in your prompt."
PROMPT = "Show me a think tag."

THINK_TAG_TEMPLATE = (
    "{%- set _think = enable_thinking | default(true) -%}\n"
    "<start_of_turn>user\n" + PROMPT + "<end_of_turn>\n"
    "<start_of_turn>model\n" + ANSWER + "<end_of_turn>\n"
    "<start_of_turn>user\n" + PROMPT + "<end_of_turn>\n"
    "<start_of_turn>model\n" + ANSWER + "<end_of_turn>\n"
    "{% for message in messages %}"
    "<start_of_turn>{{ 'model' if message['role'] == 'assistant' else 'user' }}\n"
    "{{ message['content'] }}<end_of_turn>\n"
    "{% endfor %}<start_of_turn>model\n"
)


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
             {"model_path": model_path, "max_seq_length": context_length,
              "n_parallel": 1, "chat_template_override": THINK_TAG_TEMPLATE},
             timeout=timeout_s)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = api_get(session, "/api/inference/status")
        if status.get("active_model") and not status.get("loading"):
            return status
        time.sleep(3)
    raise RuntimeError(f"{model_path} never became resident")


def _chat(session: Session, model_id: str, **extra):
    status, body = _request(session, "/v1/chat/completions", {
        "model": model_id,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": 48, "temperature": 0, "seed": 1234, "stream": False,
        **extra,
    })
    content = reasoning = ""
    try:
        choices = json.loads(body).get("choices") or []
        msg = (choices[0].get("message") or {}) if choices else {}
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or ""
    except json.JSONDecodeError:
        pass
    return status, content, reasoning


async def drive(session: Session, out_dir: Path, label: str, model_path: str = "",
                context_length: int = 2048, **_: object) -> tuple[list[Path], dict]:
    loaded = _load_model(session, model_path, context_length)
    model_id = loaded.get("active_model") or model_path
    reasoning_style = loaded.get("reasoning_style")

    # The turn under test, straight at the API with thinking off.
    api_status, api_content, api_reasoning = _chat(
        session, model_id, enable_thinking=False)

    # Control: the SAME prompt with thinking explicitly ON. The splitter is
    # supposed to run here, so this must come back split on BOTH sides. That is
    # the proof the model really typed the tags and that both installs can still
    # parse them; if this pair moves, parsing broke rather than the gate being
    # fixed, and the thinking-off pair proves nothing.
    ctl_status, ctl_content, ctl_reasoning = _chat(
        session, model_id, enable_thinking=True)

    # The Responses endpoint, the other surface the gate guards.
    resp_status, resp_body = _request(session, "/v1/responses", {
        "model": model_id,
        "input": PROMPT,
        "reasoning": {"effort": "none"},
        "max_output_tokens": 48, "temperature": 0, "stream": False,
    })
    resp_text = ""
    resp_kinds: list[str] = []
    try:
        out = json.loads(resp_body).get("output") or []
        resp_kinds = [item.get("type") for item in out]
        for item in out:
            if item.get("type") == "message":
                for part in item.get("content") or []:
                    resp_text += part.get("text") or ""
    except json.JSONDecodeError:
        pass

    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(),
        [],
    )

    async with open_chat(session.base_url, init_scripts=[auth_script],
                         viewport=(1280, 900), headless=True) as sp:
        page = sp.page
        composer = page.locator("form:has(textarea) textarea").first
        await composer.wait_for(state="visible", timeout=60_000)
        try:
            await pick_model(sp, model_id)
        except Exception:  # noqa: BLE001 -- the resident model is already selected
            pass
        # Whatever the picker left open must be gone before anything is typed,
        # or the prompt lands in a palette and the turn never happens.
        for _ in range(3):
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
        await composer.click()
        await page.wait_for_timeout(1_000)

        # The Think control for an enable_thinking model is a plain on/off pill.
        pill = page.locator('[data-pill-label="Thinking"]').first
        await pill.wait_for(state="visible", timeout=60_000)
        if (await pill.get_attribute("data-active")) == "true":
            await pill.click()
            await page.wait_for_timeout(1_000)
        thinking_pill_active = await pill.get_attribute("data-active")
        if thinking_pill_active == "true":
            raise RuntimeError("Thinking pill is still on after clicking it off")

        await send_prompt(sp, PROMPT)
        await wait_for_stream(sp, timeout_ms=180_000)
        await page.wait_for_timeout(2_000)

        bubbles = await page.locator(".aui-assistant-message-content").all_inner_texts()
        shot = out_dir / f"{label.lower()}_literal_think_tags.png"
        await page.screenshot(path=str(shot))
        if not bubbles:
            raise RuntimeError(
                "no assistant bubble after the turn -- the composer never sent it, "
                "so there is nothing to photograph")
        ui_reply = bubbles[-1].strip()

        facts = {
            "model_path": model_path,
            "active_model": model_id,
            "context_length": context_length,
            # Controls: identical on both sides, or the sides are not comparable.
            "reasoning_style": reasoning_style,
            "thinking_pill_active": thinking_pill_active,
            "api_http_status": api_status,
            "responses_http_status": resp_status,
            "control_thinking_on_http_status": ctl_status,
            "control_thinking_on_reasoning": ctl_reasoning[:300],
            "control_thinking_on_content": ctl_content[:300],
            # The point of the scene: whether the tags survived as text.
            "ui_shows_think_tags": "<think>" in ui_reply and "</think>" in ui_reply,
            "ui_reply": ui_reply[:300],
            "api_shows_think_tags": "<think>" in api_content and "</think>" in api_content,
            "api_content": api_content[:300],
            "api_reasoning_content": api_reasoning[:300],
            "responses_output_kinds": resp_kinds,
            "responses_shows_think_tags": "<think>" in resp_text and "</think>" in resp_text,
            "responses_text": resp_text[:300],
        }
        return [shot], facts
