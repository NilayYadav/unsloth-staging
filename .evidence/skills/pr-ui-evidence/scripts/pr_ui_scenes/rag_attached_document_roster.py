"""Scene: what the composer's context meter prices for a chat with documents attached.

The change under review rewrites the SYSTEM PROMPT Studio assembles for a RAG-scoped
request, and only on the local paths -- a connected provider runs the Studio tool loop
and never sees this nudge -- so the surface has to be a real resident GGUF.

`/api/inference/chat/count_tokens` is the endpoint the composer's context meter calls,
it goes through the same `_apply_rag_nudge` the completion does, and it answers with an
integer from the resident model's own tokenizer. So the meter is the visible reading and
the integer is the measurement, with no sampling anywhere in the path.

A second project with NO documents is the null control. Tool schemas, the grounding nudge
and the message are identical for it, so the only thing that can separate the two counts
is the roster. On a Studio without this change the two must agree exactly; the control
also catches a tokenizer or fixture that moved between the sides.
"""

from __future__ import annotations

import json
import time
import urllib.request
import uuid
from pathlib import Path

import os
import sys

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

RUN = uuid.uuid4().hex[:8]
WITH_PROJECT = f"pr9718w{RUN}"
WITH_THREAD = f"pr9718wt{RUN}"
BARE_PROJECT = f"pr9718b{RUN}"
BARE_THREAD = f"pr9718bt{RUN}"
CREATED_AT = 1_755_000_000_000

DOCS = {
    "hostel-allotment.txt": (
        "Hostel allotment letter. Room 214, C block, allotted for the autumn term. "
        "Report to the warden with this letter and a photo before the twelfth."
    ),
    "course-syllabus.txt": (
        "Course syllabus. Unit one covers retrieval, unit two covers ranking. "
        "Grading is 40 percent coursework and 60 percent the final examination."
    ),
}

QUESTION = "Which documents do I have attached here? List them by name."
# The count endpoint refuses a RAG-scoped PENDING turn outright -- autoinject would
# splice in retrieved text it never sees -- so the conversation it does price, and the
# one the meter shows, is a settled one ending on an assistant turn.
ANSWER = "Let me check the documents attached to this conversation."


