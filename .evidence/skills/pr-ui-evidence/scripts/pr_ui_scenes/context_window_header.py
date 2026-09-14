# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: a resident local GGUF's header before any context count exists.

The browser receives one deterministic inference-status fixture before the SPA boots. Deep
Research is armed through the same persisted setting the composer uses, so the normal recount
stands down and both sides have zero ``/chat/count_tokens`` requests. This isolates the visible
question: does the separately built frontend name the resident model's known window without
inventing usage?
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


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    *,
    model: str,
    variant: str,
    context_length: int,
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph the chat header with a resident fixture and no measured usage."""
    status = {
        "active_model": model,
        "model_identifier": model,
        "is_vision": False,
        "is_gguf": True,
        "is_local_model": False,
        "is_diffusion": False,
        "gguf_variant": variant,
        "is_audio": False,
        "has_audio_input": False,
        "loading": [],
        "loaded": [model],
        "inference": {"temperature": 0.7, "top_p": 0.95},
        "supports_reasoning": False,
        "supports_tools": True,
        "context_length": context_length,
        "max_context_length": context_length,
        "native_context_length": context_length,
        "gpu_memory_mode": "manual",
        "gpu_layers": 0,
        "requested_context_length": context_length,
        "requested_parallel_slots": 1,
        "parallel_slots": 1,
    }
    fixture_script = f"""
(() => {{
  localStorage.setItem("unsloth_chat_deep_research_enabled", "true");
  const statusFixture = {json.dumps(status)};
  const realFetch = window.fetch.bind(window);
  window.__pr8882StatusRequests = 0;
  window.__pr8882CountRequests = 0;
  window.fetch = async (input, init) => {{
    const raw = typeof input === "string" ? input : input instanceof Request ? input.url : String(input);
    const url = new URL(raw, window.location.origin);
    if (url.pathname === "/api/inference/status") {{
      window.__pr8882StatusRequests += 1;
      return new Response(JSON.stringify(statusFixture), {{
        status: 200,
        headers: {{"content-type": "application/json"}},
      }});
    }}
    if (url.pathname === "/api/inference/chat/count_tokens") {{
      window.__pr8882CountRequests += 1;
      return new Response(JSON.stringify({{"detail": "count disabled by evidence fixture"}}), {{
        status: 503,
        headers: {{"content-type": "application/json"}},
      }});
    }}
    return realFetch(input, init);
  }};
}})();
"""
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
        init_scripts=[auth_script, fixture_script],
        viewport=(1500, 900),
        headless=True,
    ) as sp:
        page = sp.page
        trigger = page.locator('[data-tour="chat-model-selector"]').first
        await trigger.wait_for(state="visible", timeout=60_000)
        await page.wait_for_function("window.__pr8882StatusRequests > 0", timeout=60_000)
        await page.wait_for_function(
            "model => document.body.innerText.includes(model.split('/').pop())",
            arg=model,
            timeout=60_000,
        )
        await page.wait_for_timeout(1_500)

        bars = page.locator(
            'button[aria-label^="Context window:"], button[aria-label^="Context usage:"]'
        )
        bar_count = await bars.count()
        if bar_count > 1:
            raise RuntimeError(f"expected at most one context header button, found {bar_count}")
        bar = bars.first if bar_count else None
        aria = await bar.get_attribute("aria-label") if bar else None
        face = (await bar.inner_text()).strip() if bar else None
        fill_present = bool(bar and await bar.locator("div").count())

        header = trigger.locator("xpath=ancestor::div[contains(@class, 'z-40')][1]")
        await header.wait_for(state="visible", timeout=30_000)
        shot = out_dir / f"{label.lower()}_context_window_header.png"
        await header.screenshot(path=str(shot))

        facts = {
            "fixture_active_model": model,
            "fixture_variant": variant,
            "fixture_context_length": context_length,
            "status_requests": await page.evaluate("window.__pr8882StatusRequests"),
            "count_token_requests": await page.evaluate("window.__pr8882CountRequests"),
            "deep_research_storage": await page.evaluate(
                "localStorage.getItem('unsloth_chat_deep_research_enabled')"
            ),
            "model_selector_text": " ".join((await trigger.inner_text()).split()),
            "context_bar_present": bar_count == 1,
            "context_bar_text": face,
            "context_bar_aria_label": aria,
            "context_bar_fill_present": fill_present,
        }
        return [shot], facts
