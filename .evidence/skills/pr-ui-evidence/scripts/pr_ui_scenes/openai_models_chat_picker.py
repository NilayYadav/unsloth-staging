# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: which model Settings -> API keys names in its OpenAI usage examples.

Both sides are pointed at the SAME isolated HF cache holding exactly one repo: the
downloaded image GGUF ``unsloth/Z-Image-Turbo-GGUF``. Nothing is loaded, so the panel
falls back to the first entry ``GET /v1/models`` returns.

The merge base has no notion of a task on that endpoint, so a downloaded image GGUF is
advertised exactly like a chat model and the examples tell the user to POST it to
``/v1/chat/completions`` -- which llama.cpp cannot serve. The head build tags it
``text-to-image`` and the settings panel keeps non-chat entries out of the pick.
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

# Tabs are restored from localStorage, so seeding the key opens /settings straight
# on API keys instead of General.
TAB_SCRIPT = """
(() => {
  try {
    localStorage.setItem("unsloth_settings_active_tab", "api-keys");
  } catch (e) {}
})();
"""

# What the frontend treats as chat-capable. An entry with no task is a pre-PR server.
CHAT_TASKS = {"text-generation", "text-to-speech"}


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    *,
    expect_repo: str = "unsloth/Z-Image-Turbo-GGUF",
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph the usage-example snippets and record what /v1/models advertised."""
    # With nothing resident the panel names a model only when "Switch model by request"
    # is on, so turn it on for BOTH sides: otherwise the examples say "no model to name
    # yet" either way and the shot proves nothing.
    request = urllib.request.Request(
        f"{session.base_url}/api/settings/openai-auto-switch",
        data=json.dumps({"enabled": True}).encode(),
        headers={
            "Authorization": f"Bearer {session.access_token}",
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        auto_switch = json.loads(response.read()).get("enabled")

    listing = api_get(session, "/v1/models")
    rows = [
        {
            "id": entry.get("id"),
            "task": entry.get("task"),
            "loaded": bool(entry.get("loaded")),
            "quant": entry.get("quant"),
        }
        for entry in (listing.get("data") or [])
    ]
    chat_rows = [r for r in rows if r["task"] is None or r["task"] in CHAT_TASKS]

    auth_script = seed_init_script(
        type(
            "A",
            (),
            {
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
            },
        )(),
        [],
    )

    async with open_chat(
        session.base_url,
        init_scripts=[auth_script, TAB_SCRIPT],
        viewport=(1500, 1000),
        headless=True,
    ) as sp:
        page = sp.page
        # The command palette is a mounted-but-closed dialog, so filter to the settings one
        # rather than taking .first, which waits forever on something hidden.
        dialog = page.locator('[data-slot="dialog-content"]').filter(
            has_text=re.compile("Manage your Unsloth preferences", re.I)
        ).first
        # A first-run home can land on onboarding and swallow the deep link, so retry it
        # rather than waiting out one 60s timeout on a dialog that was never opened.
        for attempt in range(4):
            await page.goto(f"{session.base_url}/settings", wait_until="domcontentloaded")
            try:
                await dialog.wait_for(state="visible", timeout=20_000)
                break
            except Exception:
                if attempt == 3:
                    raise
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(2_000)
        # The seeded tab key opens the dialog straight on API, which is where the examples
        # live; assert the panel itself rather than trusting the dialog opened at all.
        examples = dialog.get_by_text(re.compile("usage example", re.I)).first
        await examples.wait_for(state="visible", timeout=60_000)
        # The catalog arrives from /v1/models after mount; let the snippet settle.
        await page.wait_for_timeout(5_000)
        await examples.scroll_into_view_if_needed()
        await page.wait_for_timeout(1_000)

        panel_text = await dialog.inner_text()
        # The model id the examples actually tell the user to send.
        named = re.findall(r'"model"\s*:\s*"([^"]+)"', panel_text)
        named += re.findall(r'model\s*=\s*"([^"]+)"', panel_text)
        named_models = sorted(set(named))

        shot = out_dir / f"{label.lower()}_openai_usage_example_model.png"
        await dialog.screenshot(path=str(shot))

        facts = {
            "auto_switch_enabled": auto_switch,
            "v1_models_row_count": len(rows),
            "v1_models_rows": rows,
            "chat_picker_row_count": len(chat_rows),
            "chat_picker_row_ids": [r["id"] for r in chat_rows],
            "image_repo_listed_at_all": any(r["id"] == expect_repo for r in rows),
            "image_repo_task": next(
                (r["task"] for r in rows if r["id"] == expect_repo), None
            ),
            "image_repo_offered_for_chat": any(r["id"] == expect_repo for r in chat_rows),
            "usage_example_model_ids": named_models,
            "usage_example_names_image_repo": any(
                m.split(":")[0] == expect_repo for m in named_models
            ),
        }
        return [shot], facts
