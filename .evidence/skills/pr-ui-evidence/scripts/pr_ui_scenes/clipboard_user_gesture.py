"""Scene: what the Copy preview link click does before it copies anything.

Serves PR 9814. The reported bug is Safari's: a clipboard write is only honoured
while the click that asked for it still holds transient activation, and the pre-PR
handler spent that budget on `await fetchDeviceType({force: true})` first. The
denial itself is a real-Safari behaviour and is NOT reproducible under Playwright's
WebKit -- measured on this box, a write 9s after the click still resolves there, in
both the Studio origin and a bare page. So this scene does not pretend to
photograph a denied write. It photographs the await that causes it.

What is measured is the round trip the click makes before it copies, which is the
whole mechanism and is fully visible: health is answered instantly while the app
boots (both sides settle with a tunnel URL in the platform store) and delayed by 6s
for anything asked afterwards. Six seconds is not a contrived number --
`fetchDeviceType` itself polls that endpoint for up to HARDWARE_DETECT_WAIT_MS =
5000ms while the backend is still measuring hardware. The shot is taken at a fixed
1500ms after the click, which is the difference a user sees: a toast, or nothing
yet.

`copied_is_tunnel_link` is the control for the other half of the fix. Dropping the
click-time refresh must not cost the store its tunnel URL, or the button would
start copying a link only this machine can open.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))
# The Studio kit is owned by pr-repro-ci; pr_ui_diff.py adds this, a direct run does not.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "pr-repro-ci" / "scripts"))

from playwright.async_api import async_playwright  # noqa: E402

from pr_ui_scenes._common import Session  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402

# What a tunnelled Studio reports. The point of the button is a link someone ELSE can
# open, so the copied URL has to be this and not 127.0.0.1.
TUNNEL_URL = "https://pr9814-evidence.trycloudflare.com"
PREVIEW_REF = "unsloth/Qwen3-1.7B/run-9814"
PREVIEW_SIG = "s1gn4tur3-9814"
EXPECTED_URL = f"{TUNNEL_URL}/p/{PREVIEW_REF}?k={PREVIEW_SIG}"

# Inside what fetchDeviceType will itself wait out on a backend still measuring.
SLOW_HEALTH_MS = 6000
# When the picture is taken. Long enough that a click which copies straight away has
# its toast up, short enough that a click still on the network does not.
SHOT_AFTER_CLICK_MS = 1500

# Reads what the app asked to copy without changing whether the write is allowed:
# WebKit refuses navigator.clipboard.readText() outside a gesture, so the value
# cannot be read back afterwards.
RECORD_COPIES = """
(() => {
  const clip = navigator.clipboard;
  if (!clip || typeof clip.writeText !== "function") return;
  const original = clip.writeText.bind(clip);
  window.__copiedText = [];
  clip.writeText = (text) => { window.__copiedText.push(text); return original(text); };
})();
"""

RUN = {
    "id": "run-9814",
    "status": "completed",
    "model_name": "unsloth/Qwen3-1.7B",
    "project_name": None,
    "dataset_name": "yahma/alpaca-cleaned",
    "display_name": "Safari clipboard evidence run",
    "started_at": "2026-08-26T10:00:00Z",
    "ended_at": "2026-08-26T10:12:00Z",
    "total_steps": 60,
    "final_step": 60,
    "final_loss": 0.7421,
    "output_dir": "/tmp/outputs/run-9814",
    "can_resume": False,
    "resumed_later": False,
    "artifacts_available": True,
    "has_preview_model": True,
    "preview_ref": PREVIEW_REF,
    "preview_sig": PREVIEW_SIG,
    "duration_seconds": 720,
    "error_message": None,
    "loss_sparkline": [1.9, 1.5, 1.2, 1.0, 0.9, 0.82, 0.76, 0.74],
}


async def drive(session: Session, out_dir: Path, label: str,
                **_: object) -> tuple[list[Path], dict]:
    facts: dict = {
        "engine": "webkit",
        "slow_health_ms": SLOW_HEALTH_MS,
        "shot_after_click_ms": SHOT_AFTER_CLICK_MS,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    shots: list[Path] = []

    init = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(), []
    )

    # Flipped once the app has booted. Until then health answers at once, so both
    # builds start from the same settled store -- including the tunnel URL, which the
    # AFTER build must still be holding when the click lands.
    state = {"slow": False, "health_calls": 0, "health_after_click": 0, "clicked": False}

    async with async_playwright() as p:
        browser = await p.webkit.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1500, "height": 1000})
        await context.add_init_script(init)
        await context.add_init_script(RECORD_COPIES)

        async def health(route) -> None:
            state["health_calls"] += 1
            if state["clicked"]:
                state["health_after_click"] += 1
            # The real reply, so device_type/chat_only/secure stay this install's own
            # answers and only the tunnel fields are ours.
            try:
                body = await (await route.fetch()).json()
            except Exception:  # noqa: BLE001 -- an unauthenticated boot probe
                body = {}
            # Only into a reply that is actually authed. `version` is the authed-only
            # marker env.ts itself keys on, and env.ts now treats the presence of the
            # tunnel fields as proof a body came back authenticated -- injecting them
            # into the unauthenticated boot reply would hide that distinction from the
            # very code under test.
            if isinstance(body, dict) and "version" in body:
                body["cloudflare_url"] = TUNNEL_URL
                body.setdefault("server_url", session.base_url)
            if state["slow"]:
                await asyncio.sleep(SLOW_HEALTH_MS / 1000)
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps(body))

        async def runs(route) -> None:
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps({"runs": [RUN], "total": 1}))

        await context.route("**/api/health*", health)
        await context.route("**/api/train/runs?*", runs)

        page = await context.new_page()
        await page.goto(f"{session.base_url}/studio", wait_until="domcontentloaded")

        # The History tab is component state, not a route, so it has to be clicked.
        history = page.get_by_role("tab", name="History")
        await history.wait_for(state="visible", timeout=90_000)
        await history.click()

        copy = page.get_by_role("button", name="Copy preview link")
        await copy.wait_for(state="visible", timeout=90_000)
        facts["copy_button_visible"] = True

        # Everything the app wanted at boot has landed; from here health is slow.
        await page.wait_for_timeout(1_500)
        facts["health_calls_at_boot"] = state["health_calls"]
        state["slow"] = True
        state["clicked"] = True

        toast = page.locator("text=/Preview link copied|Couldn't copy the link/").first
        started = time.monotonic()
        await copy.click()

        # The picture, at a fixed moment for both sides.
        await page.wait_for_timeout(SHOT_AFTER_CLICK_MS)
        facts["toast_visible_at_shot"] = await toast.is_visible()
        shot = out_dir / f"{label.lower()}_copy_preview_link.png"
        await page.screenshot(path=str(shot))
        shots.append(shot)

        # Then let it finish, whenever it does.
        await toast.wait_for(state="visible", timeout=60_000)
        facts["click_to_toast_ms"] = int((time.monotonic() - started) * 1000)
        facts["toast"] = (await toast.inner_text()).strip()
        facts["copy_succeeded"] = facts["toast"].startswith("Preview link copied")
        facts["health_calls_after_click"] = state["health_after_click"]

        copied = await page.evaluate("() => window.__copiedText || []")
        facts["copied_url"] = copied[-1] if copied else None
        facts["copied_is_tunnel_link"] = facts["copied_url"] == EXPECTED_URL

        await context.close()
        await browser.close()

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
    print(asyncio.run(drive(s, a.out, a.label)))
