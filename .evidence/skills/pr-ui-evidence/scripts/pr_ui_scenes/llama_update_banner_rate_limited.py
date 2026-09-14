# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: the llama.cpp update banner while api.github.com is refusing on quota.

Both Studios run with the same sitecustomize stub on PYTHONPATH, so api.github.com
answers 403 with X-RateLimit-Remaining: 0 on BOTH sides and the release page redirect
points at the same tag on BOTH sides. The seeded install marker is one file shared by
both homes. The only thing that differs is the product code, which is the point.

Photographs /api/llama/update-status as the banner renders it: the merge base has no
answer but the API's, so a spent quota leaves latest_tag null and the banner never
mounts; head falls back to the release page redirect and the banner names the build.
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


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    stub_log: str | None = None,
    **_: object,
) -> tuple[list[Path], dict]:
    """Read the update status the banner binds to, then photograph the banner."""
    facts: dict = {}

    status = api_get(session, "/api/llama/update-status?force_refresh=true")
    for key in (
        "installed_tag",
        "latest_tag",
        "update_available",
        "component",
        "source_build",
    ):
        facts[key] = status.get(key)
    facts["banner_should_mount"] = bool(status.get("update_available"))

    # Proves the stub actually answered on this side: a pair where GitHub was reachable
    # on one side and refused on the other is not comparable.
    if stub_log and Path(stub_log).exists():
        lines = Path(stub_log).read_text(encoding="utf-8").splitlines()
        facts["stub_api_403s"] = sum(1 for line in lines if line.startswith("403 "))
        facts["stub_redirects"] = sum(1 for line in lines if line.startswith("redirect "))
    else:
        facts["stub_api_403s"] = None
        facts["stub_redirects"] = None

    init = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(), []
    )
    shots: list[Path] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 760})
        await context.add_init_script(init)
        page = await context.new_page()
        page.set_default_navigation_timeout(180_000)
        page.set_default_timeout(60_000)

        for attempt in range(3):
            await page.goto(f"{session.base_url}/chat", wait_until="domcontentloaded")
            try:
                await page.locator("body").wait_for(state="visible", timeout=90_000)
                break
            except Exception:  # noqa: BLE001 -- a slow first bundle, not a bad selector
                if attempt == 2:
                    raise
                await page.wait_for_timeout(5_000)

        # The banner mounts off an async update check, so give it a real chance to
        # appear on the side that has something to offer. Absence is the BEFORE
        # evidence, so this must be a wait, not a race we happened to win.
        banner = page.get_by_text(re.compile(r"llama\.cpp", re.I)).first
        try:
            await banner.wait_for(state="visible", timeout=45_000)
            facts["banner_visible"] = True
        except Exception:  # noqa: BLE001 -- nothing offered is the expected BEFORE
            facts["banner_visible"] = False
        await page.wait_for_timeout(3_000)

        body_text = await page.locator("body").inner_text()
        offer = re.search(r"[^\n]*llama\.cpp[^\n]*", body_text, re.I)
        facts["banner_text"] = offer.group(0).strip() if offer else None
        facts["page_names_latest_tag"] = bool(
            status.get("latest_tag") and status["latest_tag"] in body_text
        )

        shot = Path(out_dir) / f"{label}_llama_update_banner.png"
        await page.screenshot(path=str(shot))
        shots.append(shot)

        await context.close()
        await browser.close()

    return shots, facts
