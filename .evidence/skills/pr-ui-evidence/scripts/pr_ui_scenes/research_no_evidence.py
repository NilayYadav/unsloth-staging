# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: what Deep Research shows when no step gathered any evidence (PR 10663).

Same shape as `research_truncated_report`: the run is pointed at a saved "custom"
connection whose base_url is a tiny OpenAI-compatible stand-in served from this process,
so no weights, GPU, download or real provider is involved. The difference is what the
stand-in does and what the single research step is allowed to reach.

  * planning returns a one-step plan, so the run reaches synthesis immediately;
  * the decision call is unparseable, so the loop spends its one step on the plan seed;
  * web_search is proxied to a dead port on BOTH sides, so that step gathers nothing;
  * synthesis then returns a COMPLETE report -- the answer a model writes from memory
    when it was handed no evidence at all.

BEFORE (merge base) nothing notices that the evidence never arrived: the run is marked
completed and the memory-written report is delivered with zero sources. AFTER (head) the
run fails with the search error instead and no report is shown.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import (  # noqa: E402
    Session,
    api_get,
    api_post,
    pick_free_ports,
)
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402


def _api_put(session: Session, path: str, payload: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        f"{session.base_url}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {session.access_token}"},
        method="PUT")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


RUN = uuid.uuid4().hex[:10]
CREATED_AT = 1_755_000_000_000
STAND_IN_MODEL = "research-stand-in"
QUESTION = "What did the March 2026 Unsloth release change about GGUF exports?"

# What a model writes when it was handed no evidence and asked for a report anyway: fluent,
# specific, and sourced from nothing. The whole point of the PR is that this must not be
# delivered as a finished research report.
MEMORY_REPORT = (
    "## Findings\n\n"
    "The March 2026 release reworked GGUF export end to end. Exports moved to a "
    "streaming writer, quantisation defaults changed to Q4_K_XL, and the exporter "
    "began validating tensor shapes before writing the header.\n\n"
    "## What changed\n\n"
    "- A streaming writer replaced the buffered one.\n"
    "- Q4_K_XL became the default quantisation.\n"
    "- Shape validation now runs before the header is written.\n"
)

REPORT_BOUNDARY = "<!-- UNSLOTH_FINAL_REPORT -->"


def _sse(chunks: list[dict]) -> bytes:
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    return body.encode()


def _chunks(text: str, finish_reason: str) -> list[dict]:
    return [
        {
            "id": "standin",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        },
        {
            "id": "standin",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 2048, "completion_tokens": 256,
                      "total_tokens": 2304},
        },
    ]


