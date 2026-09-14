"""Scene: what the API monitor records when a non-streaming client walks away.

Nothing is intercepted. A real non-streaming POST /v1/messages goes to the
photographed Studio over a raw socket, llama-server answers it for real, and the
socket is reset mid-generation so uvicorn sees a genuine client disconnect. The
API monitor page is then read back to show how that turn was recorded.

A control turn that is allowed to finish runs first on both sides, so an amber row
is the disconnect and not a broken model, port or install.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

CONTROL_PROMPT = "Reply with the single word: ok"
ABANDON_PROMPT = "Count slowly from one to two hundred, one number per line."
ABANDON_MAX_TOKENS = 400
CONTROL_MAX_TOKENS = 16
# Long enough that the model is certainly decoding, short enough that the answer
# cannot have finished on either side.
HOLD_BEFORE_ABORT_S = 0.4


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


def _messages_payload(session: Session, model_id: str, prompt: str, max_tokens: int) -> dict:
    return {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": False,
    }


def _control_turn(session: Session, model_id: str) -> tuple[int, str]:
    req = urllib.request.Request(
        f"{session.base_url}/v1/messages",
        data=json.dumps(
            _messages_payload(session, model_id, CONTROL_PROMPT, CONTROL_MAX_TOKENS)
        ).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {session.access_token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, r.read().decode()[:2000]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:2000]


def _abandon_turn(session: Session, model_id: str) -> None:
    """POST a non-streaming turn, then RST the socket while it is still decoding.

    A plain close() would send FIN and could be read as a half-close; SO_LINGER 0
    forces a reset, which is what uvicorn turns into connection_lost -> the
    http.disconnect that request.is_disconnected() reports.
    """
    parts = urlsplit(session.base_url)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or 80
    body = json.dumps(
        _messages_payload(session, model_id, ABANDON_PROMPT, ABANDON_MAX_TOKENS)
    ).encode()
    head = (
        "POST /v1/messages HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Authorization: Bearer {session.access_token}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    sock = socket.create_connection((host, port), timeout=30)
    try:
        sock.sendall(head + body)
        time.sleep(HOLD_BEFORE_ABORT_S)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    finally:
        sock.close()


def _messages_rows(session: Session) -> list[dict]:
    data = api_get(session, "/api/inference/monitor")
    return [e for e in data.get("entries", []) if e.get("endpoint") == "/v1/messages"]


def _watch_abandoned_row(session: Session, before_ids: set[str],
                         timeout_s: float = 180.0) -> tuple[dict, float]:
    """Poll until the abandoned run's row goes terminal; return it and how long that took."""
    start = time.time()
    row = None
    while time.time() - start < timeout_s:
        for entry in _messages_rows(session):
            if entry.get("id") in before_ids:
                continue
            row = entry
            if entry.get("status") != "running":
                return entry, time.time() - start
        time.sleep(0.25)
    raise RuntimeError(f"abandoned /v1/messages row never went terminal: {row}")


async def drive(session: Session, out_dir: Path, label: str, model_path: str = "",
                context_length: int = 4096, **_: object) -> tuple[list[Path], dict]:
    loaded = _load_model(session, model_path, context_length)
    model_id = loaded.get("active_model") or model_path

    control_status, control_body = _control_turn(session, model_id)
    control_rows = _messages_rows(session)
    seen = {e.get("id") for e in control_rows}

    abandon_started = time.time()
    _abandon_turn(session, model_id)
    row, settle_s = _watch_abandoned_row(session, seen)
    held_s = time.time() - abandon_started

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

        # Open the abandoned run so the pane prints its status word and stop reason;
        # the row list alone differs only by the colour of a 8px dot.
        abandoned = page.get_by_role("button").filter(has_text="/messages").first
        await abandoned.wait_for(state="visible", timeout=30_000)
        await abandoned.click()
        heading = page.get_by_role("heading", name="POST /v1/messages").first
        await heading.wait_for(state="visible", timeout=30_000)
        detail = heading.locator("xpath=ancestor::div[contains(@class, 'p-5')][1]")
        labels = [t.strip() for t in await detail.locator("dt").all_inner_texts()]
        values = [t.strip() for t in await detail.locator("dd").all_inner_texts()]
        metrics = dict(zip(labels, values, strict=True))

        body_text = " ".join((await page.locator("body").inner_text()).split())
        section = page.locator("section").filter(
            has=page.get_by_label("Search API requests")
        ).first
        shot = out_dir / f"{label.lower()}_nonstream_disconnect_monitor.png"
        await section.screenshot(path=str(shot))

    facts = {
        "model_path": model_path,
        "active_model": model_id,
        # Identical on both sides; a move here means the pair is not comparable.
        "control_http_status": control_status,
        "control_row_status": (control_rows[0].get("status") if control_rows else None),
        "control_body_head": control_body[:200],
        # The abandoned run: what the server did and what it wrote down.
        "abandoned_row_status": row.get("status"),
        "abandoned_stop_reason": row.get("stop_reason"),
        "abandoned_completion_tokens": row.get("completion_tokens"),
        "abandoned_duration_ms": row.get("duration_ms"),
        "abandoned_max_tokens_asked": ABANDON_MAX_TOKENS,
        # Wall clock from the abort to the row going terminal: the model is held for
        # this long by a client that is no longer there.
        "held_after_client_left_s": round(held_s - HOLD_BEFORE_ABORT_S, 2),
        "row_settled_after_s": round(settle_s, 2),
        # Read off the pane that was photographed, not off the API.
        "ui_detail_status_word": ("CANCELLED" if "CANCELLED" in body_text
                                  else "COMPLETED" if "COMPLETED" in body_text else None),
        "ui_detail_stop_reason": metrics.get("Stop reason"),
        "ui_detail_duration": metrics.get("Duration"),
        "ui_detail_output_tokens": metrics.get("Output tokens"),
    }
    return [shot], facts
