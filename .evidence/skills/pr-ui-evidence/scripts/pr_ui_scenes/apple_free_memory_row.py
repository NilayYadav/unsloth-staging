# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: the GPU device row in Settings -> System, on Apple unified memory.

The row renders `device.vram_free_gb` from /api/system. The training-method picker
reads `gpu.vram_free_gb` from /api/system/hardware. On a Mac those two came from
different arithmetic, so the tab and the picker disagreed on the same screen.

Nothing here is mocked: this photographs whatever the host's real memory is at the
moment of the shot, so the absolute figures drift between the two installs. The
drift-free fact is whether the two endpoints agree with each other.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402

# training-method-hardware-policy.ts: lora when modelSizeGb * 1.5 (ctx 4096) fits.
MODEL_SIZES_GB = (3, 4, 5, 6, 7, 8, 10, 13)


def _method_for(size_gb: float, free_gb: float | None) -> str | None:
    if free_gb is None:
        return None
    return "lora" if size_gb * 1.5 <= free_gb else "qlora"


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph the GPU device row and read both servers' free-VRAM answers."""
    facts: dict = {}

    system = api_get(session, "/api/system")
    hardware = api_get(session, "/api/system/hardware")

    devices = ((system.get("gpu") or {}).get("devices")) or system.get("devices") or []
    device = devices[0] if devices else {}
    tab_free = device.get("vram_free_gb")
    picker_free = (hardware.get("gpu") or {}).get("vram_free_gb")

    facts["backend"] = (hardware.get("gpu") or {}).get("backend")
    facts["device_name"] = device.get("name")
    facts["tab_vram_free_gb"] = tab_free
    facts["tab_vram_used_gb"] = device.get("vram_used_gb")
    facts["tab_memory_total_gb"] = device.get("memory_total_gb")
    facts["picker_vram_free_gb"] = picker_free
    facts["endpoints_agree"] = (
        tab_free is not None
        and picker_free is not None
        and abs(tab_free - picker_free) <= 0.05
    )
    facts["tab_minus_picker_gb"] = (
        round(tab_free - picker_free, 2)
        if tab_free is not None and picker_free is not None
        else None
    )
    facts["training_method_by_model_size_gb"] = {
        str(size): _method_for(size, picker_free) for size in MODEL_SIZES_GB
    }

    init = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(), []
    )
    shots: list[Path] = []
    # Not open_chat: that navigates /chat first on a 30s default, and a Studio
    # serving its bundle for the first time on a loaded box does not make it. The
    # photographed surface is /settings, so go straight there with room to spare.
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        await context.add_init_script(init)
        page = await context.new_page()
        page.set_default_navigation_timeout(180_000)
        page.set_default_timeout(60_000)
        # settings-dialog.tsx puts a data-testid on every tab precisely because the
        # label is translated; the accessible name also carries the tab's icon, so
        # an exact-text role lookup does not match.
        tab = page.locator('[data-testid="settings-tab-resources"]').first
        # A first paint on a box running two Studios and a CI wave can lose a 2 min
        # wait outright. Reloading is cheaper than a longer single deadline, and a
        # dialog that never mounts is a real failure rather than a slow one.
        for attempt in range(3):
            await page.goto(f"{session.base_url}/settings", wait_until="domcontentloaded")
            try:
                await tab.wait_for(state="visible", timeout=90_000)
                break
            except Exception:
                if attempt == 2:
                    raise
                await page.wait_for_timeout(5_000)
        await tab.click(timeout=60_000)

        dialog = page.locator("[role=dialog]").filter(
            has_text=re.compile(r"GPU devices", re.I)
        ).first
        await dialog.wait_for(state="visible", timeout=60_000)

        heading = dialog.get_by_text(re.compile(r"^\s*GPU devices\s*$", re.I)).first
        await heading.wait_for(state="visible", timeout=45_000)
        await heading.scroll_into_view_if_needed()

        # The row itself, not the section: "N.NN GiB free" is the reading under test,
        # and waiting on the section heading alone would pass on an empty pane.
        free_reading = dialog.get_by_text(re.compile(r"[\d.]+\s*GiB free")).first
        await free_reading.wait_for(state="visible", timeout=45_000)
        await page.wait_for_timeout(2_000)

        panel = await dialog.inner_text()
        row = re.search(r"([\d.]+)\s*GiB used.*?([\d.]+)\s*GiB free.*?([\d.]+)\s*GiB total",
                        panel, re.S)
        facts["ui_row_text"] = row.group(0).replace("\n", " ") if row else None
        facts["ui_free_gib"] = float(row.group(2)) if row else None
        facts["ui_used_gib"] = float(row.group(1)) if row else None
        facts["ui_total_gib"] = float(row.group(3)) if row else None

        shot = Path(out_dir) / f"{label}_apple_free_memory_row.png"
        await dialog.screenshot(path=str(shot))
        shots.append(shot)

        await context.close()
        await browser.close()

    return shots, facts
