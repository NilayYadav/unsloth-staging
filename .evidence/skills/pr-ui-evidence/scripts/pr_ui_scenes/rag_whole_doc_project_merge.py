"""Scene: the Document Sources row under an answer in a project chat with a file attached.

The change under review decides how much project material is allowed to ride along
with a thread-attached document. Whole-document mode is a LOCAL inference path --
`build_rag_autoinject` is called from the llama.cpp / safetensors / studio tool loops
and never from a hosted turn -- so the surface has to be a real resident GGUF answering
a real turn, not a counted prompt.

What is photographed is the "Document Sources" row the assistant message renders
(`studio/frontend/src/components/assistant-ui/rag-sources.tsx`), one badge per distinct
document that reached the model. That row IS the injected source list, so its length is
the change, and it is deduplicated per document, which is why the fixture uses several
small project documents rather than one large one.

The project documents are CJK on purpose. That is the whole point of the PR: the block
was admitted on `len(text) // 4`, the English rule, and a CJK character is about one
token, so four characters per token under-prices this fixture roughly fourfold. The
attached file itself is short ASCII, so it is admitted on either measure and the only
thing that can move between the sides is how much project text came with it.
"""

from __future__ import annotations

import json
import re
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
from studio_test_kit.ui import open_chat, pick_model, send_prompt, wait_for_stream  # noqa: E402

# The two sidebar RAG settings this scene has to pin, by their localStorage keys
# (chat-runtime-store.ts). Defaults are top-K 5 and floor 0.70; the floor is the one
# that matters -- at 0.70 only two of the four project documents clear it against this
# question, and two are not enough text for the two measures to disagree, so both sides
# admit the same block and the pair proves nothing. Zero is a real user setting ("a weak
# match is often still the right turn" is the backend's own default reasoning) and it is
# applied identically to BEFORE and AFTER, so it controls the fixture, not the result.
SETTINGS_SEED = """
try {
  localStorage.setItem('unsloth_chat_rag_autoinject_min_score', '0');
  localStorage.setItem('unsloth_chat_rag_top_k', '5');
  localStorage.setItem('unsloth_chat_tools_enabled', 'false');
  localStorage.setItem('unsloth_chat_reasoning_enabled', 'false');
} catch (e) {}
"""
# Tools off is the control that makes the row readable. Auto-injection does NOT depend on
# it -- `skip_autoinject` in studio_tool_loop.py turns only on continue_final_message and
# the confirm-permissions gate -- so the whole-document block is still spliced in. What
# tools-off removes is the model's OWN follow-up `search_knowledge_base` call, and
# `RagSourcesGroup` unions citations across every tool-call part in the message, so one
# extra model-initiated search silently adds documents the change under review never
# injected. That is what made an earlier pair read five badges on both sides while the
# servers logged five injected sources against three. Reasoning off for the same reason:
# a thinking turn is likelier to take that second look.

RUN = uuid.uuid4().hex[:8]
PROJECT = f"pr10318p{RUN}"
THREAD = f"pr10318t{RUN}"
CREATED_AT = 1_755_000_000_000

# Short ASCII, one chunk, comfortably under the budget on either measure: this is the
# attachment whose survival the branch exists to protect, not the thing being trimmed.
ATTACHED_NAME = "meeting-notes.txt"
ATTACHED_TEXT = (
    "Meeting notes. The launch review is on the twelfth. Owner is the platform team. "
    "Open item: confirm the storage quota before the freeze."
)

# Four project documents, one chunk each, dense enough that four characters per token
# is wrong by about fourfold. Four is `_AUTOINJECT_DEFAULT_TOP_K`, so top-K can return
# all of them and the trim is the only thing that can drop any.
#
# Each body is prefixed with its own number. The store keys a document by sha256, so
# four byte-identical uploads become ONE document, the roster never reaches four, and
# the ingestion wait hangs on documents that were deduplicated rather than dropped.
#
# The English lead sentence is not decoration. The whole-document companion search runs
# WITH the relevance floor, and an all-CJK body scores under it against an English
# question, so the companion comes back empty, nothing is merged, and both sides inject
# the attachment alone -- a clean run that photographs the wrong code path. The lead
# earns the retrieval; the CJK tail is what makes four characters per token wrong.
_CJK_PARAGRAPH = "检索增强生成系统需要在有限的上下文窗口内选择最相关的文档片段。" * 14
PROJECT_DOCS = {
    f"project-source-{i}.txt": (
        f"Open item {i}: the launch review notes and the storage quota for the platform "
        f"team, attached for reference. 第{i}号资料。" + _CJK_PARAGRAPH
    )
    for i in range(1, 5)
}

