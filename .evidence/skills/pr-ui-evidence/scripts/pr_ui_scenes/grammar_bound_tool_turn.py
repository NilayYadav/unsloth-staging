"""Scene: a tool-bearing turn whose schema carries a bound llama.cpp's grammar engine refuses.

The catalog holds the two bounds that decide this PR: a string ``maxLength`` of 2000 and an
array ``maxItems`` of 1998. Both are the first value their keyword cannot compile, measured
against llama.cpp b10639 and b10679 by posting the schemas to a live llama-server.

Nothing here is intercepted. The request goes to the photographed Studio, llama-server answers
it for real, and the API monitor is then read back to show how that turn was recorded. A control
request carrying the highest bound each keyword CAN compile runs on both sides, so a red row is
the schema filter and not a broken model, port or install.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

PROMPT = "What is 1+1? Answer with the digit only."


def _tool(max_length: int, max_items: int) -> list:
    return [{
        "type": "function",
        "function": {
            "name": "report_findings",
            "description": "Record findings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "maxLength": max_length},
                    "tags": {"type": "array", "items": {"type": "string"},
                             "maxItems": max_items},
                },
                "required": ["summary"],
            },
        },
    }]


def _request(session: Session, path: str, payload: dict, timeout: int = 300):
    req = urllib.request.Request(
        f"{session.base_url}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {session.access_token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()[:4000]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:4000]


def _load_model(session: Session, model_path: str, context_length: int,
                timeout_s: int = 1800) -> dict:
    api_post(session, "/api/inference/load",
             {"model_path": model_path, "max_seq_length": context_length}, timeout=timeout_s)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = api_get(session, "/api/inference/status")
        if status.get("active_model") and not status.get("loading"):
            return status
        time.sleep(3)
    raise RuntimeError(f"{model_path} never became resident")


def _turn(session: Session, model_id: str, tools: list):
    return _request(session, "/v1/chat/completions", {
        "model": model_id,
        "messages": [{"role": "user", "content": PROMPT}],
        "tools": tools, "max_tokens": 16, "stream": False,
    })


async def drive(session: Session, out_dir: Path, label: str, model_path: str = "",
                context_length: int = 4096, **_: object) -> tuple[list[Path], dict]:
    loaded = _load_model(session, model_path, context_length)
    model_id = loaded.get("active_model") or model_path

    # Highest bound each keyword compiles: proves the model and server answer at all.
    control_status, _ = _turn(session, model_id, _tool(1999, 1997))
    # First bound each keyword refuses.
    bounded_status, bounded_body = _turn(session, model_id, _tool(2000, 1998))

    lowered = bounded_body.lower()
    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(),
        [],
    )

    async with open_chat(session.base_url, init_scripts=[auth_script],
                         viewport=(1500, 1000), headless=True) as sp:
        page = sp.page
        await page.goto(f"{session.base_url}/api-monitor", wait_until="domcontentloaded")
        await page.get_by_label("Search API requests").wait_for(state="visible", timeout=60_000)
        await page.wait_for_timeout(4_000)

        body_text = " ".join((await page.locator("body").inner_text()).split())
        section = page.locator("section").filter(
            has=page.get_by_label("Search API requests")
        ).first
        shot = out_dir / f"{label.lower()}_grammar_bound_tool_turn.png"
        await section.screenshot(path=str(shot))

        facts = {
            "model_path": model_path,
            "active_model": model_id,
            # Identical on both sides; a move here means the pair is not comparable.
            "control_http_status": control_status,
            "bounded_http_status": bounded_status,
            "bounded_grammar_error": "failed to parse grammar" in lowered,
            # The message the old text sent users chasing: another model or quant.
            "bounded_blames_model_or_quant": "quant" in lowered or "different gguf" in lowered,
            "bounded_names_schema": "schema" in lowered,
            "bounded_body_head": bounded_body[:300],
            "ui_monitor_shows_error": "error" in body_text.lower(),
            "ui_body_char_count": len(body_text),
        }
        return [shot], facts
