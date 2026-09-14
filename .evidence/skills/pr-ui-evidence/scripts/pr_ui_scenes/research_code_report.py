# Built on the stand-in server shape of research_truncated_report.py.
"""Scene: code blocks inside a delivered Deep Research report (PR 10814).

The run uses a saved `custom` connection served from this process, and both Studios are
launched with a `ddgs` stub on PYTHONPATH, so the single research step gathers one fixed
web source without touching the network. Synthesis returns a report whose fenced,
indented and inline code contains URLs and bracketed indexes. BEFORE the citation
validator rewrites that code as if it were prose; AFTER the code is delivered as written.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post, pick_free_ports  # noqa: E402
from pr_ui_scenes.research_truncated_report import (  # noqa: E402
    REPORT_BOUNDARY,
    STAND_IN_MODEL,
    _StandIn,
    _api_put,
    _chunks,
    _create_provider,
    _sse,
)
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

RUN = uuid.uuid4().hex[:10]
CREATED_AT = 1_755_000_000_000
QUESTION = "How do I run Unsloth Studio locally and call it from the OpenAI client?"
DDGS_LOG = Path(os.environ.get("PR10814_DDGS_LOG", "/tmp/pr10814_ddgs_calls.log"))
SOURCE_URL = "https://github.com/unslothai/unsloth"
SOURCE_LINK = f"[unslothai/unsloth]({SOURCE_URL})"

BASH = (
    "```bash\n"
    "git clone https://github.com/unslothai/unsloth\n"
    "pip install torch --index-url https://download.pytorch.org/whl/cu121\n"
    "curl http://localhost:8888/api/health\n"
    "```"
)
PYTHON = (
    "```python\n"
    "from openai import OpenAI\n"
    'client = OpenAI(base_url="http://localhost:8888/v1", api_key="none")\n'
    "print(x.shape[1])\n"
    'pattern = "[Document: generated]"\n'
    "```"
)
INDENTED = "    wget https://huggingface.co/unsloth/model.gguf"
INLINE = ["`curl http://localhost:8888/api/health`", "`sys.argv[1]`"]
REPORT = f"""## Running Unsloth Studio locally

Clone the repository and install a CUDA build of PyTorch [1]:

{BASH}

Point an OpenAI-compatible client at the local server:

{PYTHON}

Download a model:

{INDENTED}

Check health with {INLINE[0]} and read {INLINE[1]}.

An unverified blog https://made-up.example/blog also claims this.

