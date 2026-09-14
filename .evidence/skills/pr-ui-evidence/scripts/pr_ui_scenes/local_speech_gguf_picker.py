"""Scene: a llama-csm GGUF folder in the on-device list and the chat model picker.

Serves PR 9433. llama.cpp cannot load a `llama-csm` GGUF at all, but before the fix the
inventory had no speech arch, so the folder classified `text-generation`: it was offered in the
chat picker and a pick was handed to llama-server. The fix tags it `text-to-speech`, which the
picker's arch-task gate keeps out of chat, while a runnable sibling folder stays listed.

Cost ladder level 1: two synthetic GGUF headers on disk. No weights, no GPU, no network.
"""

from __future__ import annotations

import os
import re
import struct
import sys
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

SPEECH_FOLDER = "csm-1b-GGUF"
SPEECH_FILE = "csm-1b-Q4_0.gguf"
RUNNABLE_FOLDER = "qwen3-8b-GGUF"
RUNNABLE_FILE = "qwen3-8b-Q4_K_M.gguf"


def _arch_gguf(path: Path, architecture: str) -> None:
    """A minimal GGUF carrying only general.architecture, which is what the scan reads."""

    def string(value: str) -> bytes:
        data = value.encode()
        return struct.pack("<Q", len(data)) + data

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        struct.pack("<IIQQ", 0x46554747, 3, 0, 1)
        + string("general.architecture")
        + struct.pack("<I", 8)
        + string(architecture)
    )


def _seed(home: Path) -> Path:
    """Both folders under THIS install's own home, so the two sides scan identical bytes."""
    root = Path(home) / "pr9433-models"
    _arch_gguf(root / SPEECH_FOLDER / SPEECH_FILE, "llama-csm")
    _arch_gguf(root / RUNNABLE_FOLDER / RUNNABLE_FILE, "llama")
    return root


def _row_task(rows, folder: str):
    for row in rows:
        ident = str(row.get("id") or row.get("path") or row.get("name") or "")
        if folder in ident:
            return row.get("task") or row.get("pipeline_tag")
    return None


async def drive(session: Session, out_dir: Path, label: str,
                **_: object) -> tuple[list[Path], dict]:
    facts: dict = {}
    root = _seed(session.home)
    facts["seeded_root"] = str(root)

    # Register the folder the way the Add folder dialog does, then read the inventory back from
    # the same server that is about to be photographed.
    try:
        api_post(session, "/api/hub/scan-folders", {"path": str(root)}, timeout=300)
    except Exception as exc:  # already registered on a reused home
        facts["scan_folder_note"] = f"{type(exc).__name__}: {exc}"[:200]

    local = api_get(session, "/api/hub/local", timeout=600)
    rows = local.get("models") or local.get("local_models") or []
    facts["local_rows"] = [
        {"id": r.get("id") or r.get("path"), "task": r.get("task") or r.get("pipeline_tag")}
        for r in rows
    ]
    facts["speech_folder_task"] = _row_task(rows, SPEECH_FOLDER)
    facts["runnable_folder_task"] = _row_task(rows, RUNNABLE_FOLDER)

    shots: list[Path] = []
    init = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(), []
    )
    async with open_chat(session.base_url, init_scripts=[init],
                         viewport=(1500, 1000), headless=True) as sp:
        page = sp.page
        await page.goto(f"{session.base_url}/chat", wait_until="domcontentloaded")
        await page.wait_for_timeout(6000)

        # The picker trigger on this build is the composer's empty-state "Select model" button.
        # There is no model-picker-trigger testid and no combobox here, which is why the shared
        # pick_model locator times out, and why a looser button match walks into the left nav.
        trigger = page.get_by_role("button", name=re.compile(r"^\s*Select model\s*$", re.I)).first
        await trigger.click(timeout=30_000)

        # Prove the list opened before photographing it: a miss must fail loudly here rather than
        # produce two clean pictures of a closed composer.
        panel = page.locator("[role=listbox],[role=dialog],[role=menu]").first
        await panel.wait_for(state="visible", timeout=30_000)
        await page.wait_for_timeout(3000)

        panel_text = await panel.inner_text()
        low = panel_text.lower()
        facts["picker_lists_speech_folder"] = "csm-1b" in low
        facts["picker_lists_runnable_folder"] = "qwen3-8b" in low
        facts["picker_text_len"] = len(panel_text)

        shot = Path(out_dir) / f"{label}_model_picker.png"
        await page.screenshot(path=str(shot))
        shots.append(shot)

    return shots, facts
