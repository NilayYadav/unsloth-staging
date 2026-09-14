"""Scene: a GGUF that Hermes Desktop downloaded, in Studio's chat model picker.

Hermes stages one-click downloads as flat files in ``<hermes root>/models`` with the
vision projector under ``models/assets/``. The scene seeds exactly that layout under an
isolated ``HERMES_HOME`` (passed to BOTH Studios via ``--studio-env``), opens the chat
picker's On device tab, and reports whether the model is listed -- from the same server
it photographs, via ``/api/hub/local``.

BEFORE (merge base) Studio scans the HF caches, LM Studio and Ollama only, so the row
is absent. AFTER (head) it is listed under Custom Folders like an Ollama row, and the
inventory reports it with ``source == "hermes"``.
"""

from __future__ import annotations

import asyncio
import os
import re
import struct
import sys
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

DEFAULT_MODEL = "Qwen3.8-27B-UD-Q4_K_M.gguf"
DEFAULT_MMPROJ = "mmproj-Qwen3.8-27B-BF16.gguf"
# Hermes' catalog ships this one as four parts; the row must point at part one and be
# sized as the whole set.
DEFAULT_SPLIT = "Qwen3.8-Flash-Next-UD-Q4_K_XL"
SPLIT_PADS = (1000, 2000, 3000, 4000)
_GGUF_MAGIC = 0x46554747


def _minimal_gguf(path: Path, fields: dict[str, str], pad: int = 0) -> None:
    """A real GGUF header with only ``general.*`` strings, so Studio's metadata readers
    see a well-formed file rather than choking on zero bytes. ``pad`` gives the parts of
    a split distinct sizes, so a size read back is attributable to one part or the set."""
    body = b""
    for key, value in fields.items():
        kb, vb = key.encode(), value.encode()
        body += struct.pack("<Q", len(kb)) + kb + struct.pack("<I", 8)
        body += struct.pack("<Q", len(vb)) + vb
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<IIQQ", _GGUF_MAGIC, 3, 0, len(fields)) + body + b"\x00" * pad)


def seed_hermes(root: Path, model: str, mmproj: str, split: str = DEFAULT_SPLIT) -> dict:
    """Idempotent: both sides seed the same bytes at the same paths."""
    models = root / "models"
    _minimal_gguf(models / model, {
        "general.architecture": "qwen3",
        "general.name": Path(model).stem,
        "general.type": "model",
    })
    _minimal_gguf(models / "assets" / mmproj, {
        "general.architecture": "clip",
        "general.name": Path(mmproj).stem,
        "general.type": "mmproj",
    })
    parts = []
    for index, pad in enumerate(SPLIT_PADS, start=1):
        part = models / f"{split}-{index:05d}-of-{len(SPLIT_PADS):05d}.gguf"
        _minimal_gguf(part, {
            "general.architecture": "qwen3",
            "general.name": split,
            "general.type": "model",
        }, pad=pad)
        parts.append(part)
    return {
        "hermes_root": str(root),
        "seeded": sorted(str(p.relative_to(root)) for p in models.rglob("*.gguf")),
        "split_parts_on_disk_bytes": sum(p.stat().st_size for p in parts),
    }


