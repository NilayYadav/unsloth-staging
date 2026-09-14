"""Scene: the agent roster in Settings -> Agents.

Serves PR 10097. The roster is backend-driven: the dropdown maps over whatever
``GET /api/settings/coding-agents`` returns (``CODING_AGENTS`` in
``studio/backend/utils/coding_agents.py``), and the frontend hides ``pi``. The PR
adds ``dsh`` to that tuple and a matching display entry ("DeepSeek Harness") to
``SUPPORTED_AGENTS``, so the row appears without any picker file changing shape.

The control is OpenCode: it is listed on BOTH sides by definition, so a shot that
loses it is a broken click rather than the change being photographed.

Cost ladder level 0: no model, no weights, no GPU, no network.
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
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

NEW_AGENT = "DeepSeek Harness"
CONTROL_AGENT = "OpenCode"
# Display names the build under test knows, so the roster can be counted rather
# than eyeballed. `pi` is deliberately absent: the frontend hides it.
KNOWN_AGENTS = ("Claude Code", "OpenAI Codex", "Hermes Agent", "OpenClaw", "OpenCode", NEW_AGENT)


async def drive(session: Session, out_dir: Path, label: str,
                **_: object) -> tuple[list[Path], dict]:
    facts: dict = {}

    # The numeric half: what the photographed server itself says the roster is.
    info = api_get(session, "/api/settings/coding-agents")
    agents = list(info.get("agents") or [])
    facts["api_agents"] = agents
    facts["api_agent_count"] = len(agents)
    facts["api_lists_dsh"] = "dsh" in agents

    shots: list[Path] = []
    init = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(), []
    )
    async with open_chat(session.base_url, init_scripts=[init],
                         viewport=(1280, 900), headless=True) as sp:
        page = sp.page
        # /settings opens the modal and redirects home. Wait on the Agents tab itself:
        # a bare [role=dialog].first can latch onto the command palette, which mounts
        # hidden and never becomes visible, so the wait times out beside an open dialog.
        await page.goto(f"{session.base_url}/settings", wait_until="domcontentloaded")
        tab = page.get_by_role("button", name=re.compile(r"^\s*Agents\s*$", re.I)).first
        await tab.wait_for(state="visible", timeout=60_000)
        await tab.click(timeout=30_000)

        dialog = page.locator("[role=dialog]").filter(
            has_text=re.compile(r"Coding agent", re.I)
        ).first
        await dialog.wait_for(state="visible", timeout=45_000)
        await page.wait_for_timeout(1_500)

        # The intro paragraph names the supported agents, so it moves with the roster.
        panel = await dialog.inner_text()
        facts["intro_mentions_dsh"] = NEW_AGENT.lower() in panel.lower()

        # "Coding agent" is the roster's own aria-label; the panel holds a second
        # combobox (the GGUF variant), so matching the label keeps this off that one.
        trigger = dialog.get_by_label(re.compile(r"^\s*Coding agent\s*$", re.I)).first
        await trigger.click(timeout=30_000)

        listbox = page.locator("[role=listbox]").first
        await listbox.wait_for(state="visible", timeout=30_000)
        await page.wait_for_timeout(1_500)

        roster = await listbox.inner_text()
        low = roster.lower()
        facts["roster_text"] = roster[:400]
        listed = [name for name in KNOWN_AGENTS if name.lower() in low]
        facts["roster_names"] = listed
        facts["roster_count"] = len(listed)
        facts["ui_lists_deepseek_harness"] = NEW_AGENT.lower() in low
        facts["control_ui_lists_opencode"] = CONTROL_AGENT.lower() in low

        # Full viewport, not the dialog: Radix portals the open listbox outside the
        # dialog node, so a dialog-clipped shot loses the roster being photographed.
        shot = Path(out_dir) / f"{label}_settings_agents_roster.png"
        await page.screenshot(path=shot)
        shots.append(shot)

    return shots, facts
