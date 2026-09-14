"""Scene: a WebP screenshot sent to the GGUF passthrough with tools switched on.

#10094. Studio re-encodes data-URL images to PNG before handing them to
llama-server, whose stb_image decoder reads only a few formats. That conversion
was missing on the body builder used when ``tools`` (or a response format) are
set, so with tools on the WebP bytes went out untouched and llama-server refused
them -- the same screenshot works with tools off.

Nothing is intercepted. Both turns go to the photographed Studio, llama-server
answers for real, and the API monitor is read back to show how each turn was
recorded. The tools-off turn is the control: it must succeed on BOTH sides, or
the model, port or install differs and the pair proves nothing.

The image carries a rendered word rather than a colour because the answer has to
be a decidable fact: a solid colour comes back "white" whatever it is, while the
model reads rendered text back exactly.
"""

from __future__ import annotations

import base64
import io
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

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

WORD = "UNSLOTH"
PROMPT = "What word is written in this image? Answer with the word only."


def _webp_data_url(word: str = WORD, size: int = 448) -> str:
    """A WebP exactly as macOS screenshots and browser pastes produce them."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 90)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    draw.text((size // 2, size // 2), word, fill=(0, 0, 0), anchor="mm", font=font)
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _request(session: Session, path: str, payload: dict, timeout: int = 600):
    req = urllib.request.Request(
        f"{session.base_url}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {session.access_token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()[:6000]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:6000]


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


def _turn(session: Session, model_id: str, image_url: str, tools: list | None):
    payload = {
        "model": model_id,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
            ],
        }],
        "max_tokens": 256,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    return _request(session, "/v1/chat/completions", payload)


def _message(body: str) -> dict:
    try:
        return json.loads(body)["choices"][0]["message"]
    except Exception:  # noqa: BLE001
        return {}


def _finish_reason(body: str) -> str:
    try:
        return json.loads(body)["choices"][0].get("finish_reason") or ""
    except Exception:  # noqa: BLE001
        return ""


def _answer_text(body: str) -> str:
    """What the model said about the image.

    This checkpoint thinks before it answers, so a turn that runs out of budget
    mid-thought leaves ``content`` empty with the word sitting in
    ``reasoning_content``. Both count as "the model saw the image"; only a turn
    that never mentions the word at all is a miss.
    """
    msg = _message(body)
    return " ".join([
        (msg.get("content") or "").strip(),
        (msg.get("reasoning_content") or "").strip(),
    ]).strip()


TOOLS = [{
    "type": "function",
    "function": {
        "name": "record_word",
        "description": "Record the word read from an image.",
        "parameters": {
            "type": "object",
            "properties": {"word": {"type": "string"}},
            "required": ["word"],
        },
    },
}]


async def drive(session: Session, out_dir: Path, label: str, model_path: str = "",
                context_length: int = 4096, **_: object) -> tuple[list[Path], dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    loaded = _load_model(session, model_path, context_length)
    model_id = loaded.get("active_model") or model_path

    image_url = _webp_data_url()

    # Control: the identical WebP with tools OFF already took the converting path
    # before this PR. It must answer on both sides or the pair is incomparable.
    control_status, control_body = _turn(session, model_id, image_url, None)
    # The turn this PR is about: same image, tools switched on.
    tools_status, tools_body = _turn(session, model_id, image_url, TOOLS)

    control_answer = _answer_text(control_body)
    tools_answer = _answer_text(tools_body)
    lowered = tools_body.lower()

    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(),
        [],
    )

    async with open_chat(session.base_url, init_scripts=[auth_script],
                         viewport=(1500, 1000), headless=True) as sp:
        page = sp.page
        await page.goto(f"{session.base_url}/api-monitor", wait_until="domcontentloaded")
        await page.get_by_label("Search API requests").wait_for(state="visible", timeout=60_000)
        await page.wait_for_timeout(4_000)

        body_text = " ".join((await page.locator("body").inner_text()).split())
        section = page.locator("section").filter(
            has=page.get_by_label("Search API requests")
        ).first
        shot = out_dir / f"{label.lower()}_webp_image_with_tools.png"
        await section.screenshot(path=str(shot))

        facts = {
            "model_path": model_path,
            "active_model": model_id,
            "image_mime_sent": "image/webp",
            # Identical on both sides; a move here means the pair is not comparable.
            "control_tools_off_http_status": control_status,
            "control_tools_off_reads_word": bool(re.search(rf"\b{WORD}\b", control_answer.upper())),
            # The measurement.
            "tools_on_http_status": tools_status,
            "tools_on_reads_word": bool(re.search(rf"\b{WORD}\b", tools_answer.upper())),
            "control_tools_off_finish_reason": _finish_reason(control_body),
            "tools_on_finish_reason": _finish_reason(tools_body),
            "tools_on_answer": tools_answer[:160],
            "tools_on_image_error": "image" in lowered and tools_status != 200,
            "tools_on_body_head": tools_body[:300],
            "ui_monitor_shows_error": "error" in body_text.lower(),
            "ui_body_char_count": len(body_text),
        }
        return [shot], facts
