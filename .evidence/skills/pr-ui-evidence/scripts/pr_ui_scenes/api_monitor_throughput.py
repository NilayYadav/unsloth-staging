# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: request-scoped prompt and generation rates in the API monitor detail pane.

The request payload is deterministic and intercepted at the browser boundary. This keeps
both real, separately built Studio frontends on the exact same monitor snapshot without
loading weights merely to manufacture timing values. The photographed surface is still the
built API monitor page from each tested SHA; only its polled data is fixed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

ENTRY_ID = "req_ui_evidence_8700"
ENTRY = {
    "id": ENTRY_ID,
    "endpoint": "/v1/chat/completions",
    "method": "POST",
    "model": "unsloth/Qwen3-4B-GGUF · Q4_K_M",
    "prompt": "Explain why request-scoped throughput is useful.",
    "reply": "Prompt and generation rates describe different phases.",
    "via_api_key": True,
    "prompt_preview": "Explain why request-scoped throughput is useful.",
    "reply_preview": "Prompt and generation rates describe different phases.",
    "prompt_truncated": False,
    "reply_truncated": False,
    "status": "completed",
    "started_at": 1_775_000_000.0,
    "updated_at": 1_775_000_002.0,
    "finished_at": 1_775_000_002.0,
    "duration_ms": 2_000.0,
    "decode_ms": 1_000.0,
    "context_length": 32_768,
    "context_usage": 0.03125,
    "prompt_tokens": 1_024,
    "completion_tokens": 64,
    "total_tokens": 1_088,
    "error": None,
    "kind": "request",
    "ttft_ms": 1_000.0,
    "tok_per_sec": 64.2,
    "prompt_tok_per_sec": 2_048.4,
    "stop_reason": "stop",
}
MONITOR = {
    "status": "ready",
    "server_time": 1_775_000_003.0,
    "active_model": ENTRY["model"],
    "context_length": ENTRY["context_length"],
    "active_requests": 0,
    "queue": {"capacity": 1, "active": 0, "queued": 0, "free": 1},
    "logging_enabled": True,
    "entries": [ENTRY],
}


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph one fixed monitor row and return its rendered metric grid."""
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
    async with open_chat(
        session.base_url,
        init_scripts=[init],
        viewport=(1500, 1000),
        headless=True,
    ) as sp:
        page = sp.page

        async def fixed_monitor(route) -> None:
            url = route.request.url.split("?", 1)[0]
            payload = ENTRY if url.endswith(f"/monitor/{ENTRY_ID}") else MONITOR
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(payload),
            )

        await page.route("**/api/inference/monitor**", fixed_monitor)
        await page.goto(f"{session.base_url}/api-monitor", wait_until="domcontentloaded")
        await page.get_by_label("Search API requests").wait_for(state="visible", timeout=60_000)

        row = page.get_by_role("button").filter(has_text="/chat/completions").first
        await row.wait_for(state="visible", timeout=30_000)
        await row.click()

        heading = page.get_by_role("heading", name="POST /v1/chat/completions")
        await heading.wait_for(state="visible", timeout=30_000)
        detail = heading.locator("xpath=ancestor::div[contains(@class, 'p-5')][1]")
        await detail.get_by_text("64.2 tok/s", exact=True).wait_for(
            state="visible", timeout=30_000
        )

        labels = [text.strip() for text in await detail.locator("dt").all_inner_texts()]
        values = [text.strip() for text in await detail.locator("dd").all_inner_texts()]
        metrics = dict(zip(labels, values, strict=True))
        normalized_labels = {label.casefold() for label in labels}
        facts = {
            "fixture_prompt_tok_per_sec": ENTRY["prompt_tok_per_sec"],
            "fixture_generation_tok_per_sec": ENTRY["tok_per_sec"],
            "rendered_metrics": metrics,
            "has_prompt_speed": "prompt speed" in normalized_labels,
            "has_generation_speed": "generation speed" in normalized_labels,
            "has_legacy_speed": "speed" in normalized_labels,
        }

        section = page.locator("section").filter(
            has=page.get_by_label("Search API requests")
        ).first
        shot = out_dir / f"{label.lower()}_api_monitor_throughput.png"
        await section.screenshot(path=str(shot))
        return [shot], facts