## Sources
1. [Unsloth](https://github.com/unslothai/unsloth)
"""
CODE_LINES = [
    line
    for block in (BASH, PYTHON, INDENTED)
    for line in block.splitlines()
    if not line.startswith("```")
] + INLINE


def _handler_class(calls: list[dict]):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
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
                    "title": "Running Unsloth Studio",
                    "steps": [{"title": "Install and call Studio",
                               "query": "unsloth studio install openai client"}],
                }), "stop", 200)
            elif phase == "synthesis_audit":
                chunks = _chunks(json.dumps({
                    "thesis": "Studio runs locally and serves an OpenAI-compatible API.",
                    "outline": ["Running Unsloth Studio locally"],
                    "supportedClaims": [],
                }), "stop", 200)
            elif phase == "decision":
                chunks = _chunks("no action", "stop", 10)
            else:
                chunks = _chunks(f"{REPORT_BOUNDARY}\n{REPORT}", "stop", 900)

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


def _seed_thread(session: Session, thread_id: str, message_id: str) -> None:
    api_post(session, "/api/chat/threads", {
        "id": thread_id, "title": "Deep Research", "modelType": "base",
        "modelId": STAND_IN_MODEL, "archived": False,
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


def _broken(expected: list[str], lines: list[str], strip: bool) -> list[dict]:
    out = []
    pool = [l.strip() if strip else l for l in lines]
    for line in expected:
        want = line.strip() if strip else line
        if want in pool:
            continue
        prefix = want.strip("`")[:12]
        got = next((l for l in pool if l and prefix in l), None)
        out.append({"expected": want, "got": got})
    return out


_CLIP_JS = """() => {
  const pres = [...document.querySelectorAll('pre')];
  if (!pres.length) return null;
  const first = pres[0];
  const inline = [...document.querySelectorAll('code')]
    .filter(c => !c.closest('pre') && c.textContent.includes('sys.argv'));
  const last = inline.length ? inline[inline.length - 1] : pres[pres.length - 1];
  const seen = new Set();
  for (let n = first; n; n = n.parentElement) seen.add(n);
  let lca = last;
  while (lca && !seen.has(lca)) lca = lca.parentElement;
  const box = (lca || document.body).getBoundingClientRect();
  const top = first.getBoundingClientRect().top - 56;
  const bottom = last.getBoundingClientRect().bottom + 20;
  return {x: Math.max(0, box.left - 16), y: top + window.scrollY,
          width: box.width + 32, height: bottom - top};
}"""


async def drive(session: Session, out_dir: Path, label: str,
                **_: object) -> tuple[list[Path], dict]:
    thread_id = f"uidiff-code-{RUN}-{label.lower()}"
    message_id = f"uidiff-msg-{RUN}-{label.lower()}"
    DDGS_LOG.unlink(missing_ok=True)
    calls: list[dict] = []
    server, port = _start_stand_in(calls)
    try:
        provider_id = _create_provider(session, port)
        _seed_thread(session, thread_id, message_id)
        run = _run_research(session, thread_id, message_id, provider_id)
        report = run.get("report") or ""
        report_lines = report.splitlines()
        report_broken = _broken(CODE_LINES[:-2], report_lines, strip=False) + [
            {"expected": s, "got": None} for s in INLINE if s not in report
        ]

        auth_script = seed_init_script(
            type("A", (), {"access_token": session.access_token,
                           "refresh_token": session.refresh_token})(), [],
        )
        shots: list[Path] = []
        async with open_chat(session.base_url, init_scripts=[auth_script],
                             viewport=(1280, 1100), headless=True) as sp:
            page = sp.page
            await page.goto(f"{session.base_url}/chat?thread={thread_id}",
                            wait_until="domcontentloaded", timeout=90_000)
            await page.get_by_text(QUESTION, exact=False).first.wait_for(
                state="visible", timeout=60_000)
            first_pre = page.locator("pre").first
            await first_pre.wait_for(state="visible", timeout=90_000)
            await page.wait_for_timeout(3_000)
            pre_texts = await page.locator("pre").all_inner_texts()
            inline_texts = await page.locator("code:not(pre code)").all_inner_texts()
            dom_lines = [l for t in pre_texts for l in t.splitlines()] + inline_texts
            ui_expected = [l for l in CODE_LINES[:-2]] + [s.strip("`") for s in INLINE]
            ui_broken = _broken(ui_expected, dom_lines, strip=True)
            completed_banner = await page.get_by_text("Deep research completed").count()

            await first_pre.scroll_into_view_if_needed()
            await page.wait_for_timeout(800)
            clip = await page.evaluate(_CLIP_JS)
            shot = out_dir / f"{label}_research_code_report.png"
            if clip:
                await page.screenshot(path=str(shot), full_page=True, clip=clip)
            else:
                await page.screenshot(path=str(shot), full_page=False)
            shots.append(shot)

        search_calls = DDGS_LOG.read_text().count("\n") if DDGS_LOG.exists() else 0
        facts = {
            "run_status": run.get("status"),
            "run_error": (run.get("error") or "")[:200],
            "report_delivered": bool(report),
            "sources": [s.get("url") for s in (run.get("sources") or []) if isinstance(s, dict)],
            "ddgs_stub_calls": search_calls,
            "model_phases": [c["phase"] for c in calls],
            "code_lines_total": len(CODE_LINES),
            "code_lines_kept_in_report": len(CODE_LINES) - len(report_broken),
            "report_broken_code": report_broken,
            "code_lines_kept_in_ui": len(ui_expected) - len(ui_broken),
            "ui_broken_code": ui_broken,
            "ui_code_blocks": len(pre_texts),
            "ui_completed_banner": completed_banner > 0,
            "prose_citation_linked": f"PyTorch {SOURCE_LINK}" in report,
            "unverified_prose_url_removed": "https://made-up.example/blog" not in report,
            "model_sources_section_removed": "## Sources" not in report,
            "report_sha": hashlib.sha256(report.encode()).hexdigest()[:16],
        }
        return shots, facts
    finally:
        server.shutdown()
        server.server_close()
