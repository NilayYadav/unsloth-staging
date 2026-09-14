# Scene for PR #10944: a transformers vision chat that must remember turns before the image.
"""Scene: text turn with a code word, then an image turn, then ask for the code word.

On the transformers vision path, once the thread carries an image, every later turn is
routed through ``_generate_vision_response``. BEFORE it collapsed the prompt to the newest
user text plus the image, so the code word from turn 1 never reached the model. AFTER the
whole thread is rendered with the image on the turn that sent it.

Facts come from two sources on the same server that is photographed:
  ui_*   the real chat UI (default sampling), plus the request body the frontend sent
  api_*  the OpenAI-compatible route with temperature 0 and usage.prompt_tokens
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import urllib.error
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402

CODE_WORD = "PELICAN-42"
IMAGE_WORD = "CAT"
TURN1 = f"Remember this code word for later: {CODE_WORD}. Reply only with OK."
TURN2 = "What word is written in this image? Answer with the word only."
TURN3 = "What was the code word I gave you in my first message? Answer with the code word only."
TURN3_IMAGE = "What word was written in the image I sent? Answer with the word only."
FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)


def _word_png_bytes(word: str, size: int = 448) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = None
    for path in FONTS:
        try:
            font = ImageFont.truetype(path, 180)
            break
        except Exception:  # noqa: BLE001
            continue
    draw.text((size // 2, size // 2), word, fill=(0, 0, 0), anchor="mm",
              font=font or ImageFont.load_default())
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _chat(session: Session, messages: list, max_tokens: int = 24) -> dict:
    payload = {
        "messages": messages,
        "stream": False,
        "temperature": 0,
        "top_k": 1,
        "max_tokens": max_tokens,
    }
    last = None
    for path in ("/v1/chat/completions", "/api/inference/chat/completions"):
        try:
            out = api_post(session, path, payload, timeout=600)
            text = (out.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            return {"route": path, "answer": text.strip()[:300],
                    "prompt_tokens": (out.get("usage") or {}).get("prompt_tokens")}
        except urllib.error.HTTPError as exc:
            last = f"{path}: HTTP {exc.code} {exc.read().decode()[:200]}"
            if exc.code != 404:
                break
    return {"error": last}


def _api_facts(session: Session, image_url: str) -> dict:
    image_turn = {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": image_url}},
        {"type": "text", "text": TURN2},
    ]}
    history = [
        {"role": "user", "content": TURN1},
        {"role": "assistant", "content": "OK"},
        image_turn,
        {"role": "assistant", "content": IMAGE_WORD},
    ]
    text_only = [
        {"role": "user", "content": TURN1},
        {"role": "assistant", "content": "OK"},
        {"role": "user", "content": "Name a colour. Answer with one word."},
        {"role": "assistant", "content": "Blue"},
    ]
    facts = {
        "api_image_chat_recall": _chat(session, history + [{"role": "user", "content": TURN3}]),
        "api_image_chat_reads_image": _chat(session, history + [{"role": "user", "content": TURN3_IMAGE}]),
        "api_text_only_control_recall": _chat(session, text_only + [{"role": "user", "content": TURN3}]),
    }
    for key in ("api_image_chat_recall", "api_text_only_control_recall"):
        facts[f"{key}_has_code_word"] = CODE_WORD in (facts[key].get("answer") or "").upper()
    facts["api_image_chat_reads_image_has_word"] = (
        IMAGE_WORD in (facts["api_image_chat_reads_image"].get("answer") or "").upper()
    )
    return facts


def _summarize_request(body: str | None) -> dict:
    try:
        data = json.loads(body or "")
    except Exception:  # noqa: BLE001
        return {"unparsed": True}
    roles, image_parts = [], []
    for m in data.get("messages") or []:
        roles.append(m.get("role"))
        content = m.get("content")
        image_parts.append(
            sum(1 for p in content if isinstance(p, dict) and p.get("type") == "image_url")
            if isinstance(content, list) else 0
        )
    return {"roles": roles, "image_parts_per_message": image_parts,
            "mentions_code_word": CODE_WORD in json.dumps(data)}


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    *,
    model: str = "unsloth/Qwen2-VL-2B-Instruct-bnb-4bit",
    max_seq_length: int = 4096,
    load_timeout_ms: int = 1_200_000,
    turn_timeout_ms: int = 600_000,
    **_: object,
) -> tuple[list[Path], dict]:
    from studio_test_kit.auth import seed_init_script
    from studio_test_kit.ui import open_chat, pick_model, send_prompt, wait_for_stream

    out_dir.mkdir(parents=True, exist_ok=True)
    png = _word_png_bytes(IMAGE_WORD)
    image_path = out_dir / f"{IMAGE_WORD.lower()}.png"
    image_path.write_bytes(png)
    image_url = "data:image/png;base64," + base64.b64encode(png).decode()
    facts: dict = {"model": model, "code_word": CODE_WORD, "image_word": IMAGE_WORD}

    api_post(session, "/api/inference/load",
             {"model_path": model, "max_seq_length": max_seq_length},
             timeout=load_timeout_ms // 1000)
    deadline = time.time() + load_timeout_ms / 1000
    while time.time() < deadline:
        st = api_get(session, "/api/inference/status")
        if st.get("active_model") == model and not st.get("loading"):
            break
        time.sleep(3)
    else:
        raise RuntimeError(f"{model} never became resident on {session.base_url}")
    status = api_get(session, "/api/inference/status")
    facts["active_model"] = status.get("active_model")
    facts["is_vision"] = status.get("is_vision")
    facts["is_gguf"] = status.get("is_gguf")

    facts.update(_api_facts(session, image_url))

    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(),
        [],
    )
    shots: list[Path] = []
    requests: list[str | None] = []
    async with open_chat(session.base_url, init_scripts=[auth_script],
                         viewport=(1280, 1100), headless=True) as sp:
        page = sp.page
        page.on("request", lambda r: requests.append(r.post_data)
                if r.method == "POST" and "completions" in r.url else None)
        await page.locator("form:has(textarea) textarea").first.wait_for(
            state="visible", timeout=120_000)
        try:
            await pick_model(sp, model, timeout_ms=30_000)
        except Exception as exc:  # noqa: BLE001
            facts["pick_model_note"] = f"picker skipped: {type(exc).__name__}"

        bodies = page.locator(".aui-assistant-message-content")
        for turn, prompt in enumerate((TURN1, TURN2, TURN3), start=1):
            if turn == 2:
                async with page.expect_file_chooser(timeout=60_000) as chooser_info:
                    await page.locator('button[aria-label="Tools and attachments"]').first.click()
                    await page.get_by_role("menuitem", name="Add photos & files").first.click()
                chooser = await chooser_info.value
                await chooser.set_files(str(image_path))
                await page.locator(
                    f'button[aria-label="Image attachment: {image_path.name}"]'
                ).first.wait_for(state="visible", timeout=60_000)
            before = len(requests)
            await send_prompt(sp, prompt)
            await wait_for_stream(sp, timeout_ms=turn_timeout_ms)
            await bodies.nth(turn - 1).wait_for(state="visible", timeout=turn_timeout_ms)
            answer = (await bodies.nth(turn - 1).inner_text()).strip()
            facts[f"ui_turn{turn}_answer"] = answer[:300]
            if len(requests) > before:
                facts[f"ui_turn{turn}_request"] = _summarize_request(requests[-1])
        await page.wait_for_timeout(1500)
        shot = out_dir / f"{label.lower()}_transcript.png"
        await sp.screenshot(shot, full_page=False)
        shots.append(shot)

    facts["ui_turn2_reads_image"] = IMAGE_WORD in facts.get("ui_turn2_answer", "").upper()
    facts["ui_turn3_recalls_code_word"] = CODE_WORD in facts.get("ui_turn3_answer", "").upper()
    return shots, facts
