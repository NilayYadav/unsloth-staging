"""Scene: the Export page's "Est. size" line for a full-finetune run folder.

The folder is a real Studio training output shape under this Studio's own outputs
root: the four Llama 3.1 8B safetensors shards at their Hub byte sizes, beside the
trainer's optimizer.pt (two fp32 AdamW moments), scheduler.pt, training_args.bin,
rng_state.pth and a checkpoint-100/ snapshot. Every file is sparse, so the folder
costs nothing on disk but stats at the real sizes the sizer reads.

The label comes from /api/models/export-size, which the merge base fed with the sum
of every weight-looking file, optimizer state included. The facts are that endpoint's
own bytes and the text the page renders from them.
"""

from __future__ import annotations

import json
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

# Hub tree of meta-llama/Llama-3.1-8B-Instruct, 2026-09-08.
SHARDS = {
    "model-00001-of-00004.safetensors": 4976698672,
    "model-00002-of-00004.safetensors": 4999802720,
    "model-00003-of-00004.safetensors": 4915916176,
    "model-00004-of-00004.safetensors": 1168138808,
}
PARAMS = 8030261248
OPTIMIZER_PT = PARAMS * 2 * 4
CONFIG = {
    "architectures": ["LlamaForCausalLM"],
    "attention_bias": False,
    "attention_dropout": 0.0,
    "bos_token_id": 128000,
    "eos_token_id": [128001, 128008, 128009],
    "hidden_act": "silu",
    "hidden_size": 4096,
    "initializer_range": 0.02,
    "intermediate_size": 14336,
    "max_position_embeddings": 131072,
    "mlp_bias": False,
    "model_type": "llama",
    "num_attention_heads": 32,
    "num_hidden_layers": 32,
    "num_key_value_heads": 8,
    "pretraining_tp": 1,
    "rms_norm_eps": 1e-05,
    "rope_scaling": {
        "factor": 8.0,
        "high_freq_factor": 4.0,
        "low_freq_factor": 1.0,
        "original_max_position_embeddings": 8192,
        "rope_type": "llama3",
    },
    "rope_theta": 500000.0,
    "tie_word_embeddings": False,
    "torch_dtype": "bfloat16",
    "transformers_version": "4.43.0.dev0",
    "use_cache": True,
    "vocab_size": 128256,
}
FOLDER = "Llama-3.1-8B-Instruct-full-finetune-run"


def _sparse(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.truncate(size)


def _seed_run_folder(home: Path) -> Path:
    run = home / "outputs" / FOLDER
    run.mkdir(parents=True, exist_ok=True)
    (run / "config.json").write_text(json.dumps(CONFIG, indent=2))
    for name, size in SHARDS.items():
        _sparse(run / name, size)
    _sparse(run / "optimizer.pt", OPTIMIZER_PT)
    _sparse(run / "scheduler.pt", 1064)
    _sparse(run / "training_args.bin", 5752)
    _sparse(run / "rng_state.pth", 14244)
    _sparse(run / "checkpoint-100" / "model-00001-of-00001.safetensors", sum(SHARDS.values()))
    return run


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    **_: object,
) -> tuple[list[Path], dict]:
    facts: dict = {}
    run = _seed_run_folder(Path(session.home))
    facts["model_dir"] = str(run)
    facts["one_copy_bytes"] = sum(SHARDS.values())
    facts["files_on_disk_bytes"] = sum(
        p.stat().st_size for p in run.rglob("*") if p.is_file()
    )

    from urllib.parse import quote
    size = api_get(session, f"/api/models/export-size?model={quote(str(run))}", timeout=300)
    facts["api_fp16_bytes"] = size.get("fp16_bytes")
    facts["api_source"] = size.get("source")
    facts["api_fp16_gib"] = (
        round(size["fp16_bytes"] / 1024**3, 2) if size.get("fp16_bytes") else None
    )

    init = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(), []
    )
    shots: list[Path] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        await context.add_init_script(init)
        page = await context.new_page()
        page.set_default_navigation_timeout(180_000)
        page.set_default_timeout(60_000)

        tab = page.get_by_role("tab", name="Local Model")
        for attempt in range(3):
            await page.goto(f"{session.base_url}/export", wait_until="domcontentloaded")
            try:
                await tab.first.wait_for(state="visible", timeout=90_000)
                break
            except Exception:
                if attempt == 2:
                    raise
                await page.wait_for_timeout(5_000)
        await tab.first.click()

        box = page.get_by_placeholder("./models/my-model")
        await box.first.wait_for(state="visible", timeout=180_000)
        await box.first.fill(str(run))
        await box.first.press("Enter")

        # Merged: the label is the fp16 bytes themselves, no quant scaling in the way.
        await page.get_by_role("button").filter(
            has_text=re.compile(r"Merged Model")
        ).first.click()

        est = page.get_by_text(re.compile(r"Est\. size:"))
        await est.first.wait_for(state="visible", timeout=120_000)
        # The label re-renders when the size request resolves; settle on a stable text.
        last = ""
        for _ in range(20):
            text = " ".join((await est.first.inner_text()).split())
            if text == last and "~" in text:
                break
            last = text
            await page.wait_for_timeout(1_000)
        facts["ui_est_size_text"] = last
        match = re.search(r"~\s*([\d.]+)\s*(GB|MB|TB)", last)
        facts["ui_est_size_value"] = f"{match.group(1)} {match.group(2)}" if match else None

        await est.first.scroll_into_view_if_needed()
        await page.wait_for_timeout(1_000)
        shot = Path(out_dir) / f"{label.lower()}_export_size_one_copy.png"
        await page.screenshot(path=str(shot))
        shots.append(shot)

        await context.close()
        await browser.close()

    return shots, facts
