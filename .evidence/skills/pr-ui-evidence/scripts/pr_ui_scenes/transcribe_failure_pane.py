"""Scene: the Transcribe pane right after a second file fails (PR 9816).

Transcribe one file, then transcribe a second one that fails. Merge base leaves the
first file's transcript on screen while `transcribedName` has already moved to the
second file, so Copy and Download .txt export A's words under B's name and the only
sign of failure is a toast that clears itself.

No weights and no GPU: the sidecar status and the transcription endpoint are both
served from the browser, so each side photographs the same two requests and the
separately built frontend is the only variable. The failing response is held open
long enough to photograph the spinner, which is what proves the pane and the export
name have already diverged.
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

# Resident on the sidecar for both sides. large-v3-turbo round-trips through
# sttRepoIdForSidecarKey -> unsloth/whisper-large-v3-turbo, which is what makes
# reconcileSttSelection adopt it and sttSelectionReady go true with no picker click.
SIDECAR_KEY = "large-v3-turbo"

GOOD_FILE = "quarterly-review.wav"
BAD_FILE = "interview-clip.wav"

# Long enough to read in a 1440px screenshot, and unmistakably about the FIRST file.
GOOD_TEXT = (
    "Revenue for the quarter came in at four point two million, "
    "ahead of the three point eight we guided to."
)
BAD_DETAIL = "Model failed to decode the audio stream (invalid frame header)."

# The 500 is held this long so the spinner naming the SECOND file can be photographed
# while the pane still holds the FIRST file's words.
FAIL_HOLD_S = 2.5


def _wav_bytes(seconds: float = 1.0, rate: int = 16_000) -> bytes:
    """A real (silent) mono 16-bit WAV, so the picker accepts it as audio/*."""
    frames = int(seconds * rate)
    data = b"\x00\x00" * frames
    return (
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", len(data)) + data
    )


async def _pane_text(page) -> str:
    """The transcript pane's text, never the controls column beside it.

    Both carry .hover-scrollbar, and the controls column is first in the DOM, so an
    index or a .first reads the Microphone field and reports an empty transcript on
    both sides -- an identical pair that looks like "the PR changed nothing". Only
    the controls column holds the Microphone field, so exclude by that.
    Toasts render in a portal outside both, which is what keeps a toast-only failure
    from counting as a failure shown in the pane.
    """
    panes = page.locator("div.hover-scrollbar")
    for index in range(await panes.count()):
        text = " ".join((await panes.nth(index).inner_text()).split())
        if "Microphone" not in text:
            return text
    return ""


def _stt_status() -> dict:
    """A sidecar holding large-v3-turbo under the Transformers engine."""
    engine = {
        "available": True,
        "installed": True,
        "loaded_model": SIDECAR_KEY,
        "loading": False,
        "device": "cuda:0",
        "keep_alive_seconds": 300,
        "default_model": SIDECAR_KEY,
        "models": ["tiny", "base", "small", "large-v3-turbo", "large-v3"],
        "downloaded": [SIDECAR_KEY],
    }
    return {**engine, "transformers": dict(engine), "gguf": {**engine, "available": False,
                                                             "loaded_model": None}}


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph the Transcribe pane after a good file and then a failing one."""
    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(),
        [],
    )
    wav = _wav_bytes()
    calls: list[str] = []

    async with open_chat(
        session.base_url,
        init_scripts=[auth_script],
        viewport=(1440, 950),
        headless=True,
    ) as sp:
        page = sp.page

        async def serve_status(route):
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps(_stt_status()))

        async def serve_transcribe(route):
            calls.append(route.request.url)
            if len(calls) == 1:
                await route.fulfill(status=200, content_type="application/json",
                                    body=json.dumps({"text": GOOD_TEXT}))
                return
            # Held open so the spinner naming BAD_FILE can be photographed against a
            # pane that, on the merge base, still holds GOOD_FILE's words.
            await asyncio.sleep(FAIL_HOLD_S)
            await route.fulfill(status=500, content_type="application/json",
                                body=json.dumps({"detail": BAD_DETAIL}))

        await page.route("**/api/inference/audio/stt/status*", serve_status)
        await page.route("**/api/inference/audio/transcribe/raw*", serve_transcribe)
        # Nothing here loads or unloads weights; keep those verbs off the real server.
        await page.route("**/api/inference/audio/stt/load*",
                         lambda r: r.fulfill(status=200, content_type="application/json",
                                             body=json.dumps({"ok": True})))
        await page.route("**/api/inference/audio/stt/unload*",
                         lambda r: r.fulfill(status=200, content_type="application/json",
                                             body=json.dumps({"ok": True})))

        await page.goto(f"{session.base_url}/audio", wait_until="domcontentloaded")

        # PillTabs keeps WAI-ARIA tab roles for keyboard nav, so this is role="tab",
        # not a button; get_by_role("button") waits out its whole timeout here.
        tab = page.get_by_role("tab", name="Transcribe", exact=True)
        await tab.wait_for(state="visible", timeout=90_000)
        await tab.click()

        # Enabled only once the stubbed residency has been reconciled into a selection,
        # so this doubles as the assertion that sttSelectionReady went true.
        file_input = page.locator('input[type="file"][accept="audio/*"]')
        await file_input.wait_for(state="attached", timeout=60_000)
        for _ in range(60):
            if await file_input.is_enabled():
                break
            await page.wait_for_timeout(500)
        transcribe_enabled = await file_input.is_enabled()

        # --- 1. the good file ------------------------------------------------
        await file_input.set_input_files(
            {"name": GOOD_FILE, "mimeType": "audio/wav", "buffer": wav})
        await page.get_by_text(GOOD_TEXT[:40], exact=False).first.wait_for(
            state="visible", timeout=60_000)
        first_pane_text = await _pane_text(page)
        shot_good = out_dir / f"{label.lower()}_1_first_transcript.png"
        await page.screenshot(path=str(shot_good))

        # --- 2. the failing file --------------------------------------------
        await file_input.set_input_files(
            {"name": BAD_FILE, "mimeType": "audio/wav", "buffer": wav})
        # Mid-flight: the spinner has already moved to BAD_FILE. Whether the pane
        # under it still holds GOOD_FILE's words is the whole bug.
        await page.get_by_text(f"Transcribing {BAD_FILE}", exact=False).first.wait_for(
            state="visible", timeout=30_000)
        inflight_text = await _pane_text(page)
        shot_inflight = out_dir / f"{label.lower()}_2_second_file_inflight.png"
        await page.screenshot(path=str(shot_inflight))
        stale_while_running = GOOD_TEXT[:40] in inflight_text

        # Settled: the 500 has landed and busy has cleared.
        await page.get_by_text(f"Transcribing {BAD_FILE}", exact=False).first.wait_for(
            state="hidden", timeout=60_000)
        await page.wait_for_timeout(1_000)
        final_pane_text = await _pane_text(page)
        shot_failed = out_dir / f"{label.lower()}_3_after_failure.png"
        await page.screenshot(path=str(shot_failed))

        copy_button = page.get_by_role("button", name="Copy", exact=True)
        download_button = page.get_by_role("button", name="Download .txt", exact=False)
        has_copy = await copy_button.count() > 0 and await copy_button.first.is_visible()
        has_download = (await download_button.count() > 0
                        and await download_button.first.is_visible())

        # The reported symptom, measured: what the export would actually be called and
        # what it would actually contain, after the run that failed.
        download_name = None
        download_text = None
        if has_download:
            try:
                async with page.expect_download(timeout=20_000) as caught:
                    await download_button.first.click()
                download = await caught.value
                download_name = download.suggested_filename
                saved = out_dir / f"{label.lower()}_export{Path(download_name).suffix}"
                await download.save_as(str(saved))
                download_text = " ".join(saved.read_text(encoding="utf-8").split())
            except Exception as exc:  # noqa: BLE001 - recorded, not fatal
                download_name = f"<capture failed: {type(exc).__name__}>"

        facts = {
            "transcribe_enabled_without_weights": transcribe_enabled,
            "transcribe_requests": len(calls),
            "good_file": GOOD_FILE,
            "bad_file": BAD_FILE,
            "first_pane_holds_good_text": GOOD_TEXT[:40] in first_pane_text,
            "spinner_named_bad_file": f"Transcribing {BAD_FILE}" in inflight_text,
            "stale_transcript_while_second_runs": stale_while_running,
            "stale_transcript_after_failure": GOOD_TEXT[:40] in final_pane_text,
            "shows_failure_in_pane": "Could not transcribe" in final_pane_text,
            "names_failed_file_in_pane": BAD_FILE in final_pane_text
                                         and "Could not transcribe" in final_pane_text,
            "shows_error_detail_in_pane": BAD_DETAIL[:30] in final_pane_text,
            "shows_placeholder": "The transcript appears here" in final_pane_text,
            "copy_button_after_failure": has_copy,
            "download_button_after_failure": has_download,
            "export_filename_after_failure": download_name,
            "export_holds_other_files_words": (
                bool(download_text) and GOOD_TEXT[:40] in download_text),
            "final_pane_text": final_pane_text[:400],
        }
        return [shot_good, shot_inflight, shot_failed], facts
