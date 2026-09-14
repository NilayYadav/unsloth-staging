# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: the Train page "Recents" list after a tokenless API key tries to train a private model.

Both Studios run against the same stand-in Hugging Face server, hold the same saved login, and
receive the same API-key request. The photographed surface is the owner's /studio page from each
tested SHA; the facts count runs, visible rows, and requests that carried the saved login.
"""

from __future__ import annotations

import asyncio
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

from pr_ui_scenes._common import Session, api_get  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402


def _request(method: str, url: str, token: str | None = None, body: dict | None = None):
    req = urllib.request.Request(
        url,
        data = json.dumps(body).encode() if body is not None else None,
        method = method,
        headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout = 300) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw.decode(errors = "replace")[:300]


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    *,
    hub_url: str,
    model_name: str,
    wait_s: float = 45.0,
    **_: object,
) -> tuple[list[Path], dict]:
    base = session.base_url
    facts: dict = {"label": label, "model_name": model_name}

    code, key = _request("POST", f"{base}/api/auth/api-keys", session.access_token, {"name": f"ui-evidence-{label.lower()}"})
    assert code == 200, (code, key)
    _request("GET", f"{hub_url}/__probe_log?reset=1")

    code, resp = _request(
        "POST",
        f"{base}/api/train/start",
        key["key"],
        {"model_name": model_name, "training_type": "LoRA/QLoRA", "format_type": "alpaca", "load_in_4bit": False},
    )
    detail = resp.get("detail") if isinstance(resp, dict) else None
    facts["api_key_start_http_status"] = code
    facts["api_key_start_result"] = (
        detail.get("code") if isinstance(detail, dict) else (resp.get("status") if isinstance(resp, dict) else str(resp)[:120])
    )

    deadline = time.monotonic() + wait_s
    matching: list = []
    while time.monotonic() < deadline:
        runs = api_get(session, "/api/train/runs").get("runs", [])
        matching = [r for r in runs if r.get("model_name") == model_name]
        if matching and all(r.get("status") != "running" for r in matching):
            break
        await asyncio.sleep(2)
    facts["runs_for_private_model"] = len(matching)
    facts["run_statuses"] = sorted({str(r.get("status")) for r in matching})
    facts["server_login_requests"] = _request("GET", f"{hub_url}/__probe_log")[1].get("operator")

    init = seed_init_script(
        type("A", (), {"access_token": session.access_token, "refresh_token": session.refresh_token})(),
        [],
    )
    shot = out_dir / f"{label.lower()}-studio-recents.png"
    async with open_chat(base, init_scripts = [init], viewport = (1440, 900), headless = True) as sp:
        page = sp.page
        health = api_get(session, "/api/health")
        facts["backend_chat_only"] = health.get("chat_only")
        facts["backend_chat_only_reason"] = health.get("chat_only_reason")
        await page.goto(f"{base}/studio", wait_until = "domcontentloaded")
        await page.wait_for_timeout(5_000)
        if not page.url.startswith(f"{base}/studio"):
            raise RuntimeError(
                f"{label}: /studio redirected to {page.url.replace(base, '')} "
                f"(chat_only={facts['backend_chat_only']}, reason={facts['backend_chat_only_reason']}); "
                "the Train page is not reachable on this host, so no screenshot is evidence"
            )
        sidebar = page.locator('[data-sidebar="sidebar"]').first
        await sidebar.wait_for(state = "visible", timeout = 60_000)
        row = sidebar.get_by_text(model_name, exact = True)
        try:
            await row.first.wait_for(state = "visible", timeout = 20_000)
        except Exception:
            pass
        await page.wait_for_timeout(2_000)
        sidebar_text = await sidebar.inner_text()
        facts["ui_recents_group_visible"] = "Recents" in sidebar_text
        facts["ui_private_model_rows"] = await row.count()
        facts["ui_url"] = page.url.replace(base, "")
        await page.screenshot(path = str(shot), full_page = False)
    return [shot], facts
