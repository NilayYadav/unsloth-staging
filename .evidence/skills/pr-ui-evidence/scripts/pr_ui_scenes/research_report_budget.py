# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: the report budget Deep Research spends on a saved connection.

Adapted from `research_truncated_report`. Same stand-in connection and the same real
`ResearchSupervisor`, but the stand-in now behaves like a real provider: it honours the
`max_tokens` it is sent, truncating with `finish_reason: "length"` when the request cannot
fit the whole report and running to a natural `stop` when it can.

The connection is saved WITH a per-connection Max Output Tokens of 32768, which is the
field the Studio settings dialog writes. BEFORE (merge base) the report hop ignores it and
spends the hardcoded 16384, which is not enough for this report, so the run is delivered
truncated under an "Incomplete report." callout. AFTER (head) the hop resolves the
connection's own ceiling, asks for 32768, and the report completes.

The report is written by the BACKEND, not the browser, so a page-level route intercept
cannot reach it. The run is instead pointed at a saved connection whose base_url is a
tiny OpenAI-compatible stand-in served from this process, which is exactly the shape
`routes/research_runs.py` sanctions for durable research (`provider_runs_local_tools`).
Nothing is downloaded, no weights load, and no GPU is touched: the two sides differ only
in the separately built Studio that ran the synthesis.

The stand-in makes the run take the branch the PR is about:

  * planning returns a one-step plan, so the run reaches synthesis immediately;
  * synthesis returns a long draft that stops mid ```python fence with
    ``finish_reason: "length"``;
  * the recovery pass returns a SHORTER draft, also ``length``.

BEFORE (merge base) that pair is fatal -- the recovery replaced the first draft no matter
what and a second `length` raised -- so the chat shows a failed run and no report at all.
AFTER (head) the better of the two drafts is kept and delivered, led by an "Incomplete
report." callout: the notice sits above the report because a draft cut off inside a code
fence would otherwise swallow anything placed under it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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
    """`_common` has GET and POST; the message write is a PUT."""
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
QUESTION = "What limits the context window of a local GGUF model?"

# What the model gets out before a 16384 budget runs dry: stops INSIDE a python fence,
# the shape a report takes when the budget expires while it is quoting a config block.
TRUNCATED_DRAFT = (
    "## Findings\n\n"
    + "The context window is set at load time and every later request is measured "
    "against it. "
    * 4
    + "\n\n## The setting that matters\n\n```python\nllama = Llama(\n    model_path = "
    "model,\n    n_ctx = 32768,\n    rope_scaling ="
)
# The same report carried to its end, which only the larger budget can pay for.
FULL_REPORT = (
    "## Findings\n\n"
    + "The context window is set at load time and every later request is measured "
    "against it. "
    * 4
    + "\n\n## The setting that matters\n\n```python\nllama = Llama(\n    model_path = "
    "model,\n    n_ctx = 32768,\n    rope_scaling = None,\n)\n```\n\n"
    "## What to change\n\nRaise `n_ctx` at load time; nothing later widens it.\n"
)
FIRST_DRAFT = TRUNCATED_DRAFT
SECOND_DRAFT = TRUNCATED_DRAFT

# The real marker from core/research/prompts.py; _select_synthesis_report extracts the
# report only from AFTER this line, so a stand-in that omits it returns nothing at all.
REPORT_BOUNDARY = "<!-- UNSLOTH_FINAL_REPORT -->"


def _sse(chunks: list[dict]) -> bytes:
    body = "".join(
        f"data: {json.dumps(chunk)}\n\n" for chunk in chunks
    ) + "data: [DONE]\n\n"
    return body.encode()


# What the whole report costs. Between the hardcoded 16384 and the connection's 32768, so
# the budget alone decides whether the run truncates.
REPORT_COST_TOKENS = 24_000
CONNECTION_MAX_OUTPUT_TOKENS = 32_768


def _chunks(text: str, finish_reason: str, completion_tokens: int = 16384) -> list[dict]:
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
            "usage": {"prompt_tokens": 2048, "completion_tokens": completion_tokens,
                      "total_tokens": 2048 + completion_tokens},
        },
    ]


