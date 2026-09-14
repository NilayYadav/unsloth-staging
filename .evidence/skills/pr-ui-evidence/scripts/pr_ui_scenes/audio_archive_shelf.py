"""Scene: the Audio page's clip row menu and the Data tab's archive shelves (PR 9637).

The PR gives audio clips the archive/restore flow images and videos already have. The
visible half is two rows appearing: an "Archive" item in a clip's row menu, and an
"Archived audio" row in Settings -> Data.

No weights, no GPU and no TTS call: clips are seeded straight into `<home>/audio` as the
WAV + JSON sidecar pair `save()` writes, so both sides photograph the same two clips and
the only thing that differs is the separately built frontend and backend.

The numeric half is the PATCH route the menu item drives. BEFORE, `/api/inference/audio/
gallery/{id}` accepts only GET and DELETE, so archiving is not merely hidden but absent;
AFTER it answers 200 and the clip moves off History onto the archived shelf.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

# Distinct enough to locate by, and to read in the screenshot.
KEEP_PROMPT = "A sloth reads the morning news"
SHELVE_PROMPT = "A sloth hums while the kettle boils"


def _wav_bytes(seconds: float = 1.0, rate: int = 24_000) -> bytes:
    """A real (silent) mono 16-bit WAV, so the row's <audio> element has something valid."""
    frames = int(seconds * rate)
    data = b"\x00\x00" * frames
    return (
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", len(data)) + data
    )


def _seed_clips(home: Path) -> list[dict]:
    """Write the pairs `save()` would write, newest last, and return their records.

    Seeded on disk rather than generated: the surface under test is the gallery row, and
    a TTS call would put a model download and a GPU between the scene and the thing it is
    photographing. Any pre-existing pair is cleared so the two sides start identical.
    """
    directory = home / "audio"
    directory.mkdir(parents=True, exist_ok=True)
    for stale in list(directory.glob("*.wav")) + list(directory.glob("*.json")):
        stale.unlink()
    for stale in directory.glob(".flags.json*"):
        stale.unlink()

    seeded = []
    for index, prompt in enumerate((SHELVE_PROMPT, KEEP_PROMPT)):
        audio_id = uuid.uuid4().hex
        meta = {
            "prompt": prompt,
            "model": "unsloth/orpheus-3b-0.1-ft",
            "audio_type": "snac",
            "sample_rate": 24_000,
            "duration_s": 1.0,
            "created_at": f"2026-08-2{index}T00:00:00Z",
        }
        (directory / f"{audio_id}.wav").write_bytes(_wav_bytes())
        (directory / f"{audio_id}.json").write_text(json.dumps(meta), encoding="utf-8")
        # mtime is the gallery's sort key, so stamp it rather than trust write order.
        os.utime(directory / f"{audio_id}.wav", (1_700_000_000 + index, 1_700_000_000 + index))
        seeded.append({"id": audio_id, **meta})
    return seeded


def _patch_flags(session: Session, audio_id: str) -> int:
    """PATCH the clip's archive flag and return the HTTP status.

    The status IS the fact: BEFORE the route does not exist, so this is the difference
    between "the button is hidden" and "there is nothing behind the button".
    """
    request = urllib.request.Request(
        f"{session.base_url}/api/inference/audio/gallery/{audio_id}",
        data=json.dumps({"archived": True}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {session.access_token}",
        },
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _count(session: Session, archived: bool) -> int:
    data = api_get(session, f"/api/inference/audio/gallery?offset=0&limit=50"
                            f"&archived={'true' if archived else 'false'}")
    return len(data.get("audio", []))


async def drive(session: Session, out_dir: Path, label: str,
                **_: object) -> tuple[list[Path], dict]:
    facts: dict = {}
    seeded = _seed_clips(Path(session.home))
    facts["seeded_clips"] = len(seeded)
    facts["history_count"] = _count(session, archived=False)

    shots: list[Path] = []
    init = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(), []
    )
    async with open_chat(session.base_url, init_scripts=[init],
                         viewport=(1500, 1000), headless=True) as sp:
        page = sp.page

        # --- shot 1: the clip row menu -------------------------------------------------
        await page.goto(f"{session.base_url}/audio", wait_until="domcontentloaded")
        trigger = page.get_by_label(f"Actions for {SHELVE_PROMPT}")
        await trigger.first.wait_for(state="attached", timeout=60_000)
        # The row hides its trigger until hover, and the menu loses races with the
        # gallery's own refresh, so retry the TRIGGER rather than stretch the item wait.
        for attempt in range(6):
            await trigger.first.scroll_into_view_if_needed()
            await trigger.first.hover()
            await page.wait_for_timeout(400)
            await trigger.first.click()
            try:
                await page.locator('[role="menuitem"]').first.wait_for(
                    state="visible", timeout=4_000)
                break
            except Exception:  # noqa: BLE001 -- a lost race, not a bad selector
                if attempt == 5:
                    raise
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(1_000)

        items = [t.strip() for t in await page.locator('[role="menuitem"]').all_inner_texts()]
        facts["menu_items"] = items
        facts["menu_item_count"] = len(items)
        facts["has_archive_item"] = any(i.lower() == "archive" for i in items)
        shot = out_dir / f"{label.lower()}_clip_row_menu.png"
        await page.screenshot(path=str(shot))
        shots.append(shot)
        await page.keyboard.press("Escape")

        # --- the route behind the item -------------------------------------------------
        facts["patch_flags_status"] = _patch_flags(session, seeded[0]["id"])
        facts["history_after_archive"] = _count(session, archived=False)
        facts["archived_shelf_count"] = _count(session, archived=True)

        # --- shot 2: the Data tab's archive shelves ------------------------------------
        await page.goto(f"{session.base_url}/settings", wait_until="domcontentloaded")
        dialog = page.get_by_role("dialog").first
        await dialog.wait_for(state="visible", timeout=60_000)
        await dialog.get_by_role("tab", name="Data").or_(
            dialog.get_by_role("button", name="Data")).first.click(timeout=30_000)
        # Assert the tab actually switched: a click that misses leaves the default tab
        # and photographs a panel with no archive rows on EITHER side.
        await dialog.get_by_text("Archived images", exact=False).first.wait_for(
            state="visible", timeout=30_000)
        row = dialog.get_by_text("Archived audio", exact=False)
        facts["has_archived_audio_row"] = await row.count() > 0
        if facts["has_archived_audio_row"]:
            await row.first.scroll_into_view_if_needed()
        else:
            await dialog.get_by_text("Archived videos", exact=False).first \
                .scroll_into_view_if_needed()
        await page.wait_for_timeout(500)
        shot = out_dir / f"{label.lower()}_data_tab_archives.png"
        await page.screenshot(path=str(shot))
        shots.append(shot)

    return shots, facts
