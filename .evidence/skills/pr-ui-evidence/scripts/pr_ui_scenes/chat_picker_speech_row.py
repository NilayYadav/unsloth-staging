"""Scene: a downloaded voice model in the chat model picker's on-device list.

Serves PR 9813. A TTS checkpoint is an ordinary causal LM by architecture -- Orpheus is
``LlamaForCausalLM`` -- so the inventory classified it chat-capable. That is the flag the
chat picker filters its on-device list on AND the flag chat auto-load reads when it picks
the smallest downloaded model, and the chat route answers a turn on a speech model by
SYNTHESIZING the prompt rather than refusing it. The fix reads the codec vocabulary in
``tokenizer_config.json``, so the row reports ``can_chat`` false and leaves the chat list.

The control folder beside it is the same architecture with an ordinary vocabulary: it must
stay listed on both sides, or the shot proves only that something broke.

Cost ladder level 1: two small model directories on disk. No weights, no GPU, no network.
"""

from __future__ import annotations

import json
import os
import re
import struct
import sys
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

VOICE_FOLDER = "my-voice-orpheus"
CONTROL_FOLDER = "my-chat-llama"
# Matches both seeded folders and nothing else on the box.
SEARCH_PREFIX = "my-"

# Past the codebook threshold the classifier uses, and carrying the stray <|audio|> a real
# Orpheus ships beside its codes -- the token that made the generic audio-input pattern win
# before the codec fingerprints were ordered ahead of it.
VOICE_TOKENS = ["<|audio|>"] + [f"<custom_token_{i}>" for i in range(10_002)]
CONTROL_TOKENS = ["<bos>", "<eos>", "<pad>"]


def _safetensors(path: Path) -> None:
    """A structurally valid, tensor-less safetensors file: the scan weighs and names these,
    it never opens them, and a stub keeps the folder off the cost ladder."""
    header = json.dumps({"__metadata__": {"format": "pt"}}).encode()
    path.write_bytes(struct.pack("<Q", len(header)) + header + b"\0" * 4096)


def _model_dir(root: Path, name: str, tokens: list[str]) -> Path:
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text(
        json.dumps({"model_type": "llama", "architectures": ["LlamaForCausalLM"]}),
        encoding="utf-8",
    )
    (path / "tokenizer_config.json").write_text(
        json.dumps(
            {"added_tokens_decoder": {str(i): {"content": t} for i, t in enumerate(tokens)}}
        ),
        encoding="utf-8",
    )
    _safetensors(path / "model.safetensors")
    return path


def _seed(home: Path) -> Path:
    """Under THIS install's own home, so the two sides scan identical bytes and neither
    can see the other's copy."""
    root = Path(home) / "pr9813-models"
    _model_dir(root, VOICE_FOLDER, VOICE_TOKENS)
    _model_dir(root, CONTROL_FOLDER, CONTROL_TOKENS)
    return root


def _row(rows, folder: str):
    for row in rows:
        ident = str(row.get("id") or row.get("path") or row.get("display_name") or "")
        if folder in ident:
            return row
    return None


def _summary(row) -> dict:
    if row is None:
        return {"listed_by_api": False}
    caps = row.get("capabilities") or {}
    return {
        "listed_by_api": True,
        "can_chat": caps.get("can_chat"),
        "model_format": row.get("model_format"),
        "source": row.get("source"),
        "task": row.get("task") or row.get("pipeline_tag"),
    }


async def drive(session: Session, out_dir: Path, label: str,
                **_: object) -> tuple[list[Path], dict]:
    facts: dict = {}
    root = _seed(session.home)
    facts["seeded_root"] = str(root)

    # Registered the way the Add folder dialog does, then read back from the same server
    # that is about to be photographed.
    try:
        api_post(session, "/api/hub/scan-folders", {"path": str(root)}, timeout=300)
    except Exception as exc:  # already registered on a reused home
        facts["scan_folder_note"] = f"{type(exc).__name__}: {exc}"[:200]

    local = api_get(session, "/api/hub/local", timeout=600)
    rows = local.get("models") or local.get("local_models") or []
    facts["voice_row"] = _summary(_row(rows, VOICE_FOLDER))
    facts["control_row"] = _summary(_row(rows, CONTROL_FOLDER))

    shots: list[Path] = []
    init = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(), []
    )
    async with open_chat(session.base_url, init_scripts=[init],
                         viewport=(1500, 1000), headless=True) as sp:
        page = sp.page
        await page.goto(f"{session.base_url}/chat", wait_until="domcontentloaded")
        await page.wait_for_timeout(8000)

        # The composer's empty-state trigger. There is no model-picker testid on this build,
        # and a looser button match walks into the left nav.
        trigger = page.get_by_role("button", name=re.compile(r"^\s*Select model\s*$", re.I)).first
        await trigger.click(timeout=30_000)

        # Prove the list opened before photographing it: a miss must fail loudly here rather
        # than produce two clean pictures of a closed composer.
        panel = page.locator("[role=listbox],[role=dialog],[role=menu]").first
        await panel.wait_for(state="visible", timeout=30_000)
        await page.wait_for_timeout(3000)

        # The picker opens on Recommended, which is the Hub catalogue and holds nothing of
        # ours. On Device is the tab the seeded folders land in, and the first run of this
        # scene photographed Recommended on both sides and called it "no change".
        on_device = panel.get_by_role("tab", name=re.compile(r"On Device", re.I)).first
        if await on_device.count() == 0:
            on_device = panel.get_by_role("button", name=re.compile(r"On Device", re.I)).first
        await on_device.click(timeout=30_000)
        # The inventory read is what fills this tab, so wait for the control folder to
        # arrive rather than a fixed pause. The control is listed on BOTH sides by
        # definition, so waiting on it cannot mask the difference being photographed.
        try:
            await panel.get_by_text(
                re.compile(re.escape(CONTROL_FOLDER), re.I)
            ).first.wait_for(state="visible", timeout=45_000)
            facts["on_device_ready"] = True
        except Exception as exc:  # noqa: BLE001 -- recorded, then photographed anyway
            facts["on_device_ready"] = False
            facts["on_device_note"] = f"{type(exc).__name__}: {exc}"[:200]
        await page.wait_for_timeout(2000)

        # Filter to the two seeded folders. Without this the list is whatever else is on
        # the box, both folders sort below the fold, and the two shots came back
        # byte-identical while inner_text -- which reads the scrolled-away rows too --
        # already showed the row leaving. The pair has to be visible, not merely true.
        search = panel.get_by_placeholder(re.compile(r"Search local models", re.I)).first
        await search.fill(SEARCH_PREFIX, timeout=30_000)
        await page.wait_for_timeout(2500)

        panel_text = await panel.inner_text()
        facts["filtered_text"] = panel_text[:400]
        low = panel_text.lower()
        facts["picker_lists_voice"] = VOICE_FOLDER in low
        facts["picker_lists_control"] = CONTROL_FOLDER in low
        facts["picker_text_len"] = len(panel_text)

        shot = Path(out_dir) / f"{label}_chat_model_picker.png"
        # Clipped to the panel: at a full 1500px viewport the rows are 12px type and the
        # greeting beside them is randomised, which reads as a difference that is not one.
        # The seeded folder's absolute path is masked -- it names this box, and it is the
        # one string in frame that must not be published.
        await page.screenshot(
            path=str(shot),
            clip={"x": 290, "y": 36, "width": 505, "height": 340},
            mask=[panel.get_by_text(re.compile(re.escape(str(root)))).first],
            mask_color="#d9dee6",
        )
        shots.append(shot)

    return shots, facts
