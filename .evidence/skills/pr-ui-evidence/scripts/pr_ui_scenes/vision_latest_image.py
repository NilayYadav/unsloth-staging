# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: a two-image vision chat, photographed at the second answer.

#9638. A single-image vision backend (the standard safetensors/MLX path) gets
exactly one image per request, chosen by ``_extract_content_parts`` from the whole
replayed history. The user attaches an image reading ONE and asks what it says,
then attaches an image reading TWO and asks again. The second answer is the
measurement: it names the image the backend actually forwarded, so "ONE" means the
newest attachment never reached the model and "TWO" means it did.

The images carry a word rather than a colour because the answer has to be a
decidable fact. Measured against this checkpoint over the API, a solid colour
field comes back "White" whatever colour it is, while the same model reads
rendered text back exactly -- so the word is what makes the observation trustworthy.
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
from studio_test_kit.ui import open_chat, pick_model, send_prompt, wait_for_stream  # noqa: E402

WORDS = ("ONE", "TWO")


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
    prompt: str = "What word is written in this image? Answer with the word only.",
    load_timeout_ms: int = 900_000,
    turn_timeout_ms: int = 600_000,
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph the second answer of a two-image vision chat."""
    out_dir.mkdir(parents=True, exist_ok=True)
    shots: list[Path] = []
    images = {}
    for word in WORDS:
        p = out_dir / f"{word.lower()}.png"
        _word_png(p, word)
        images[word] = p

    facts: dict = {"model": model, "order": list(WORDS)}

    # Load through the API first: it is the deterministic way to get the weights
    # resident, and it fails loudly here rather than as a timeout inside the picker.
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
        # The API load above already made this the resident model; the picker is a
        # nicety, so a trigger that moved must not fail the run.
        try:
            await pick_model(sp, model, timeout_ms=30_000)
        except Exception as exc:  # noqa: BLE001
            facts["pick_model_note"] = f"picker skipped: {type(exc).__name__}"

        for i, word in enumerate(WORDS, start=1):
            # The composer has no standing file input; the + menu opens a real
            # chooser, so drive that rather than an element that is never there.
            async with page.expect_file_chooser(timeout=60_000) as chooser_info:
                await page.locator(
                    'button[aria-label="Tools and attachments"]').first.click()
                await page.get_by_role(
                    "menuitem", name="Add photos & files").first.click()
            chooser = await chooser_info.value
            await chooser.set_files(str(images[word]))
            # The chip carries the filename, so this asserts THIS turn's image is the
            # attached one -- not merely that some attachment exists.
            await page.locator(
                f'button[aria-label="Image attachment: {word.lower()}.png"]'
            ).first.wait_for(state="visible", timeout=60_000)
            await send_prompt(sp, prompt)
            await wait_for_stream(sp, timeout_ms=turn_timeout_ms)

            # The assistant-ui message body for THIS turn, addressed by index, so the
            # answer is the model's prose and not the surrounding page chrome.
            bodies = page.locator(".aui-assistant-message-content")
            await bodies.nth(i - 1).wait_for(state="visible", timeout=turn_timeout_ms)
            answer = (await bodies.nth(i - 1).inner_text()).strip()
            facts[f"turn{i}_attached"] = word
            facts[f"turn{i}_answer"] = answer[:400]
            facts[f"turn{i}_words_named"] = _words_named(answer)
            shot = out_dir / f"{label.lower()}_turn{i}_{word.lower()}.png"
            await sp.screenshot(shot, full_page=False)
            shots.append(shot)

    named2 = facts.get("turn2_words_named") or []
    # The claim, reduced to one boolean: did the second answer describe the image the
    # user had just attached, or the one that opened the thread?
    facts["turn2_reads_newest_image"] = named2 == ["TWO"]
    facts["turn2_reads_first_image"] = named2 == ["ONE"]
    facts["image_reaching_model_turn2"] = (
        "TWO (newest attachment)" if named2 == ["TWO"]
        else "ONE (first attachment)" if named2 == ["ONE"]
        else f"undecided:{named2}"
    )
    status = api_get(session, "/api/inference/status")
    facts["active_model"] = status.get("active_model")
    facts["is_vision"] = status.get("is_vision")
    return shots, facts