def _put(session: Session, path: str, payload: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        f"{session.base_url}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {session.access_token}"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _load_model(session: Session, model: str, variant: str, context_length: int,
                timeout_s: int = 1800) -> dict:
    """Make the GGUF resident. count_tokens refuses without a loaded tokenizer, and the
    context meter needs the window to draw a percentage."""
    api_post(session, "/api/inference/load", {
        "model_path": model, "gguf_variant": variant, "max_seq_length": context_length,
    }, timeout=timeout_s)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        st = api_get(session, "/api/inference/status")
        if st.get("active_model") == model and not st.get("loading"):
            return st
        time.sleep(3)
    raise RuntimeError(f"{model} never became resident")


def _upload(session: Session, project_id: str, name: str, text: str) -> dict:
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: text/plain\r\n\r\n{text}\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    req = urllib.request.Request(
        f"{session.base_url}/api/rag/projects/{project_id}/documents", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Authorization": f"Bearer {session.access_token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())


def _project(session: Session, project_id: str, name: str) -> None:
    api_post(session, "/api/chat/projects", {
        "id": project_id, "name": name, "instructions": "",
        "archived": False, "createdAt": CREATED_AT, "updatedAt": CREATED_AT,
    })


def _thread(session: Session, thread_id: str, project_id: str, title: str) -> None:
    api_post(session, "/api/chat/threads", {
        "id": thread_id, "title": title, "modelType": "base", "modelId": "",
        "projectId": project_id, "archived": False,
        "createdAt": CREATED_AT, "updatedAt": CREATED_AT,
    })
    _put(session, f"/api/chat/threads/{thread_id}/messages/{thread_id}-q", {
        "id": f"{thread_id}-q", "threadId": thread_id, "role": "user",
        "content": [{"type": "text", "text": QUESTION}], "createdAt": CREATED_AT,
    })
    _put(session, f"/api/chat/threads/{thread_id}/messages/{thread_id}-a", {
        "id": f"{thread_id}-a", "threadId": thread_id, "role": "assistant",
        "content": [{"type": "text", "text": ANSWER}], "createdAt": CREATED_AT + 1,
    })


def _seed(session: Session) -> list[dict]:
    _project(session, WITH_PROJECT, "PR 9718 with sources")
    _thread(session, WITH_THREAD, WITH_PROJECT, "With documents")
    _project(session, BARE_PROJECT, "PR 9718 control")
    _thread(session, BARE_THREAD, BARE_PROJECT, "Control, no documents")
    for name, text in DOCS.items():
        _upload(session, WITH_PROJECT, name, text)
    deadline = time.time() + 1800
    while time.time() < deadline:
        docs = api_get(session, f"/api/rag/projects/{WITH_PROJECT}/documents")["documents"]
        if docs and all(d.get("status") in ("completed", "failed") for d in docs):
            return docs
        time.sleep(3)
    raise RuntimeError("documents never finished ingesting")


def _count(session: Session, model: str, project_id: str, thread_id: str,
           tools: bool = True) -> dict:
    """The composer's own request shape, so the number is the one the meter shows."""
    return api_post(session, "/api/inference/chat/count_tokens", {
        "model": model,
        "messages": [
            {"role": "user", "content": QUESTION},
            {"role": "assistant", "content": ANSWER},
        ],
        # Explicit: get_tool_policy_default() is None unless `unsloth studio run`
        # installed one, and the harness launches plain `unsloth studio`, so an omitted
        # enable_tools prices no tool block at all -- and therefore no nudge and no roster.
        "enable_tools": tools,
        "enabled_tools": ["search_knowledge_base"],
        "mcp_enabled": False,
        "auto_heal_tool_calls": True,
        "bypass_permissions": False,
        "permission_mode": "auto",
        "rag_scope": {
            "project_id": project_id, "thread_id": thread_id,
            "default_top_k": 10, "mode": "hybrid",
        },
    })


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    *,
    model: str,
    variant: str,
    context_length: int = 2048,
    **_: object,
) -> tuple[list[Path], dict]:
    """Read the meter for a chat with documents attached, against one without."""
    status = _load_model(session, model, variant, context_length)
    documents = _seed(session)

    with_docs = _count(session, model, WITH_PROJECT, WITH_THREAD)
    control = _count(session, model, BARE_PROJECT, BARE_THREAD)
    # The roster rides inside the tool block. If that block is not priced at all, both
    # numbers above are just the two messages and the scene has measured nothing, which
    # is indistinguishable from "the PR changed nothing" unless it is asserted.
    tools_off = _count(session, model, WITH_PROJECT, WITH_THREAD, tools = False)

    auth = type("A", (), {"access_token": session.access_token,
                          "refresh_token": session.refresh_token})()
    async with open_chat(
        session.base_url, init_scripts=[seed_init_script(auth, [])],
        viewport=(1280, 900), headless=True,
    ) as sp:
        page = sp.page
        await page.goto(f"{session.base_url}/chat?thread={WITH_THREAD}",
                        wait_until="domcontentloaded", timeout=60_000)
        await page.locator("form:has(textarea) textarea").first.wait_for(
            state="visible", timeout=60_000)
        # The meter is populated by the same count_tokens call measured above; give the
        # debounced recount time to land before shooting, and open the tooltip, which is
        # where the percentage and the prompt-token total are written out.
        meter = page.locator('button[aria-label^="Context"]').first
        try:
            await meter.wait_for(state="visible", timeout=60_000)
            await meter.hover()
            await page.wait_for_timeout(2_000)
            meter_label = await meter.get_attribute("aria-label")
        except Exception:  # noqa: BLE001 -- recorded as a fact, not swallowed
            meter_label = None
        await page.wait_for_timeout(1_500)
        body_text = " ".join((await page.locator("body").inner_text()).split())

        shot = out_dir / f"{label.lower()}_rag_attached_document_roster.png"
        await page.screenshot(path=str(shot),
                              clip={"x": 280, "y": 0, "width": 1000, "height": 620})

    facts = {
        "resident_model": status.get("active_model"),
        # The roster rides in the tool block, which count_tokens prices only for a
        # template that declares tools. False here means the scene measured nothing.
        "model_supports_tools": status.get("supports_tools"),
        "gguf_variant": status.get("gguf_variant"),
        "context_length": status.get("context_length"),
        "documents_ingested": sorted(
            f"{d['filename']}:{d.get('status')}:{d.get('numChunks')}" for d in documents
        ),
        "tokens_with_documents": with_docs.get("input_tokens"),
        "tokens_control_no_documents": control.get("input_tokens"),
        "roster_token_cost": (
            with_docs.get("input_tokens", 0) - control.get("input_tokens", 0)
        ),
        "tokenizer_model": with_docs.get("model"),
        "tokens_tools_off": tools_off.get("input_tokens"),
        # Guards the shape that measured nothing twice: no tool block, no nudge, no roster.
        "tool_block_cost": (
            with_docs.get("input_tokens", 0) - tools_off.get("input_tokens", 0)
        ),
        "meter_label": meter_label,
        "meter_label_in_page": bool(meter_label),
        "question_visible": QUESTION[:40] in body_text,
    }
    return [shot], facts