QUESTION = "Using the attached notes, what is the open item and when is the review?"


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
    """Make the GGUF resident.

    The window is also the measurement: `_text_token_cost` only trusts the serving
    model's own counter when `llama.context_length` equals the window the budget was
    sized against, and the budget itself is derived from this number.
    """
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


def _upload(session: Session, path: str, name: str, text: str) -> dict:
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    ).encode() + text.encode("utf-8") + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{session.base_url}{path}", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Authorization": f"Bearer {session.access_token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())


def _await_ingest(session: Session, path: str, expected: int) -> list[dict]:
    """Wait for exactly `expected` documents to settle, naming what it saw if they do not.

    The count is asserted, not just the statuses: a duplicate body is stored once, so a
    fixture that repeats itself silently produces fewer documents than it uploaded.
    """
    deadline = time.time() + 600
    docs: list[dict] = []
    while time.time() < deadline:
        docs = api_get(session, path)["documents"]
        if len(docs) >= expected and all(
            d.get("status") in ("completed", "failed") for d in docs
        ):
            return docs
        time.sleep(3)
    raise RuntimeError(
        f"{path}: wanted {expected} settled documents, saw "
        + repr([(d.get("filename"), d.get("status")) for d in docs])
    )


def _seed(session: Session) -> tuple[list[dict], list[dict]]:
    api_post(session, "/api/chat/projects", {
        "id": PROJECT, "name": "PR 10318 sources", "instructions": "",
        "archived": False, "createdAt": CREATED_AT, "updatedAt": CREATED_AT,
    })
    api_post(session, "/api/chat/threads", {
        "id": THREAD, "title": "Attached notes", "modelType": "base", "modelId": "",
        "projectId": PROJECT, "archived": False,
        "createdAt": CREATED_AT, "updatedAt": CREATED_AT,
    })
    for name, text in PROJECT_DOCS.items():
        _upload(session, f"/api/rag/projects/{PROJECT}/documents", name, text)
    _upload(session, f"/api/rag/threads/{THREAD}/documents", ATTACHED_NAME, ATTACHED_TEXT)
    project_docs = _await_ingest(
        session, f"/api/rag/projects/{PROJECT}/documents", len(PROJECT_DOCS))
    thread_docs = _await_ingest(session, f"/api/rag/threads/{THREAD}/documents", 1)
    return project_docs, thread_docs


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    *,
    model: str,
    variant: str,
    context_length: int = 2560,
    **_: object,
) -> tuple[list[Path], dict]:
    """Answer one real turn in a project chat with a file attached, then read the row."""
    status = _load_model(session, model, variant, context_length)
    project_docs, thread_docs = _seed(session)

    auth = type("A", (), {"access_token": session.access_token,
                          "refresh_token": session.refresh_token})()
    async with open_chat(
        session.base_url, init_scripts=[seed_init_script(auth, []), SETTINGS_SEED],
        viewport=(1280, 900), headless=True,
    ) as sp:
        page = sp.page
        await page.goto(f"{session.base_url}/chat?thread={THREAD}",
                        wait_until="domcontentloaded", timeout=60_000)
        await page.locator("form:has(textarea) textarea").first.wait_for(
            state="visible", timeout=60_000)
        await page.wait_for_timeout(2_000)
        # A composer left on another model would send this turn to a path that never
        # calls build_rag_autoinject, which photographs as no change on both sides.
        try:
            await pick_model(sp, model)
            model_picked = True
        except Exception:  # noqa: BLE001 -- the resident model is usually preselected
            model_picked = False
        await page.wait_for_timeout(1_000)

        # Chat with Files is what puts thread_id into the request's rag_scope
        # (chat-adapter.ts: `ragEnabled && threadId ? { thread_id: threadId }`), and
        # thread_id is what makes whole-document mode reachable at all. It is thread
        # state with no persisted slot, so an API upload alone never turns it on: the
        # turn goes out project-scoped and the attachment is simply not in it.
        rag_enabled = False
        try:
            await page.locator(
                'form:has(textarea) button[aria-label="Tools and attachments"]'
            ).first.click(timeout=30_000)
            item = page.get_by_role("menuitem", name="Chat with Files").first
            await item.wait_for(state="visible", timeout=15_000)
            await item.click()
            await page.wait_for_timeout(2_000)
            rag_enabled = True
        except Exception:  # noqa: BLE001 -- recorded as a fact, not swallowed
            pass
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(1_500)

        await send_prompt(sp, QUESTION)
        try:
            await wait_for_stream(sp, timeout_ms=300_000)
        except Exception:  # noqa: BLE001 -- an error bubble is a result, recorded below
            pass
        await page.wait_for_timeout(3_000)

        # The row itself, not the page text: this is the injected source list.
        group = page.locator(
            'div:has(> div:text-is("Document Sources")) > div.flex.flex-wrap'
        ).last
        badge_names: list[str] = []
        try:
            await group.wait_for(state="visible", timeout=30_000)
            row_text = await group.inner_text()
            badge_names = sorted(
                {w for w in row_text.replace("\n", " ").split()
                 if w.endswith(".txt")}
            )
        except Exception:  # noqa: BLE001
            row_text = ""
        body_text = " ".join((await page.locator("body").inner_text()).split())

        shot = out_dir / f"{label.lower()}_rag_whole_doc_project_merge.png"
        await page.screenshot(path=str(shot),
                              clip={"x": 260, "y": 0, "width": 1020, "height": 900})

    # What the server actually injected, from its own log line, rather than from the DOM.
    # A badge row can be misread; this number is the length of the source list the code
    # under review built. Equal on both sides means the code path did not differ and any
    # DOM fact that moved is a scraping artifact.
    injected = None
    try:
        logs = sorted((session.home / "logs" / "server").glob("*.log"),
                      key=lambda f: f.stat().st_mtime)
        for line in reversed(logs[-1].read_text(errors="ignore").splitlines()):
            found = re.search(r"whole-document context \((\d+) chunk", line)
            if found:
                injected = int(found.group(1))
                break
    except Exception:  # noqa: BLE001 -- absence is itself the fact
        pass

    facts = {
        "resident_model": status.get("active_model"),
        # None means whole-document mode never ran and this scene measured nothing.
        "injected_source_count": injected,
        "gguf_variant": status.get("gguf_variant"),
        "context_length": status.get("context_length"),
        "project_documents_ingested": sorted(
            f"{d['filename']}:{d.get('status')}:{d.get('numChunks')}" for d in project_docs
        ),
        "attached_document_ingested": sorted(
            f"{d['filename']}:{d.get('status')}:{d.get('numChunks')}" for d in thread_docs
        ),
        # The measurement. One badge per distinct document that reached the model.
        "source_badge_count": len(badge_names),
        "source_badge_files": badge_names,
        # The attachment is what this branch exists to keep; losing it is a finding,
        # not a smaller number.
        "attached_document_cited": ATTACHED_NAME in badge_names,
        "project_documents_cited": sum(1 for n in badge_names if n.startswith("project-source-")),
        "sources_row_visible": bool(badge_names),
        # With tools off the only tool-call part is the auto-inject exchange, so the row
        # and the log must agree. A mismatch means something else cited documents and the
        # picture is not showing this change.
        "badges_match_injection": (
            injected is not None and len(badge_names) == injected
        ),
        "context_error_visible": (
            "context" in body_text.lower() and "exceed" in body_text.lower()
        ),
        "question_visible": QUESTION[:40] in body_text,
        "model_picked_in_composer": model_picked,
        # False means the turn went out without thread_id and this scene measured
        # the project-only path, not the one under review.
        "chat_with_files_enabled": rag_enabled,
    }
    return [shot], facts
