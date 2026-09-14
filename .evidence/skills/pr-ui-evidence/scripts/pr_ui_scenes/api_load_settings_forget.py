# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: the "Settings applied on API load" panel, and whether a row can be removed.

Two overrides are seeded through the photographed Studio's own overrides route, so the
rows on screen and the numbers in `facts` come from that server rather than a fixture.
One is keyed by a local folder path, which is the case issue #10159 describes: the folder
is gone, so the model has left the picker and its settings page can never be reached
again. The other carries the counts whose plural the panel used to get wrong.

The scene then tries to forget the folder-keyed row and reads the panel and the server
back, so the shot pair is "the panel" and "the panel after a forget was attempted".
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

OVERRIDES = "/api/settings/openai-auto-switch/overrides"
# A repo the picker still lists, carrying every count whose plural the panel got wrong.
REPO_KEY = "unsloth/Qwen3-4B-GGUF:Q4_K_M"
# The deleted custom folder from the issue: it cannot come back under this id, so its
# settings page is unreachable and nothing but this panel can drop the entry.
FOLDER_KEY = "/home/santiago/Temp-GGUF/qwen38/UD-IQ3_XXS:UD-IQ3_XXS"


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


def _seed(session: Session) -> list[str]:
    for key in (REPO_KEY, FOLDER_KEY):
        _put(session, {"model_id": key, "remove": True})
    _put(session, {
        "model_id": REPO_KEY,
        "mirrors_server_tuning": True,
        "n_parallel": 1,
        "ctx_checkpoints": 1,
        "gpu_layers": 1,
        "n_cpu_moe": 1,
    })
    _put(session, {
        "model_id": FOLDER_KEY,
        "mirrors_server_tuning": True,
        "n_parallel": 4,
        "max_seq_length": 32768,
    })
    return sorted(api_get(session, OVERRIDES).get("overrides", {}))


async def _read_rows(panel) -> list[dict]:
    rows = panel.locator("li")
    out = []
    for i in range(await rows.count()):
        spans = rows.nth(i).locator("span")
        out.append({
            "key": (await spans.nth(0).inner_text()).strip(),
            "summary": (await spans.nth(1).inner_text()).strip(),
        })
    return out


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph the panel, try to forget one row, photograph it again."""
    seeded = _seed(session)
    init = seed_init_script(
        type("A", (), {
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
        })(),
        [],
    )
    async with open_chat(
        session.base_url,
        init_scripts=[init],
        viewport=(1200, 900),
        headless=True,
    ) as sp:
        page = sp.page
        await page.goto(f"{session.base_url}/api-monitor", wait_until="domcontentloaded")
        heading = page.get_by_role("heading", name="Settings applied on API load")
        await heading.wait_for(state="visible", timeout=60_000)
        panel = page.locator("section").filter(has=heading).first
        # The rows arrive with the fetch, not with the heading; without this the first
        # shot is of two skeletons on both sides and the pair proves nothing.
        await panel.locator("li").first.wait_for(state="visible", timeout=30_000)
        await panel.get_by_text(FOLDER_KEY, exact=True).wait_for(
            state="visible", timeout=30_000
        )
        await page.wait_for_timeout(500)

        rows = await _read_rows(panel)
        forget = panel.get_by_role("button", name=f"Forget settings for {FOLDER_KEY}")
        forget_buttons = await panel.get_by_role(
            "button", name="Forget settings for", exact=False
        ).count()
        await heading.scroll_into_view_if_needed()
        shot_panel = out_dir / f"{label.lower()}_api_load_settings_panel.png"
        await panel.screenshot(path=str(shot_panel))

        forgot = False
        if forget_buttons:
            await forget.first.click()
            # The row goes only once the refetch lands, so wait on the row, not the click.
            await panel.get_by_text(FOLDER_KEY, exact=True).wait_for(
                state="detached", timeout=30_000
            )
            forgot = True
        await page.wait_for_timeout(500)

        rows_after = await _read_rows(panel)
        shot_after = out_dir / f"{label.lower()}_api_load_settings_after_forget.png"
        await panel.screenshot(path=str(shot_after))

    server_after = sorted(api_get(session, OVERRIDES).get("overrides", {}))
    facts = {
        "seeded_server_keys": seeded,
        "forget_buttons": forget_buttons,
        "forget_clicked": forgot,
        "rows_before": [r["key"] for r in rows],
        "row_summary_repo": next(
            (r["summary"] for r in rows if r["key"] == REPO_KEY), None
        ),
        "row_summary_folder": next(
            (r["summary"] for r in rows if r["key"] == FOLDER_KEY), None
        ),
        "rows_after_forget": [r["key"] for r in rows_after],
        "server_keys_after_forget": server_after,
        "folder_entry_still_applies": FOLDER_KEY in server_after,
    }
    return [shot_panel, shot_after], facts