async def drive(session: Session, out_dir: Path, label: str,
                hermes_root: str = "", model: str = DEFAULT_MODEL,
                mmproj: str = DEFAULT_MMPROJ, split: str = DEFAULT_SPLIT,
                **_: object) -> tuple[list[Path], dict]:
    if not hermes_root:
        raise RuntimeError("hermes_root kwarg is required (also pass it as HERMES_HOME "
                           "via --studio-env so the launched Studio resolves the same root)")
    facts: dict = seed_hermes(Path(hermes_root), model, mmproj, split)
    stem = Path(model).stem

    # The numeric half, from the server we photograph.
    try:
        data = api_get(session, "/api/hub/local")
        rows = data.get("models", [])
        hermes_rows = [r for r in rows if r.get("source") == "hermes"]
        facts["hermes_dirs_scanned"] = data.get("hermes_dirs")
        facts["hermes_row_count"] = len(hermes_rows)
        facts["hermes_rows"] = [
            {"display_name": r.get("display_name"), "source": r.get("source"),
             "model_format": r.get("model_format"), "path": r.get("path"),
             "size_bytes": r.get("size_bytes")}
            for r in hermes_rows
        ]
        facts["stem_listed_by_any_source"] = any(
            stem in (r.get("display_name") or "") for r in rows)
        # The split lists once, by its first part, sized as the whole set.
        split_rows = [r for r in hermes_rows if (r.get("display_name") or "") == split]
        facts["split_row_count"] = len(split_rows)
        facts["split_row_path_is_part_one"] = bool(split_rows) and (
            split_rows[0].get("path") or "").endswith("-00001-of-00004.gguf")
        facts["split_row_size_bytes"] = split_rows[0].get("size_bytes") if split_rows else None
    except Exception as exc:  # noqa: BLE001 -- the screenshot is still worth taking
        facts["api_error"] = f"{type(exc).__name__}: {exc}"
    # The legacy endpoint behind the recipe picker, the chat auto-load and /v1/models.
    try:
        compat = api_get(session, "/api/models/local")
        facts["compat_hermes_dirs"] = compat.get("hermes_dirs")
        facts["compat_hermes_row_count"] = sum(
            1 for r in compat.get("models", []) if r.get("source") == "hermes")
    except Exception as exc:  # noqa: BLE001
        facts["compat_api_error"] = f"{type(exc).__name__}: {exc}"

    shots: list[Path] = []
    init = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(), []
    )
    async with open_chat(session.base_url, init_scripts=[init],
                         viewport=(1440, 1000), headless=True) as sp:
        page = sp.page
        # The picker opens from the header's "Select model" button (aria-haspopup=dialog),
        # not from the composer; the kit's form-scoped testid predates this layout.
        trigger = page.get_by_role("button", name=re.compile(r"select model|^model$", re.I))
        if await trigger.count() == 0:
            trigger = page.locator('button[aria-haspopup="dialog"]')
        await trigger.first.wait_for(state="visible", timeout=60_000)
        await trigger.first.click()

        tab = page.get_by_role("tab", name=re.compile(r"on device", re.I))
        if await tab.count() == 0:
            tab = page.get_by_role("button", name=re.compile(r"^on device$", re.I))
        await tab.first.click(timeout=30_000)
        # The local list is filled from /api/hub/local; wait for the scan itself, not a clock.
        loading = page.get_by_text(re.compile(r"loading models", re.I))
        for _ in range(90):
            if await loading.count() == 0:
                break
            await page.wait_for_timeout(1_000)
        await page.wait_for_timeout(1_500)
        facts["picker_still_loading"] = (await loading.count()) > 0

        heading = page.get_by_text("Custom Folders", exact=True)
        facts["custom_folders_section_visible"] = (await heading.count()) > 0
        model_text = page.get_by_text(stem, exact=False)
        facts["picker_shows_model"] = (await model_text.count()) > 0
        facts["picker_shows_split"] = (await page.get_by_text(split, exact=False).count()) > 0
        # Photograph the same region on both sides: the Custom Folders section, where the
        # rows land, rather than the top of a list that the box's ambient caches fill.
        target = model_text if facts["picker_shows_model"] else heading
        if await target.count() > 0:
            await target.first.scroll_into_view_if_needed()
            await page.wait_for_timeout(500)

        shot = out_dir / f"{label.lower()}_hermes_picker.png"
        out_dir.mkdir(parents=True, exist_ok=True)
        # Fixed viewport, no full_page: both sides stay the same size for the composite.
        await page.screenshot(path=str(shot), full_page=False)
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
    ap.add_argument("--label", default="AFTER")
    ap.add_argument("--hermes-root", required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    s = studio_session(a.url, a.home, a.password)
    print(asyncio.run(drive(s, a.out, a.label, hermes_root=a.hermes_root)))
