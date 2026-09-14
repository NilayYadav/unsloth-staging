"""Scene: issue #9146 under the reported low-VRAM projector pressure.

Both exact-SHA Studio installs load the same PaddleOCR GGUF pair while an external
ballast keeps GPU 0 near 0.8 GiB free. The merge base silently recovers text-only
and rejects an image attachment. The PR head must retry with
``--no-mmproj-offload``, retain vision, report ``cpu_offload``, and accept the
same image.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

DEFAULT_MODEL_DIR = Path(
    os.environ.get(
        "UNSLOTH_MM_PROJ_MODEL_DIR",
        Path.home() / ".cache" / "unsloth-pr-skills" / "pr9173-model",
    )
)
DEFAULT_MODEL_FILE = "PaddleOCR-VL-1.6-GGUF.gguf"


def _free_mib() -> int | None:
    proc = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.free",
            "--format=csv,noheader,nounits",
            "--id=0",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        return int(proc.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def _input_image(path: Path) -> None:
    image = Image.new("RGB", (640, 360), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((24, 24, 616, 336), outline="black", width=5)
    draw.text((74, 150), "PR 9173 VISION INPUT", fill="black")
    image.save(path)


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    model_dir: str = str(DEFAULT_MODEL_DIR),
    model_file: str = DEFAULT_MODEL_FILE,
    **_: object,
) -> tuple[list[Path], dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = (Path(model_dir) / model_file).resolve()
    mmproj_path = model_path.with_name("PaddleOCR-VL-1.6-GGUF-mmproj.gguf")
    if not model_path.is_file() or not mmproj_path.is_file():
        raise RuntimeError(f"missing reported PaddleOCR GGUF pair under {model_path.parent}")

    facts: dict = {
        "model_file_bytes": model_path.stat().st_size,
        "mmproj_file_bytes": mmproj_path.stat().st_size,
        "free_mib_before_load": _free_mib(),
    }
    load = api_post(
        session,
        "/api/inference/load",
        {
            "model_path": str(model_path),
            "max_seq_length": 4096,
            "gpu_memory_mode": "manual",
            "gpu_layers": -1,
            "force_reload": True,
        },
        timeout=900,
    )
    status = api_get(session, "/api/inference/status", timeout=120)
    facts.update(
        {
            "load_is_vision": load.get("is_vision"),
            "load_is_multimodal": load.get("is_multimodal"),
            "load_mmproj_fallback_reason": load.get("mmproj_fallback_reason"),
            "status_is_vision": status.get("is_vision"),
            "status_is_multimodal": status.get("is_multimodal"),
            "status_mmproj_fallback_reason": status.get("mmproj_fallback_reason"),
            "active_model": status.get("active_model"),
            "free_mib_after_load": _free_mib(),
        }
    )

    input_image = out_dir / "vision_input.png"
    _input_image(input_image)
    console_errors: list[str] = []
    request_failures: list[str] = []
    init = seed_init_script(
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
    shots: list[Path] = []
    async with open_chat(
        session.base_url,
        init_scripts=[init],
        viewport=(1500, 900),
        headless=True,
    ) as sp:
        page = sp.page
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        page.on("requestfailed", lambda req: request_failures.append(req.url))
        await page.goto(f"{session.base_url}/chat", wait_until="domcontentloaded")
        composer = page.locator("form:has(textarea)").first
        await composer.wait_for(state="visible", timeout=60_000)
        await page.wait_for_timeout(6_000)

        tools_button = page.get_by_role("button", name="Tools and attachments").first
        await tools_button.click()
        add_files = page.get_by_role("menuitem", name="Add photos & files").first
        async with page.expect_file_chooser(timeout=30_000) as chooser_info:
            await add_files.click()
        chooser = await chooser_info.value
        await chooser.set_files(str(input_image))
        await page.wait_for_timeout(4_000)
        body_text = " ".join((await page.locator("body").inner_text()).split())
        composer_text = " ".join((await composer.inner_text()).split())
        facts.update(
            {
                "body_has_generic_image_error": "cannot accept images" in body_text,
                "body_has_projector_failure": "vision projector failed" in body_text,
                "body_has_cpu_vision_notice": "vision on CPU" in body_text,
                "composer_has_input_filename": input_image.name in composer_text,
                "composer_image_count": await composer.locator("img").count(),
                "file_chooser_completed": True,
            }
        )

        full = out_dir / f"{label.lower()}_chat_after_attachment.png"
        await page.screenshot(path=str(full), full_page=False)
        shots.append(full)
        focused = out_dir / f"{label.lower()}_composer_after_attachment.png"
        await page.screenshot(
            path=str(focused),
            clip={"x": 520, "y": 390, "width": 950, "height": 480},
        )
        shots.append(focused)

    facts["console_errors"] = console_errors[:10]
    facts["request_failures"] = request_failures[:10]
    try:
        api_post(
            session,
            "/api/inference/unload",
            {"model_path": str(model_path)},
            timeout=180,
        )
        facts["unloaded"] = True
    except Exception as exc:  # noqa: BLE001
        facts["unloaded"] = f"{type(exc).__name__}: {exc}"
    return shots, facts
