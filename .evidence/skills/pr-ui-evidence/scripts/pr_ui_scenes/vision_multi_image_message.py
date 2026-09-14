# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: two images attached to ONE message, photographed at the outcome.

#9719. The composer's file input is ``multiple``, so a single turn can carry
several images, but the standard safetensors/MLX path forwards exactly one.
Attach an image reading ONE and an image reading TWO to the SAME message and ask
for every word. The answer is the measurement: naming only ONE means the second
attachment was dropped and nothing said so, and a refusal naming the one-image
limit means the request was rejected instead.

The images carry a word rather than a colour for the reason ``vision_latest_image``
records: this checkpoint reads rendered text back exactly and answers "White" for
any solid colour field.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat, pick_model, send_prompt  # noqa: E402

WORDS = ("ONE", "TWO")
REFUSAL = "one image per message"


def _word_png(path: Path, word: str, size: int = 448) -> None:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 200)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    draw.text((size // 2, size // 2), word, fill=(0, 0, 0), anchor="mm", font=font)
    img.save(path)


def _words_named(text: str) -> list[str]:
    up = text.upper()
    return [w for w in WORDS if re.search(rf"\b{w}\b", up)]


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    *,
    model: str = "unsloth/Qwen2-VL-2B-Instruct",
    prompt: str = "Which words are written in these images? List every word you can see.",
    load_timeout_ms: int = 900_000,
    turn_timeout_ms: int = 600_000,
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph what one two-image message produces."""
    out_dir.mkdir(parents=True, exist_ok=True)
    shots: list[Path] = []
    images = []
    for word in WORDS:
        p = out_dir / f"{word.lower()}.png"
        _word_png(p, word)
        images.append(p)

    facts: dict = {"model": model, "attached_words": list(WORDS)}

    # Load through the API first: the deterministic way to get the weights resident,
    # and it fails loudly here rather than as a timeout inside the picker.
    api_post(session, "/api/inference/load", {"model_path": model},
             timeout=load_timeout_ms // 1000)
    deadline = time.time() + load_timeout_ms / 1000
    while time.time() < deadline:
        st = api_get(session, "/api/inference/status")
        if st.get("active_model") == model and not st.get("loading"):
            break
        time.sleep(3)
    else:
        raise RuntimeError(f"{model} never became resident on {session.base_url}")

    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(),
        [],
    )

    async with open_chat(
        session.base_url,
        init_scripts=[auth_script],
        viewport=(1400, 1000),
        headless=True,
    ) as sp:
        page = sp.page
        await page.locator("form:has(textarea) textarea").first.wait_for(
            state="visible", timeout=120_000)
        try:
            await pick_model(sp, model, timeout_ms=30_000)
        except Exception as exc:  # noqa: BLE001
            facts["pick_model_note"] = f"picker skipped: {type(exc).__name__}"

        # Both files through ONE chooser: this is the composer's own `multiple`
        # input, i.e. the flow that produces a single message carrying two images.
        async with page.expect_file_chooser(timeout=60_000) as chooser_info:
            await page.locator('button[aria-label="Tools and attachments"]').first.click()
            await page.get_by_role("menuitem", name="Add photos & files").first.click()
        chooser = await chooser_info.value
        await chooser.set_files([str(p) for p in images])

        # Named chips, so this asserts BOTH images are on the turn about to be sent
        # rather than that some attachment exists.
        for word in WORDS:
            await page.locator(
                f'button[aria-label="Image attachment: {word.lower()}.png"]'
            ).first.wait_for(state="visible", timeout=60_000)
        facts["attachments_on_turn"] = await page.locator(
            'button[aria-label^="Image attachment:"]').count()

        composed = out_dir / f"{label.lower()}_composer_two_images.png"
        await sp.screenshot(composed, full_page=False)
        shots.append(composed)

        await send_prompt(sp, prompt)

        # Either outcome ends the turn: an answer, or the refusal toast. Poll for
        # whichever arrives so the toast is still on screen when it is photographed.
        toast = page.locator("[data-sonner-toast]")
        bodies = page.locator(".aui-assistant-message-content")
        stop = page.locator('button[aria-label="Stop generating"], button:has-text("Stop")')
        answer, toast_text = "", ""
        deadline = time.time() + turn_timeout_ms / 1000
        while time.time() < deadline:
            if await toast.count():
                toast_text = " ".join((await toast.first.inner_text()).split())
                break
            if await bodies.count() and not await stop.count():
                text = (await bodies.first.inner_text()).strip()
                if text:
                    answer = text
                    break
            await page.wait_for_timeout(500)

        facts["answer"] = answer[:400]
        facts["words_named"] = _words_named(answer)
        facts["toast_text"] = toast_text[:400]
        facts["refused"] = REFUSAL in toast_text.lower()
        facts["assistant_answered"] = bool(answer)

        shot = out_dir / f"{label.lower()}_two_image_turn.png"
        await sp.screenshot(shot, full_page=False)
        shots.append(shot)

    named = facts["words_named"]
    facts["outcome"] = (
        f"refused: {facts['toast_text']}" if facts["refused"]
        else f"answered naming {named or 'no listed word'}, both images sent"
    )
    facts["silently_dropped_an_image"] = bool(answer) and named != list(WORDS)
    status = api_get(session, "/api/inference/status")
    facts["active_model"] = status.get("active_model")
    facts["is_vision"] = status.get("is_vision")
    return shots, facts
