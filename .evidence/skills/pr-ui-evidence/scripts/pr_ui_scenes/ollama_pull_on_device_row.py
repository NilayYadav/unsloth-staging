"""Scene: a model put on disk by a real `ollama pull` in the Hub's On Device list.

PR 10222. The inventory scan withholds any Ollama manifest carrying a layer type
the direct llama.cpp load does not consume, and that list includes
`image.template` and `image.system` -- which a plain `ollama pull` writes for
nearly every model. So the row for a model sitting on disk never appears, with
nothing in the UI saying why.

The store this scene points Studio at holds one real `ollama pull qwen2.5:0.5b`
(manifest: model 397,807,936 B + system 68 B + template 1,482 B + license
11,343 B). It is passed in through OLLAMA_MODELS, which `ollama_model_dirs()`
honours, so both sides read the SAME bytes on disk and differ only in the code
that decides whether to surface them.

The photograph is the On Device list filtered to `qwen`; the numeric half is the
same server's own /api/hub/local, counted by source.
"""

from __future__ import annotations

import asyncio
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

SEARCH_TERM = "qwen"


async def drive(session: Session, out_dir: Path, label: str,
                search_term: str = SEARCH_TERM,
                **_: object) -> tuple[list[Path], dict]:
    """One shot of the On Device list, plus the row counts behind it."""
    facts: dict = {}

    # The numeric half, read from the same server that gets photographed.
    inventory = api_get(session, "/api/hub/local")
    models = inventory.get("models", []) if isinstance(inventory, dict) else []
    ollama_rows = [m for m in models if m.get("source") == "ollama"]
    facts["rows_total"] = len(models)
    facts["rows_ollama"] = len(ollama_rows)
    facts["ollama_display_names"] = sorted(m.get("display_name", "") for m in ollama_rows)
    facts["ollama_load_ids_are_manifest_refs"] = sorted(
        str(m.get("load_id", "")).startswith("ollama-manifest:") for m in ollama_rows
    )
    facts["ollama_tasks"] = sorted({str(m.get("task")) for m in ollama_rows})

    shots: list[Path] = []
    init = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(), []
    )
    async with open_chat(session.base_url, init_scripts=[init],
                         viewport=(1500, 1000), headless=True) as sp:
        page = sp.page
        await page.goto(f"{session.base_url}/hub", wait_until="domcontentloaded")

        on_device = page.get_by_role("radio", name="On Device").first
        await on_device.wait_for(state="visible", timeout=60_000)
        await on_device.click()

        search = page.get_by_placeholder("Search on-device models").first
        await search.wait_for(state="visible", timeout=60_000)
        await search.fill(search_term)

        # The inventory is a filesystem walk over every configured root, and on a box
        # with a populated HF cache it takes minutes. An empty list is the BEFORE state
        # AND what a still-loading list looks like, so a fixed settle photographs
        # "Loading local inventory..." on both sides and proves nothing -- that is
        # exactly what the first run of this scene did. Wait on the spinner itself.
        loading = page.get_by_text(re.compile(r"Loading local inventory", re.I)).first
        try:
            await loading.wait_for(state="visible", timeout=15_000)
        except Exception:  # noqa: BLE001 -- already finished, which is fine
            pass
        await loading.wait_for(state="hidden", timeout=600_000)
        # The list paints its rows after the spinner clears.
        await page.wait_for_timeout(5_000)
        facts["inventory_finished_loading"] = await loading.count() == 0

        # What the list itself reports, so the picture and the fact cannot disagree.
        # Match the OLLAMA TAG, not the word "qwen": a populated HF cache answers a
        # "qwen" search with a dozen Qwen3 safetensors rows on both sides, so the loose
        # match is true either way and discriminates nothing.
        body = " ".join((await page.locator("body").inner_text()).split())
        facts["ollama_tag_on_page"] = bool(re.search(r"qwen2\.5:0\.5b", body, re.I))
        facts["ollama_source_label_on_page"] = bool(re.search(r"\bOllama\b", body))

        shot = out_dir / f"{label.lower()}_0_on_device_ollama.png"
        await page.screenshot(path=str(shot),
                              clip={"x": 340, "y": 96, "width": 1110, "height": 620})
        shots.append(shot)

    return shots, facts


if __name__ == "__main__":
    import argparse

    from pr_ui_scenes._common import studio_session

    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--label", default="MANUAL")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    s = studio_session(a.url, a.home, a.password)
    print(asyncio.run(drive(s, a.out, a.label)))
