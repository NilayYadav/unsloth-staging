"""Scene: the Images viewer mid-denoise (PR 9635).

Before this PR a running generation showed a percentage bar over an empty viewer: the
image only ever appeared once the whole denoise had finished. The PR decodes the
in-flight latents to a small RGB thumbnail every 0.4s and ships it on the progress
endpoint the page already polls, so the viewer fills with a blurry image that sharpens.

The photograph is taken mid-run, so the viewer is showing whatever the PR does or does
not put there. The facts carry the half a picture cannot prove: whether the progress
payload has a `preview` at all, how many DISTINCT frames arrived while the run was in
flight, and how large they are. A blurry JPEG is easy to mistake for a finished image at
a glance -- "the field exists and N different frames came back mid-denoise" is not.
"""

from __future__ import annotations

import asyncio
import json
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
from studio_test_kit.ui import open_chat  # noqa: E402

DEFAULT_REPO = "unsloth/Z-Image-Turbo-GGUF"
DEFAULT_FILE = "z-image-turbo-Q4_K_M.gguf"

_DATA_URL = "data:image/jpeg;base64,"
# Generate / Stop / Generating: one locator that finds the action row on both sides.
_ACTION = re.compile(r"^(Generate|Stop|Generating)", re.I)


# 900s covers a warm cache but not a cold one: Z-Image pulls ~12.9 GB of base components
# the first time, and the driver exited mid-download on the first attempt.
def _load_and_wait(session: Session, body: dict, timeout_s: int = 3600) -> dict:
    api_post(session, "/api/inference/images/unload", {}, timeout=300)
    api_post(session, "/api/inference/images/load", body, timeout=timeout_s)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(5)
        status = api_get(session, "/api/inference/images/status")
        if status.get("loaded"):
            return status
        progress = api_get(session, "/api/inference/images/load-progress")
        if progress.get("error"):
            raise RuntimeError(f"load failed: {str(progress['error'])[:400]}")
        if progress.get("phase") == "downloading":
            print(
                f"  [load] downloading {progress.get('fraction', 0):.1%} "
                f"({progress.get('bytes_downloaded', 0) / 1e9:.1f}/"
                f"{progress.get('bytes_total', 0) / 1e9:.1f} GB)",
                flush = True,
            )
    raise RuntimeError(f"model did not load within {timeout_s}s")


async def _crank(page, index: int, presses: int = 60) -> None:
    """Push a slider to its maximum with the keyboard, which is exact where a drag is not."""
    slider = page.get_by_role("slider").nth(index)
    await slider.click()
    for _ in range(presses):
        await page.keyboard.press("ArrowRight")


async def drive(session: Session, out_dir: Path, label: str,
                repo: str = DEFAULT_REPO, filename: str = DEFAULT_FILE,
                **_: object) -> tuple[list[Path], dict]:
    status = _load_and_wait(session, {"model_path": repo, "gguf_filename": filename,
                                      "model_kind": "gguf"})
    facts: dict = {"loaded": status.get("loaded"), "gguf_variant": status.get("gguf_variant")}

    shots: list[Path] = []
    init = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(), []
    )
    async with open_chat(session.base_url, init_scripts=[init],
                         viewport=(1600, 1000), headless=True) as sp:
        page = sp.page
        await page.goto(f"{session.base_url}/images", wait_until="domcontentloaded")
        await page.wait_for_timeout(8_000)
        # A turbo model at its default step count finishes before the viewer can be
        # photographed mid-denoise, and a finished run is exactly what this scene must
        # not catch. Steps to the maximum buys a run long enough to shoot.
        await _crank(page, 0)
        await page.get_by_role("button", name="Generate", exact=True).first.click()

        # Poll the same endpoint the page polls, collecting DISTINCT frames. Sameness is
        # the trap here: one frame repeated by every poll is not a live preview.
        frames: list[str] = []
        seen: set[str] = set()
        steps_seen: list[int] = []
        shot_taken = False
        shot = out_dir / f"{label.lower()}_denoising.png"
        deadline = time.time() + 180
        while time.time() < deadline:
            await page.wait_for_timeout(700)
            progress = api_get(session, "/api/inference/images/generate-progress")
            if not progress.get("active"):
                if steps_seen:
                    break
                continue
            steps_seen.append(int(progress.get("step") or 0))
            preview = progress.get("preview")
            if isinstance(preview, str) and preview not in seen:
                seen.add(preview)
                frames.append(preview)
            # Shoot once the run is unambiguously under way, so BEFORE and AFTER are
            # photographed at the same point of the same kind of run.
            if not shot_taken and int(progress.get("step") or 0) >= 3:
                await page.screenshot(path=str(shot))
                shots.append(shot)
                shot_taken = True
                facts["shot_at"] = {"step": progress.get("step"),
                                    "total_steps": progress.get("total_steps"),
                                    "fraction": progress.get("fraction")}

        if not shot_taken:
            raise RuntimeError(
                "the run never reached step 3 while active, so nothing was photographed "
                f"mid-denoise. steps_seen={steps_seen[:20]}"
            )

        # Was the field even in the payload? Absent/None on the base, a data URL on the head.
        sample = api_get(session, "/api/inference/images/generate-progress")
        facts["preview_key_in_payload"] = "preview" in sample
        facts["preview_frames_distinct"] = len(frames)
        facts["preview_is_data_url"] = bool(frames) and frames[0].startswith(_DATA_URL)
        facts["preview_bytes_first"] = len(frames[0]) if frames else 0
        facts["preview_bytes_last"] = len(frames[-1]) if frames else 0
        facts["steps_observed"] = steps_seen[:40]
        # The viewer element itself: the <img> the PR renders exists on one side only.
        viewer = page.get_by_role("img", name="Generation preview")
        facts["preview_img_in_dom"] = bool(await viewer.count())

        stop = page.get_by_role("button").filter(has_text=re.compile(r"^\s*Stop\s*$", re.I))
        if await stop.count():
            await stop.last.click()
        waited = 0
        after = api_get(session, "/api/inference/images/generate-progress")
        while after.get("active") and waited < 90:
            await page.wait_for_timeout(4_000)
            waited += 4
            after = api_get(session, "/api/inference/images/generate-progress")

    api_post(session, "/api/inference/images/unload", {}, timeout=300)
    return shots, facts


if __name__ == "__main__":
    import argparse

    from pr_ui_scenes._common import studio_session

    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--label", default="AFTER")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    s = studio_session(a.url, a.home, a.password)
    print(json.dumps(asyncio.run(drive(s, a.out, a.label))[1], indent=2))
