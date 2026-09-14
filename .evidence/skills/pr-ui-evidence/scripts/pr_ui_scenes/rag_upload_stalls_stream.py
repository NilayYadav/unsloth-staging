"""Scene: whether a reply keeps streaming into the chat while a document uploads.

The three RAG upload routes were `async def` but never awaited, so FastAPI ran them ON
the event loop: the file copy, the sha256 re-read and the embedder probe all held it.
Nothing else the server owed anyone could move meanwhile, and the loudest casualty is a
streaming reply, which stops mid-token until the upload lets go.

So the surface is a real resident GGUF answering a long prompt, and the measurement is
the assistant text that arrives BETWEEN the upload request leaving the page and its
response coming back. That window is the blocked one. Reading the transcript once at
each boundary keeps this structural rather than a wall-clock bound: on the unfixed
server the count is ~0 because the loop cannot run the stream at all, and on the fixed
one it is whatever the model managed, which is far from zero.

The upload is fired from the page with the UI's own credentials, so it is a genuine
browser request over HTTP and not a test-client call dressed up as one.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat, pick_model, send_prompt  # noqa: E402

RUN = uuid.uuid4().hex[:8]
# Big enough that the copy + rehash is a window worth photographing, under the 200 MB
# MAX_UPLOAD_BYTES the route enforces.
UPLOAD_MB = 180
# Long enough that the model is still streaming when the upload lands, and dull enough
# that neither side wanders off into a different shape of answer.
PROMPT = (
    "Count slowly from one to two hundred in words, one number per line, "
    "with no commentary before or after."
)


def _load_model(session: Session, model: str, variant: str, context_length: int,
                timeout_s: int = 1800) -> dict:
    api_post(session, "/api/inference/load", {
        "model_path": model, "gguf_variant": variant, "max_seq_length": context_length,
    }, timeout=timeout_s)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        st = api_get(session, "/api/inference/status", timeout=60)
        if st.get("active_model") == model and not st.get("loading"):
            return st
        time.sleep(3)
    raise RuntimeError("model never became resident")


async def drive(session: Session, out_dir: Path, label: str, *,
                model: str, variant: str, context_length: int):
    resident = _load_model(session, model, variant, context_length)
    kb = api_post(session, "/api/rag/knowledge-bases", {"name": f"pr10552-{RUN}"})
    kb_id = kb["id"]

    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(),
        [],
    )

    async with open_chat(session.base_url, init_scripts=[auth_script],
                         viewport=(1440, 900), headless=True) as sp:
        page = sp.page
        await page.wait_for_selector('[data-tour="chat-model-selector"]', timeout=60_000)
        try:
            await pick_model(sp, model)
        except Exception:
            pass  # a single resident model is already selected

        await send_prompt(sp, PROMPT)
        bodies = page.locator(".aui-assistant-message-content")
        # Only start the upload once tokens are genuinely flowing, so the window we
        # measure is inside the stream rather than in front of it.
        await page.wait_for_function(
            "() => { const n = document.querySelectorAll('.aui-assistant-message-content');"
            "  return n.length && n[n.length-1].innerText.trim().length > 40; }",
            timeout=120_000,
        )

        result = await page.evaluate(
            """async ({kbId, mb}) => {
                const tail = () => {
                    const n = document.querySelectorAll('.aui-assistant-message-content');
                    return n.length ? n[n.length - 1].innerText.length : 0;
                };
                const token = localStorage.getItem('unsloth_auth_token');
                const blob = new Blob([new Uint8Array(mb * 1024 * 1024)], {type: 'text/plain'});
                const form = new FormData();
                form.append('file', blob, 'evidence.txt');
                const before = tail();
                const t0 = performance.now();
                let status = 0;
                try {
                    const r = await fetch(`/api/rag/knowledge-bases/${kbId}/documents`, {
                        method: 'POST', body: form,
                        headers: token ? {'Authorization': `Bearer ${token}`} : {},
                    });
                    status = r.status;
                } catch (e) { status = -1; }
                const ms = performance.now() - t0;
                const after = tail();
                return {before, after, chars_during_upload: after - before,
                        upload_ms: Math.round(ms), status};
            }""",
            {"kbId": kb_id, "mb": UPLOAD_MB},
        )

        if result["status"] != 200:
            raise RuntimeError(
                f"upload answered {result['status']}, so it never reached the handler and "
                "the window measures nothing"
            )

        shot = out_dir / f"{label.lower()}_rag_upload_stalls_stream.png"
        await page.screenshot(path=str(shot))

        facts = {
            "resident_model": resident.get("active_model"),
            "upload_mb": UPLOAD_MB,
            "upload_status": result["status"],
            "upload_ms": result["upload_ms"],
            "assistant_chars_before_upload": result["before"],
            "assistant_chars_after_upload": result["after"],
            "chars_streamed_during_upload": result["chars_during_upload"],
        }
        return [shot], facts
