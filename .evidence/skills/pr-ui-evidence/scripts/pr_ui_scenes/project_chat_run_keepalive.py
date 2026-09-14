# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: a project chat's run, after the user opens another chat mid-response.

#8908. The browser gets two deterministic fixtures before the SPA boots: a resident
GGUF on ``/api/inference/status`` so the composer can send without weights, and a
``/v1/chat/completions`` stream that emits a fixed number of numbered tokens on a
fixed cadence and **ends the moment its request is aborted**, exactly as the backend
does when the client disconnects.

That makes the abort observable as a number rather than a guess: the fixture records
how many of its tokens it managed to deliver and whether it was cut off. Sending in a
project, clicking another chat in the sidebar mid-stream, then coming back therefore
answers one question quantitatively -- did the run survive the navigation?
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

# The long answer's tokens. Numbered so a truncated transcript names exactly where it
# stopped, and so the tail is a hard equality check rather than a length heuristic.
LONG_TOKENS = 40
SHORT_TOKENS = 4


def _fixture_script(model: str, variant: str, context_length: int,
                    chunk_ms: int) -> str:
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
        "supports_tools": False,
        "context_length": context_length,
        "max_context_length": context_length,
        "native_context_length": context_length,
        "gpu_memory_mode": "manual",
        "gpu_layers": 0,
        "requested_context_length": context_length,
        "requested_parallel_slots": 1,
        "parallel_slots": 1,
    }
    return f"""
(() => {{
  const statusFixture = {json.dumps(status)};
  const realFetch = window.fetch.bind(window);
  const state = {{ runs: [] }};
  window.__pr9129 = state;
  window.__pr9129Run = (tag) => state.runs.find((r) => r.tag === tag) || null;

  const sse = (payload) => new TextEncoder().encode("data: " + JSON.stringify(payload) + "\\n\\n");
  const done = () => new TextEncoder().encode("data: [DONE]\\n\\n");

  window.fetch = async (input, init) => {{
    const raw = typeof input === "string" ? input
      : input instanceof Request ? input.url : String(input);
    const url = new URL(raw, window.location.origin);

    if (url.pathname === "/api/inference/status") {{
      return new Response(JSON.stringify(statusFixture), {{
        status: 200, headers: {{"content-type": "application/json"}},
      }});
    }}

    if (url.pathname === "/v1/chat/completions") {{
      // Which run this is, read from the prompt the composer actually sent, so the
      // decoy chat finishes immediately and the project chat is the slow one.
      let body = {{}};
      try {{ body = JSON.parse(typeof init?.body === "string" ? init.body : "{{}}"); }} catch (e) {{}}
      const text = JSON.stringify(body.messages || []);
      const tag = text.includes("PR9129-LONG") ? "long"
        : text.includes("PR9129-SHORT") ? "short" : "other";
      const total = tag === "long" ? {LONG_TOKENS} : {SHORT_TOKENS};
      const gap = tag === "long" ? {chunk_ms} : 40;
      const run = {{ tag, total, delivered: 0, completed: false, aborted: false,
                    abortedAfter: null, startedAt: Date.now() }};
      state.runs.push(run);

      const signal = init && init.signal;
      let timer = null;
      let closed = false;
      const stream = new ReadableStream({{
        start(controller) {{
          const stop = (why) => {{
            if (closed) return;
            closed = true;
            if (timer) clearInterval(timer);
            // The backend stops generating when the client disconnects; the socket
            // dies without a terminal event, so error the body the same way.
            run.aborted = true;
            run.abortedAfter = run.delivered;
            try {{ controller.error(new DOMException(why, "AbortError")); }} catch (e) {{}}
          }};
          if (signal) {{
            if (signal.aborted) {{ stop("aborted before start"); return; }}
            signal.addEventListener("abort", () => stop("request aborted"), {{ once: true }});
          }}
          timer = setInterval(() => {{
            if (closed) return;
            if (run.delivered >= total) {{
              clearInterval(timer);
              closed = true;
              controller.enqueue(sse({{
                id: "pr9129", object: "chat.completion.chunk", model: "{model}",
                choices: [{{index: 0, delta: {{}}, finish_reason: "stop"}}],
              }}));
              controller.enqueue(done());
              run.completed = true;
              controller.close();
              return;
            }}
            run.delivered += 1;
            const word = "w" + String(run.delivered).padStart(2, "0");
            controller.enqueue(sse({{
              id: "pr9129", object: "chat.completion.chunk", model: "{model}",
              choices: [{{index: 0, delta: {{content: word + " "}}, finish_reason: null}}],
            }}));
          }}, gap);
        }},
        cancel() {{
          closed = true;
          if (timer) clearInterval(timer);
          if (!run.completed) {{
            run.aborted = true;
            if (run.abortedAfter === null) run.abortedAfter = run.delivered;
          }}
        }},
      }});
      return new Response(stream, {{
        status: 200,
        headers: {{"content-type": "text/event-stream", "cache-control": "no-cache"}},
      }});
    }}

    return realFetch(input, init);
  }};
}})();
"""


