"""Scene: the chat model picker's On Device rows for an mlx-community repo.

Serves PR 10457. The picker called a repo MLX only when its name ended in ``-MLX``.
Almost every MLX build is published under ``mlx-community/`` and does not end that
way, so the row's format pill read "Safetensors" and the MLX format filter returned
nothing. The fix mirrors the backend's ``_looks_like_mlx_repo``.

Two seeded cache repos, photographed together on purpose:
  mlx-community/Qwen3-8B-4bit  the subject -- Safetensors -> MLX
  unsloth/Qwen3-8B             the control -- Safetensors on BOTH sides

The control is what makes the pair evidence rather than a claim: a run that flips
both rows is a broken scene, not a bigger fix.

Cost ladder level 1: a seeded HF cache (config + 512 KB of zeros per repo). No
weights, no GPU, no network. HF_HOME/XDG_CACHE_HOME are pinned by --studio-env so
the rows are the two we seeded and not whatever else this box has downloaded.
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

from pr_ui_scenes._common import Session, api_get  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

SUBJECT = "mlx-community/Qwen3-8B-4bit"
CONTROL = "unsloth/Qwen3-8B"
# The pill sits in the row's meta line, "<format> . <size>". Read the format word for a
# named row rather than counting "MLX" anywhere in the panel: the filter menu also spells
# it, and a substring test over the whole panel would pass on the wrong element.
ROW_META_RE = re.compile(r"(MLX|Safetensors)", re.I)


def _row_format(panel_text: str, repo_id: str) -> str | None:
    """The format word on the line that names `repo_id`, or the line after it."""
    lines = [ln.strip() for ln in panel_text.splitlines()]
    for i, line in enumerate(lines):
        if repo_id.lower() in line.lower() or repo_id.split("/")[-1].lower() in line.lower():
            for candidate in lines[i : i + 3]:
                found = ROW_META_RE.search(candidate)
                if found:
                    return found.group(1)
    return None


def _lists(panel_text: str, repo_id: str) -> bool:
    """Whether the panel renders THIS repo as a row.

    Exact line match, not a substring: the picker draws the leaf on its own line, and
    "Qwen3-8B" is a substring of the subject's "Qwen3-8B-4bit", so a substring test
    reports the unsloth control as listed whenever the mlx-community row is.
    """
    leaf = repo_id.split("/")[-1].lower()
    return any(line.strip().lower() == leaf for line in panel_text.splitlines())


async def _settle(panel, page, timeout_ms: int = 60_000) -> str:
    """Panel text once the list has stopped loading.

    Both sides must be read in the SAME state. The inventory arrives asynchronously, and a
    fixed sleep photographed one side mid-fetch ("Loading models...") while the other had
    settled -- which reads as a difference the PR did not cause.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    text = await panel.inner_text()
    while "loading models" in text.lower() and time.monotonic() < deadline:
        await page.wait_for_timeout(500)
        text = await panel.inner_text()
    if "loading models" in text.lower():
        raise RuntimeError("model list still loading after wait; sides would not be comparable")
    # One more beat so late rows land before the shot.
    await page.wait_for_timeout(1500)
    return await panel.inner_text()


async def drive(session: Session, out_dir: Path, label: str,
                **_: object) -> tuple[list[Path], dict]:
    facts: dict = {}

    # Read the inventory from the SAME server that is about to be photographed, so a fact
    # and its screenshot can never come from different installs.
    cached = api_get(session, "/api/hub/cached-models", timeout=600)
    rows = cached.get("cached") or []
    facts["cached_repo_ids"] = sorted(
        str(r.get("repo_id")) for r in rows if r.get("repo_id")
    )
    facts["subject_is_cached"] = SUBJECT in facts["cached_repo_ids"]
    facts["control_is_cached"] = CONTROL in facts["cached_repo_ids"]

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

        trigger = page.get_by_role(
            "button", name=re.compile(r"^\s*Select model\s*$", re.I)
        ).first
        await trigger.click(timeout=30_000)

        # Prove the list opened before photographing it, so a miss fails loudly here
        # instead of producing two clean pictures of a closed composer.
        panel = page.locator("[role=listbox],[role=dialog],[role=menu]").first
        await panel.wait_for(state="visible", timeout=30_000)
        await page.wait_for_timeout(3000)

        # The picker opens on Recommended, which is a curated Hub list. The cached repos this
        # scene seeded live under On Device, and that tab is the surface the format pill is
        # drawn on, so the shot is worthless without this click.
        on_device = panel.get_by_role("tab", name=re.compile(r"On Device", re.I)).first
        if await on_device.count() == 0:
            on_device = panel.get_by_text(re.compile(r"^\s*On Device\s*$", re.I)).first
        await on_device.click(timeout=30_000)
        await page.wait_for_timeout(4000)

        all_text = await _settle(panel, page)
        facts["on_device_tab_opened"] = "on device" in all_text.lower()
        facts["all_filter_lists_subject"] = _lists(all_text, SUBJECT)
        facts["all_filter_lists_control"] = _lists(all_text, CONTROL)

        # Set the format toggle to MLX. This is the second symptom in the report and it needs
        # no scrolling: the whole list becomes the answer to "which of these are MLX".
        fmt = panel.locator('[aria-label="Filter by format"]').first
        await fmt.click(timeout=30_000)
        await page.wait_for_timeout(1200)
        mlx_option = page.get_by_role("menuitem", name=re.compile(r"^\s*MLX\s*$", re.I)).first
        if await mlx_option.count() == 0:
            mlx_option = page.get_by_role("option", name=re.compile(r"^\s*MLX\s*$", re.I)).first
        await mlx_option.click(timeout=30_000)
        panel_text = await _settle(panel, page)
        facts["mlx_filter_lists_subject"] = _lists(panel_text, SUBJECT)
        facts["mlx_filter_lists_control"] = _lists(panel_text, CONTROL)
        # Row count under the MLX filter: the empty-list symptom is a number, not a vibe.
        facts["mlx_filter_row_count"] = await panel.get_by_role("option").count()
        facts["mlx_filter_text"] = panel_text[:600]
        facts["picker_text_len"] = len(panel_text)

        # Clip to the panel: the rows are ~13px type and a full 1500px frame renders at
        # ~440px per half in a comment, which is not legible.
        box = await panel.bounding_box()
        shot = Path(out_dir) / f"{label}_picker_rows.png"
        if box:
            await page.screenshot(path=str(shot), clip={
                "x": max(box["x"] - 4, 0), "y": max(box["y"] - 4, 0),
                "width": box["width"] + 8, "height": box["height"] + 8,
            })
        else:
            await page.screenshot(path=str(shot))
        shots.append(shot)

    return shots, facts
