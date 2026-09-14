"""Scene: the run-settings panel of a downloaded but UNLOADED GGUF (issue #10168).

A real quant is seeded into an isolated HF cache both Studios read, so the chat picker
lists it with its "Inference settings" gear. The scene opens that panel, changes one
setting, ticks "Remember for this model", and reads the panel's footer buttons. If a
"Save settings" button exists it is clicked, and the server's API-load overrides and the
loaded-model list are read back from the same Studio that was photographed.

Cost ladder level 2: one 240 MB GGUF on disk, no GPU, no load.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

REPO = "unsloth/gemma-3-270m-it-GGUF"
QUANT = "UD-Q4_K_XL"
FILE = f"gemma-3-270m-it-{QUANT}.gguf"
KEY = f"{REPO}:{QUANT}"
# Substring of the picker row, as playwright_model_config.py uses: the row label is not
# reliably the bare repo id.
HINT = "gemma-3-270m"
OVERRIDES = "/api/settings/openai-auto-switch/overrides"
FOOTER_RE = re.compile(r"^(Reset|Save settings|Forget settings|Load model|Reload model)$")


def _put(session: Session, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{session.base_url}{OVERRIDES}",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {session.access_token}",
        },
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def _seed(hf_home: Path) -> str:
    from huggingface_hub import hf_hub_download

    return hf_hub_download(REPO, FILE, cache_dir=str(hf_home / "hub"))


async def _footer_buttons(page) -> list[str]:
    names = []
    buttons = page.get_by_role("button", name=FOOTER_RE)
    for i in range(await buttons.count()):
        if await buttons.nth(i).is_visible():
            names.append((await buttons.nth(i).inner_text()).strip())
    return names


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    hf_home: str,
    **_: object,
) -> tuple[list[Path], dict]:
    facts: dict = {"seeded_file": _seed(Path(hf_home))}
    # Clear EVERY override, not just the seeded key. Which quant the panel opens is decided
    # by the picker's expander, so a key-specific pre-clean can miss the row this run is
    # about -- and these homes are reused between runs, so a leftover row from an earlier
    # run then shows up as "override_after false -> true" that this run's save did not
    # cause. The home is disposable, so clearing all of them costs nothing and makes the
    # delta mean what it says.
    try:
        for stale in sorted(api_get(session, OVERRIDES).get("overrides", {})):
            _put(session, {"model_id": stale, "remove": True})
    except Exception as exc:  # noqa: BLE001 -- nothing to remove on a fresh home
        facts["reset_note"] = f"{type(exc).__name__}: {exc}"[:120]
    facts["override_before_keys"] = sorted(api_get(session, OVERRIDES).get("overrides", {}))

    init = seed_init_script(
        type("A", (), {
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
        })(),
        [],
    )
    shots: list[Path] = []
    async with open_chat(
        session.base_url, init_scripts=[init], viewport=(1400, 1000), headless=True
    ) as sp:
        page = sp.page
        await page.goto(f"{session.base_url}/chat", wait_until="domcontentloaded")
        trigger = page.get_by_role(
            "button", name=re.compile(r"^\s*Select model\s*$", re.I)
        ).first
        await trigger.click(timeout=60_000)
        panel = page.locator("[role=listbox],[role=dialog],[role=menu]").first
        await panel.wait_for(state="visible", timeout=30_000)
        await panel.get_by_text("On Device", exact=True).first.click(timeout=30_000)
        # Anchored regex, not the full repo id, and never a click on the row -- both
        # mirror tests/studio/playwright_model_config.py's row_gear:
        #   * the gear's accessible name is "<row label> <quant>", and the row label is
        #     not always the bare repo id, so an exact "Inference settings for <repo>
        #     <quant>" can miss a gear that is right there;
        #   * a sole-quant repo renders as a collapsed row only once an async probe
        #     lands, so the wait has to be the probe's, not a UI paint's;
        #   * clicking the row to reveal the gear SELECTS the model and closes the
        #     picker, which is fatal here: this scene's whole claim is that the model
        #     is never loaded.
        quant_gear = page.get_by_role(
            "button",
            name=re.compile(
                rf"^Inference settings for .*{re.escape(HINT)}.*{re.escape(QUANT)}$",
                re.I,
            ),
        )
        repo_gear = page.get_by_role(
            "button",
            name=re.compile(rf"^Inference settings for .*{re.escape(HINT)}", re.I),
        )
        gear = quant_gear
        try:
            await gear.first.wait_for(state="visible", timeout=30_000)
        except Exception:
            gear = repo_gear
            await gear.first.wait_for(state="visible", timeout=30_000)
        label_text = await gear.first.get_attribute("aria-label")
        facts["gear_label"] = label_text
        # Which quant actually opened, not which one was seeded. The picker lists the
        # repo's catalogue quants and the expander orders them by fit, so the repo-only
        # fallback can legitimately open a different one -- and then a storage check keyed
        # to the seeded quant reads "nothing was saved" for a save that worked perfectly.
        # The claim under test is "saved without loading", not "saved under UD-Q4_K_XL",
        # so the override lookup follows the panel that was actually driven.
        opened_quant = (label_text or "").rsplit(" ", 1)[-1].strip() if label_text else ""
        facts["opened_quant"] = opened_quant
        facts["opened_quant_is_seeded"] = opened_quant == QUANT
        await gear.first.click()

        remember = page.get_by_label("Remember for this model")
        await remember.wait_for(state="visible", timeout=30_000)
        # The panel re-derives its baseline once mount-time work lands and drops whatever was
        # staged before that, so an edit made in its first moments is silently discarded and
        # the save reports "Default settings kept." with nothing stored. Measured at 500ms in
        # tests/studio/playwright_model_config.py, which waits 1000; the panel exposes no
        # readiness signal to poll, so this is a bounded wait rather than a condition.
        await page.wait_for_timeout(1_200)

        # Context Length first, not Parallel decode slots. Slots lives behind the Advanced
        # toggle and is not offered for every quant the picker may open, which made the staged
        # edit -- the thing that makes this a non-default save at all -- depend on which row
        # the expander chose. Context Length is on the panel unconditionally.
        staged = None
        ctx = page.get_by_label("Context Length")
        if await ctx.count() and await ctx.first.is_visible():
            await ctx.first.click()
            await ctx.first.fill("4096")
            await ctx.first.press("Tab")
            staged = "customContextLength=4096"
        else:
            slots = page.get_by_label("Parallel decode slots")
            if not await slots.is_visible():
                await page.get_by_label("Show advanced settings").click()
                await slots.wait_for(state="visible", timeout=15_000)
            await slots.fill("2")
            await slots.press("Tab")
            staged = "nParallel=2"
        facts["staged_setting"] = staged
        if not await remember.is_checked():
            await remember.click()
        await page.wait_for_timeout(800)

        facts["footer_buttons"] = await _footer_buttons(page)
        save = page.get_by_role("button", name=re.compile(r"^Save settings$"))
        facts["save_buttons"] = await save.count()
        await remember.scroll_into_view_if_needed()
        shot_panel = out_dir / f"{label.lower()}_run_settings_panel.png"
        await page.screenshot(path=str(shot_panel))
        shots.append(shot_panel)

        facts["save_clicked"] = False
        facts["toast"] = None
        if facts["save_buttons"]:
            await save.first.click()
            toast = page.get_by_text(re.compile(r"Settings saved\.|Couldn't save"))
            await toast.first.wait_for(state="visible", timeout=15_000)
            facts["toast"] = (await toast.first.inner_text()).strip()
            facts["save_clicked"] = True
        await page.wait_for_timeout(800)
        facts["footer_buttons_after"] = await _footer_buttons(page)
        shot_after = out_dir / f"{label.lower()}_run_settings_after_save.png"
        await page.screenshot(path=str(shot_after))
        shots.append(shot_after)

    overrides = api_get(session, OVERRIDES).get("overrides", {})
    active_key = f"{REPO}:{opened_quant}" if opened_quant else KEY
    facts["override_key"] = active_key
    facts["override_after"] = active_key in overrides
    facts["override_row"] = overrides.get(active_key)
    # Say what else is there, so "no row" is distinguishable from "row under another key".
    facts["override_keys_present"] = sorted(overrides)
    status = api_get(session, "/api/inference/status")
    facts["loaded_after"] = status.get("loaded")
    facts["active_model_after"] = status.get("active_model")
    return shots, facts