class _StandIn(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _handler_class(calls: list[dict]):
    """An OpenAI-compatible endpoint that answers each research phase by what it is asked."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):  # noqa: D102
            return

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/").endswith("/models"):
                self._json({"object": "list", "data": [
                    {"id": STAND_IN_MODEL, "object": "model", "owned_by": "stand-in"}]})
                return
            self._json({"detail": "not found"}, status=404)

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw)
            except Exception:  # noqa: BLE001
                body = {}
            prompt = json.dumps(body.get("messages") or [])
            if REPORT_BOUNDARY in prompt:
                phase = ("synthesis_recovery" if "previous synthesis" in prompt.lower()
                         else "synthesis")
            elif "evidence-to-claim audit" in prompt:
                phase = "synthesis_audit"
            elif "web research plan" in prompt:
                phase = "planning"
            else:
                phase = "decision"
            calls.append({"phase": phase})

            if phase == "planning":
                chunks = _chunks(json.dumps({
                    "title": "GGUF export changes",
                    "steps": [{"title": "What the release notes say",
                               "query": "unsloth march 2026 release gguf export"}],
                }), "stop")
            elif phase == "synthesis_audit":
                chunks = _chunks(json.dumps({
                    "thesis": "The release reworked GGUF export.",
                    "outline": ["Findings", "What changed"],
                    "supportedClaims": [],
                }), "stop")
            elif phase == "decision":
                # Unparseable on purpose: the loop falls back to the plan's seed action and
                # spends its single allowed step on a search that cannot reach anything.
                chunks = _chunks("no action", "stop")
            else:
                # A COMPLETE report, not a truncated one: the model happily writes it from
                # memory because nothing told it the evidence never arrived.
                chunks = _chunks(f"{REPORT_BOUNDARY}\n{MEMORY_REPORT}", "stop")

            payload = _sse(chunks)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def _start_stand_in(calls: list[dict]) -> tuple[_StandIn, int]:
    port = pick_free_ports(1, start=9400, stop=9600)[0]
    server = _StandIn(("127.0.0.1", port), _handler_class(calls))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def _create_provider(session: Session, port: int) -> str:
    made = api_post(session, "/api/providers/", {
        "provider_type": "custom",
        "display_name": "Research stand-in",
        "base_url": f"http://127.0.0.1:{port}/v1",
        "models": [STAND_IN_MODEL],
        "available_models": [STAND_IN_MODEL],
        "encrypted_api_key": "",
    })
    return made["id"]


def _seed_thread(session: Session, thread_id: str, message_id: str, model_id: str) -> None:
    api_post(session, "/api/chat/threads", {
        "id": thread_id, "title": "Deep Research", "modelType": "base",
        "modelId": model_id, "archived": False,
        "createdAt": CREATED_AT, "updatedAt": CREATED_AT,
    })
    _api_put(session, f"/api/chat/threads/{thread_id}/messages", {
        "messages": [{
            "id": message_id, "threadId": thread_id, "role": "user",
            "content": [{"type": "text", "text": QUESTION}],
            "createdAt": CREATED_AT + 1,
        }],
    })


def _run_research(session: Session, thread_id: str, message_id: str,
                  provider_id: str) -> dict:
    created = api_post(session, "/api/chat/research-runs", {
        "threadId": thread_id,
        "userMessageId": message_id,
        "question": QUESTION,
        "inferenceRequest": {
            "model": STAND_IN_MODEL,
            "providerType": "custom",
            "providerId": provider_id,
            "externalModel": STAND_IN_MODEL,
        },
        "budgets": {"maxSteps": 1, "maxSources": 3,
                    "modelTimeoutSeconds": 60, "toolTimeoutSeconds": 20},
    })
    run_id = created["id"]

    deadline = time.time() + 180
    while time.time() < deadline:
        run = api_get(session, f"/api/chat/research-runs/{run_id}")
        if run.get("plan") and run.get("status") == "awaiting_approval":
            api_post(session, f"/api/chat/research-runs/{run_id}/approve", {
                "planRevision": run["planRevision"], "planHash": run["planHash"]})
            break
        if run.get("status") in ("completed", "failed", "cancelled"):
            return run
        time.sleep(2)

    deadline = time.time() + 300
    while time.time() < deadline:
        run = api_get(session, f"/api/chat/research-runs/{run_id}")
        if run.get("status") in ("completed", "failed", "cancelled"):
            return run
        time.sleep(2)
    raise RuntimeError(f"research run {run_id} never reached a terminal status")


async def drive(session: Session, out_dir: Path, label: str,
                **_: object) -> tuple[list[Path], dict]:
    thread_id = f"uidiff-noevidence-{RUN}"
    message_id = f"uidiff-msg-{RUN}"
    calls: list[dict] = []
    server, port = _start_stand_in(calls)
    try:
        provider_id = _create_provider(session, port)
        _seed_thread(session, thread_id, message_id, STAND_IN_MODEL)
        run = _run_research(session, thread_id, message_id, provider_id)

        report = run.get("report") or ""
        steps = run.get("steps") or []
        listed = api_get(session, f"/api/chat/threads/{thread_id}/messages")
        messages = (listed or {}).get("messages") or []
        assistant = [m for m in messages if m.get("role") == "assistant"]
        shown = ""
        for part in (assistant[-1].get("content") if assistant else []) or []:
            if isinstance(part, dict) and part.get("type") == "text":
                shown += part.get("text") or ""

        facts = {
            "run_status": run.get("status"),
            "run_error": (run.get("error") or "")[:200],
            "error_names_no_evidence": "gathered any evidence" in (run.get("error") or ""),
            "report_delivered": bool(report),
            "report_chars": len(report),
            "memory_report_delivered": MEMORY_REPORT[:40] in report,
            "sources_count": len(run.get("sources") or []),
            "document_sources_count": len(run.get("documentSources") or []),
            "steps_recorded": len(steps),
            "steps_completed": sum(1 for s in steps if s.get("status") == "completed"),
            "steps_failed": sum(1 for s in steps if s.get("status") == "failed"),
            "assistant_shows_report": MEMORY_REPORT[:40] in shown,
            "assistant_text_chars": len(shown),
            "assistant_research_status": ((assistant[-1].get("metadata") or {})
                                          .get("researchStatus") if assistant else None),
            "model_phases": [c["phase"] for c in calls],
            "synthesis_ran": any(c["phase"] == "synthesis" for c in calls),
            "report_sha": hashlib.sha256(report.encode()).hexdigest()[:16],
        }

        auth_script = seed_init_script(
            type("A", (), {"access_token": session.access_token,
                           "refresh_token": session.refresh_token})(), [],
        )
        shots: list[Path] = []
        async with open_chat(session.base_url, init_scripts=[auth_script],
                             viewport=(1280, 1000), headless=True) as sp:
            page = sp.page
            await page.goto(f"{session.base_url}/chat?thread={thread_id}",
                            wait_until="domcontentloaded", timeout=90_000)
            question = page.get_by_text(QUESTION, exact=False).first
            await question.wait_for(state="visible", timeout=60_000)
            await page.wait_for_timeout(6_000)
            await question.scroll_into_view_if_needed()
            await page.wait_for_timeout(1_000)
            shot = out_dir / f"{label}_research_no_evidence.png"
            await page.screenshot(path=str(shot), full_page=False)
            shots.append(shot)
        return shots, facts
    finally:
        server.shutdown()
        server.server_close()