class _StandIn(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _handler_class(calls: list[dict], completion_tokens: int = 16384):
    """An OpenAI-compatible endpoint that answers each research phase by what it is asked.

    The phase is read off the prompt rather than a counter, so a retry inside the
    supervisor cannot silently shift which canned answer a phase receives.
    """

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):  # noqa: D102 -- keep the driver's output readable
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
            except Exception:  # noqa: BLE001 -- an unreadable body is itself the record
                body = {}
            prompt = json.dumps(body.get("messages") or [])
            # Each phase is identified by a phrase only its own system prompt carries, so a
            # retry cannot shift which canned answer a phase receives. Only the report
            # prompt names the final-report boundary, and the recovery pass appends its own
            # "The previous synthesis ..." sentence to that same prompt.
            if REPORT_BOUNDARY in prompt:
                phase = ("synthesis_recovery" if "previous synthesis" in prompt.lower()
                         else "synthesis")
            elif "evidence-to-claim audit" in prompt:
                phase = "synthesis_audit"
            elif "web research plan" in prompt:
                phase = "planning"
            else:
                phase = "decision"
            calls.append({"phase": phase, "max_tokens": body.get("max_tokens")})

            if phase == "planning":
                # One step, because the planner rejects an empty list. The step never
                # gathers anything: web_search is proxied to a dead port on both sides.
                chunks = _chunks(json.dumps({
                    "title": "Context windows",
                    "steps": [{"title": "How n_ctx is chosen",
                               "query": "llama.cpp n_ctx context window"}],
                }), "stop")
            elif phase == "synthesis_audit":
                chunks = _chunks(json.dumps({
                    "thesis": "The window is fixed when the model is loaded.",
                    "outline": ["Findings", "The setting that matters"],
                    "supportedClaims": [],
                }), "stop")
            elif phase == "decision":
                # Unparseable on purpose: the loop falls back to the plan's seed action,
                # spends the single allowed step on it, and moves on to synthesis.
                chunks = _chunks("no action", "stop")
            elif phase in ("synthesis", "synthesis_recovery"):
                # A real provider stops at whatever max_tokens it was given. Below the
                # report's cost that is a truncated draft and `length`; at or above it the
                # report is written out and the turn ends naturally.
                asked = int(body.get("max_tokens") or 0)
                if asked >= REPORT_COST_TOKENS:
                    chunks = _chunks(f"{REPORT_BOUNDARY}\n{FULL_REPORT}", "stop",
                                     REPORT_COST_TOKENS)
                else:
                    chunks = _chunks(f"{REPORT_BOUNDARY}\n{TRUNCATED_DRAFT}", "length",
                                     asked or completion_tokens)

            payload = _sse(chunks)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def _start_stand_in(calls: list[dict],
                    completion_tokens: int = 16384) -> tuple[_StandIn, int]:
    port = pick_free_ports(1, start=9400, stop=9600)[0]
    server = _StandIn(("127.0.0.1", port), _handler_class(calls, completion_tokens))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def _create_provider(session: Session, port: int) -> str:
    made = api_post(session, "/api/providers/", {
        # "custom", not "openai": external_provider hard-routes provider_type "openai" to
        # /v1/responses, and the self-hosted types are the ones that stay on
        # /v1/chat/completions -- which is the endpoint this stand-in speaks.
        "provider_type": "custom",
        "display_name": "Research stand-in",
        "base_url": f"http://127.0.0.1:{port}/v1",
        "models": [STAND_IN_MODEL],
        "available_models": [STAND_IN_MODEL],
        "encrypted_api_key": "",
        # The Max Output Tokens field the connection dialog writes. BEFORE never reads it.
        "max_output_tokens": 32768,
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
                  provider_id: str, state: dict | None = None) -> dict:
    inference = {
        "model": STAND_IN_MODEL,
        "providerType": "custom",
        "providerId": provider_id,
        "externalModel": STAND_IN_MODEL,
        # What the chat path resolves for this connection and model and sends with the run.
        # The merge base has no such field and its route rejects the whole request for it,
        # which is exactly why its client never sent one; that side falls back below.
        "maxOutputTokens": CONNECTION_MAX_OUTPUT_TOKENS,
    }
    body = {
        "threadId": thread_id,
        "userMessageId": message_id,
        "question": QUESTION,
        "inferenceRequest": inference,
        "budgets": {"maxSteps": 1, "maxSources": 3,
                    "modelTimeoutSeconds": 60, "toolTimeoutSeconds": 20},
    }
    if state is not None:
        state["client_sent_ceiling"] = True
    try:
        created = api_post(session, "/api/chat/research-runs", body)
    except Exception as exc:
        # The old client could not send the ceiling, so neither does this side once the
        # server says it does not know the field. Anything else is a real failure.
        if "maxOutputTokens" not in str(exc):
            raise
        body["inferenceRequest"] = {k: v for k, v in inference.items() if k != "maxOutputTokens"}
        if state is not None:
            state["client_sent_ceiling"] = False
        created = api_post(session, "/api/chat/research-runs", body)
    run_id = created["id"]

    # Approve the plan as soon as the worker has written one; the run parks until then.
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


def _notice(report: str) -> str:
    """The "Incomplete report." blockquote the reader sees above the report, if any."""
    first = (report or "").lstrip().splitlines()[0] if (report or "").strip() else ""
    return first if first.startswith("> **Incomplete report.**") else ""


def _fence_balance(text: str) -> int:
    return len(re.findall(r"^ {0,3}```", text or "", flags=re.M))


async def drive(session: Session, out_dir: Path, label: str,
                completion_tokens: int = 16384,
                **_: object) -> tuple[list[Path], dict]:
    """`completion_tokens` is what the stand-in reports it produced.

    At the default it equals what synthesis asked for, which is the PR 10166 shape. Set it
    BELOW the request and the stand-in reports what a provider that stopped at its own
    output cap reports, which is the PR 10220 shape.
    """
    thread_id = f"uidiff-research-{RUN}"
    message_id = f"uidiff-msg-{RUN}"
    calls: list[dict] = []
    server, port = _start_stand_in(calls, completion_tokens)
    try:
        provider_id = _create_provider(session, port)
        _seed_thread(session, thread_id, message_id, STAND_IN_MODEL)
        run_state: dict = {}
        run = _run_research(session, thread_id, message_id, provider_id, run_state)

        report = run.get("report") or ""
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
            "report_delivered": bool(report),
            "report_chars": len(report),
            "report_ran_to_completion": "What to change" in report,
            "report_truncated_mid_fence": report.rstrip().endswith("rope_scaling ="),
            "has_incomplete_notice": "Incomplete report." in report,
            "code_fences_in_report": _fence_balance(report),
            "fences_balanced": _fence_balance(report) % 2 == 0,
            "notice_leads_report": report.lstrip().startswith("> **Incomplete report.**"),
            "notice_inside_a_code_block": "```" in report.partition(
                "> **Incomplete report.**")[0],
            "assistant_text_chars": len(shown),
            "assistant_shows_report": TRUNCATED_DRAFT[:60] in shown,
            "model_phases": [c["phase"] for c in calls],
            "recovery_pass_ran": any(c["phase"] == "synthesis_recovery" for c in calls),
            "truncation_notice": _notice(report),
            "notice_blames_local_context": "Increase Context Length" in report,
            "notice_names_a_local_model": "Local model report" in report,
            "connection_max_output_tokens": CONNECTION_MAX_OUTPUT_TOKENS,
            "client_sent_ceiling": run_state.get("client_sent_ceiling"),
            "synthesis_max_tokens": next(
                (c["max_tokens"] for c in calls if c["phase"] == "synthesis"), None),
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
            # domcontentloaded, not networkidle: the research run holds an SSE stream
            # open, so the network never goes idle and goto would always time out.
            await page.goto(f"{session.base_url}/chat?thread={thread_id}",
                            wait_until="domcontentloaded", timeout=90_000)
            question = page.get_by_text(QUESTION, exact=False).first
            await question.wait_for(state="visible", timeout=60_000)
            await page.wait_for_timeout(6_000)
            # Both sides framed from the top of the turn: the notice leads the report, so a
            # view anchored at the bottom would scroll the evidence off the top.
            await question.scroll_into_view_if_needed()
            await page.wait_for_timeout(1_000)
            shot = out_dir / f"{label}_research_report.png"
            await page.screenshot(path=str(shot), full_page=False)
            shots.append(shot)
        return shots, facts
    finally:
        server.shutdown()
        server.server_close()