async def _send(page, text: str) -> None:
    box = page.locator("form:has(textarea) textarea").first
    await box.click()
    await box.fill(text)
    await box.press("Enter")


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    *,
    model: str = "unsloth/gemma-3-270m-it-GGUF",
    variant: str = "Q4_K_M",
    context_length: int = 32768,
    chunk_ms: int = 300,
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph a project chat after the user visits another chat mid-response."""
    project_id = f"pr9129-{uuid.uuid4().hex[:8]}"
    now = int(time.time() * 1000)
    api_post(session, "/api/chat/projects", {
        "id": project_id, "name": "Keepalive project", "archived": False,
        "createdAt": now, "updatedAt": now,
    })

    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(),
        [],
    )
    fixture = _fixture_script(model, variant, context_length, chunk_ms)

    async with open_chat(
        session.base_url,
        init_scripts=[auth_script, fixture],
        viewport=(1500, 950),
        headless=True,
    ) as sp:
        page = sp.page

        # 1) A decoy chat outside the project, created through the real composer so
        #    its sidebar row is the one a user would click.
        await page.locator("form:has(textarea) textarea").first.wait_for(
            state="visible", timeout=60_000)
        await _send(page, "PR9129-SHORT decoy")
        await page.wait_for_function(
            "() => (window.__pr9129Run('short') || {}).completed === true",
            timeout=90_000)
        decoy_row = page.locator('[data-testid="recent-thread"]').first
        await decoy_row.wait_for(state="visible", timeout=60_000)
        decoy_id = await decoy_row.get_attribute("data-thread-id")

        # 2) The project landing, and a long answer started from its own composer.
        await page.goto(f"{session.base_url}/chat?project={project_id}",
                        wait_until="domcontentloaded")
        await page.locator("form:has(textarea) textarea").first.wait_for(
            state="visible", timeout=60_000)
        await _send(page, "PR9129-LONG write the numbered list")
        await page.wait_for_function(
            "() => ((window.__pr9129Run('long') || {}).delivered || 0) >= 5",
            timeout=90_000)

        # 3) Leave for the decoy chat MID-RESPONSE. An in-app row click, never a
        #    page load: a reload would abort the run on both sides and prove nothing.
        await page.locator(
            f'[data-testid="recent-thread"][data-thread-id="{decoy_id}"]'
        ).first.click(timeout=30_000)
        await page.wait_for_function(
            "() => new URLSearchParams(location.search).get('thread') !== null",
            timeout=30_000)
        left_after = await page.evaluate(
            "() => (window.__pr9129Run('long') || {}).delivered || 0")

        # 4) Give the run its full remaining time either way.
        try:
            await page.wait_for_function(
                "() => { const r = window.__pr9129Run('long') || {};"
                "        return r.completed === true || r.aborted === true; }",
                timeout=(LONG_TOKENS + 10) * chunk_ms + 20_000)
        except Exception:  # noqa: BLE001 -- report the stall as the finding
            pass
        await page.wait_for_timeout(2_000)
        run = await page.evaluate("() => window.__pr9129Run('long')") or {}

        # 5) Back to the project's chat to read what it actually holds.
        threads = api_get(session, f"/api/chat/threads?project_id={project_id}")
        rows = threads.get("threads") if isinstance(threads, dict) else None
        project_thread_id = rows[0]["id"] if rows else None
        stored_words = 0
        if project_thread_id:
            msgs = api_get(
                session, f"/api/chat/threads/{project_thread_id}/messages")
            texts = [
                json.dumps(m.get("content"))
                for m in (msgs.get("messages") or [])
                if m.get("role") == "assistant"
            ]
            stored_words = len(re.findall(r"\bw\d\d\b", " ".join(texts)))
            await page.goto(
                f"{session.base_url}/chat?thread={project_thread_id}&project={project_id}",
                wait_until="domcontentloaded")
            await page.locator("form:has(textarea) textarea").first.wait_for(
                state="visible", timeout=60_000)
            await page.wait_for_timeout(3_000)

        body_text = await page.evaluate("() => document.body.innerText")
        shown = re.findall(r"\bw\d\d\b", body_text)
        shot = out_dir / f"{label.lower()}_project_chat_run_keepalive.png"
        await page.screenshot(path=str(shot), full_page=False)

        facts = {
            "tokens_the_answer_has": LONG_TOKENS,
            "tokens_delivered_when_user_left": left_after,
            "tokens_delivered_in_total": run.get("delivered"),
            "run_aborted_by_the_navigation": bool(run.get("aborted")),
            "run_reached_its_last_token": bool(run.get("completed")),
            "aborted_after_n_tokens": run.get("abortedAfter"),
            "tokens_visible_in_the_returned_chat": len(shown),
            "last_token_visible": shown[-1] if shown else None,
            "tokens_persisted_for_the_chat": stored_words,
            "response_stopped_notice": "Response stopped." in body_text,
        }
        return [shot], facts
