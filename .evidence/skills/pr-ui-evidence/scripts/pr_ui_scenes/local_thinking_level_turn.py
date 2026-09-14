"""Scene: the level picked in the Think menu, and what the local model was actually told (PR 10458).

Nothing is intercepted or stubbed. A real Qwen3-0.6B GGUF is resident in the photographed
Studio, loaded with a `chat_template_override` that is a genuine wide-ladder
``reasoning_effort`` template: it branches on the quoted literals 'none' | 'minimal' |
'low' | 'medium' | 'high' | 'xhigh' | 'max', so Studio's own detection publishes all seven
levels and the Think menu offers exactly those on BOTH sides. The template renders the
level it received into the system line the model is asked to echo, so the assistant bubble
in the screenshot IS the level that reached llama-server.

The chosen level is picked through the real Think dropdown in the composer and the turn is
sent from the real composer.

BEFORE (merge base) the request builder forwards only 'none' | 'low' | 'medium' | 'high',
so 'Max' is dropped: the request carries no `reasoning_effort` at all, the template takes
its else-branch, and the reply reads EFFORT=NOTHING. AFTER (head) the advertised level is
forwarded and the reply reads EFFORT=MAX.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post, open_menu  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat, pick_model, send_prompt, wait_for_stream  # noqa: E402

# A real reasoning_effort-style template over the whole ladder. The quoted literals are
# what Studio's detection scans for, and the rendered system line is what makes the level
# visible in the reply. No 'enable_thinking' anywhere: that would make it the hybrid style.
WIDE_LADDER_TEMPLATE = """{%- set _eff = reasoning_effort | default('') -%}
{%- if _eff == 'none' -%}{%- set _tag = 'NONE' -%}
{%- elif _eff == 'minimal' -%}{%- set _tag = 'MINIMAL' -%}
{%- elif _eff == 'low' -%}{%- set _tag = 'LOW' -%}
{%- elif _eff == 'medium' -%}{%- set _tag = 'MEDIUM' -%}
{%- elif _eff == 'high' -%}{%- set _tag = 'HIGH' -%}
{%- elif _eff == 'xhigh' -%}{%- set _tag = 'XHIGH' -%}
{%- elif _eff == 'max' -%}{%- set _tag = 'MAX' -%}
{%- else -%}{%- set _tag = 'NOTHING' -%}
{%- endif -%}
<|im_start|>system
Answer with exactly this line and nothing else: EFFORT={{ _tag }}<|im_end|>
{% for message in messages %}<|im_start|>{{ message['role'] }}
{{ message['content'] }}<|im_end|>
{% endfor %}<|im_start|>assistant
<think>

</think>

"""

LEVELS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
# The dropdown's own label for each level; 'xhigh' reads "Extra High" off a local model.
MENU_LABEL = {"none": "None", "minimal": "Minimal", "low": "Low", "medium": "Medium",
              "high": "High", "xhigh": "Extra High", "max": "Max"}
PROMPT = "Which effort level reached you?"
_EFFORT_RE = re.compile(r"EFFORT=([A-Z]+)")


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
              "n_parallel": 1, "chat_template_override": WIDE_LADDER_TEMPLATE},
             timeout=timeout_s)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = api_get(session, "/api/inference/status")
        if status.get("active_model") and not status.get("loading"):
            return status
        time.sleep(3)
    raise RuntimeError(f"{model_path} never became resident")


def _effort_tag(text: str) -> str:
    m = _EFFORT_RE.search(text or "")
    return m.group(1) if m else ""


async def drive(session: Session, out_dir: Path, label: str, model_path: str = "",
                context_length: int = 2048, level: str = "max",
                **_: object) -> tuple[list[Path], dict]:
    loaded = _load_model(session, model_path, context_length)
    model_id = loaded.get("active_model") or model_path

    # What the server tells the Think menu. Identical on both sides -- the menu is not
    # what this PR changes, and a move here means the two sides are not comparable.
    advertised = list(loaded.get("reasoning_effort_levels") or [])
    reasoning_style = loaded.get("reasoning_style")

    # An API turn at the same level, for the numbers: the reply is the level the template
    # was rendered with, independent of anything the browser did.
    api_status, api_body = _request(session, "/v1/chat/completions", {
        "model": model_id,
        "messages": [{"role": "user", "content": PROMPT}],
        "reasoning_effort": level,
        "max_tokens": 24, "temperature": 0, "seed": 1234, "stream": False,
    })
    api_answer = ""
    try:
        choices = json.loads(api_body).get("choices") or []
        api_answer = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
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
        await page.locator("form:has(textarea) textarea").first.wait_for(
            state="visible", timeout=60_000)
        try:
            await pick_model(sp, model_id)
        except Exception:  # noqa: BLE001 -- the resident model is already selected
            await page.keyboard.press("Escape")
        await page.wait_for_timeout(2_000)

        # The Think control: a dropdown for a reasoning_effort model, labelled
        # "Thinking . <Level>" once a level is picked.
        trigger = page.locator('[data-pill-label="Thinking settings"]').first
        await trigger.wait_for(state="visible", timeout=60_000)
        menu_open_label = MENU_LABEL[level]
        item = page.get_by_role("menuitem", name=re.compile(rf"^\s*{menu_open_label}\s*$"))
        await open_menu(page, trigger, item.first)
        # Photograph the open menu: the rows are the levels the model advertises.
        menu_rows = [t.strip() for t in await page.get_by_role("menuitem").all_inner_texts()]
        menu_shot = out_dir / f"{label.lower()}_think_menu.png"
        await page.screenshot(path=str(menu_shot))
        await item.first.click()
        await page.wait_for_timeout(1_000)

        # The click must have landed, or the turn below proves nothing.
        trigger_label = (await trigger.inner_text()).strip()
        if menu_open_label.lower() not in trigger_label.lower():
            raise RuntimeError(
                f"Think menu still reads {trigger_label!r} after picking {menu_open_label!r}")

        await send_prompt(sp, PROMPT)
        await wait_for_stream(sp, timeout_ms=180_000)
        await page.wait_for_timeout(2_000)

        bubbles = await page.locator(".aui-assistant-message-content").all_inner_texts()
        ui_reply = (bubbles[-1].strip() if bubbles else "")
        if not ui_reply:
            body_text = await page.locator("body").inner_text()
            ui_reply = body_text
        shot = out_dir / f"{label.lower()}_thinking_level_turn.png"
        await page.screenshot(path=str(shot))

        facts = {
            "model_path": model_path,
            "active_model": model_id,
            "context_length": context_length,
            # Control: the same on both sides. The menu already offered every level.
            "reasoning_style": reasoning_style,
            "advertised_levels": advertised,
            "menu_rows": menu_rows,
            "level_picked": level,
            "think_trigger_label": trigger_label,
            # The point of the scene: which level the template was rendered with.
            "ui_effort_tag": _effort_tag(ui_reply),
            "ui_reply": ui_reply[:300],
            "api_http_status": api_status,
            "api_effort_tag": _effort_tag(api_answer),
            "api_reply": api_answer[:300],
        }
        return [shot, menu_shot], facts
