# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: arming Deep Research and then saying "hi" (PR 9726).

A chat gets one Deep Research run. Arming it in the composer used to create that run before
the model had read anything, so a greeting spent it: the reply became a research card and the
pill went away for the rest of the conversation. The PR offers the model a ``deep_research``
tool instead and lets it decide, so a greeting is answered as a greeting and the run is still
there for the question that wants it.

The model is a browser-side fixture, so nothing here needs weights, a GPU or a download, and
the two sides differ only in the separately built Studio they are talking to:

  * ``/api/inference/status`` reports a resident, tool-capable ``unsloth/Qwen3-1.7B-GGUF``,
    which is what lights the composer's Deep research entry.
  * ``/v1/chat/completions`` records the request body and answers the greeting in plain text,
    calling no tool -- which is exactly what the PR's nudge and tool description ask a model
    to do with "hi", and what the author reported the real Qwen3-1.7B doing.

Everything else -- the thread, the messages, ``/api/chat/research-runs`` -- is the real
backend on the same install that was photographed, so "was a run created" is read from the
server rather than inferred from the screen.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, open_menu  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

COMPOSER = 'textarea[aria-label="Message input"]'
SEND = 'button[aria-label="Send message"]'
TOOLS_MENU = 'button[aria-label="Tools and attachments"]'
RESEARCH_PILL = 'button[aria-label="Configure Deep Research website access"]'

GREETING = "hi"
REPLY = "Hey! What would you like to look into?"


def _fixture_script(status: dict) -> str:
    """Answer status and the model; record, but never intercept, the research API."""
    return f"""
(() => {{
  const statusFixture = {json.dumps(status)};
  const REPLY = {json.dumps(REPLY)};
  const realFetch = window.fetch.bind(window);
  window.__pr9726 = {{ completions: [], researchPosts: [] }};

  const sse = (chunks) => new ReadableStream({{
    start(controller) {{
      const encode = new TextEncoder();
      for (const chunk of chunks) controller.enqueue(encode.encode(chunk));
      controller.close();
    }},
  }});

  window.fetch = async (input, init) => {{
    const raw = typeof input === "string" ? input
      : input instanceof Request ? input.url : String(input);
    const url = new URL(raw, window.location.origin);
    const method = ((init && init.method) || "GET").toUpperCase();

    if (url.pathname === "/api/inference/status") {{
      return new Response(JSON.stringify(statusFixture), {{
        status: 200, headers: {{ "content-type": "application/json" }} }});
    }}
    // The context meter would price a prompt against a model that is not really there.
    if (url.pathname === "/api/inference/chat/count_tokens") {{
      return new Response(JSON.stringify({{ detail: "count disabled by evidence fixture" }}), {{
        status: 503, headers: {{ "content-type": "application/json" }} }});
    }}
    // Recorded and forwarded: whether a run is created is the real backend's answer, not
    // this fixture's, and the card BEFORE paints has to come from a real run.
    if (url.pathname === "/api/chat/research-runs" && method === "POST") {{
      let body = {{}};
      try {{ body = JSON.parse((init && init.body) || "{{}}"); }} catch {{ /* opaque */ }}
      window.__pr9726.researchPosts.push({{
        userMessageId: body.userMessageId || null,
        question: body.question ?? null,
      }});
      return realFetch(input, init);
    }}
    if (url.pathname !== "/v1/chat/completions") {{
      return realFetch(input, init);
    }}

    const body = JSON.parse((init && init.body) || "{{}}");
    window.__pr9726.completions.push({{
      deep_research_armed: body.deep_research_armed ?? null,
      enable_tools: body.enable_tools ?? null,
      enabled_tools: body.enabled_tools ?? null,
      last_user: (body.messages || []).filter((m) => m.role === "user").map((m) => m.content).pop(),
    }});
    const frames = REPLY.split(" ").map((word, at) => "data: " + JSON.stringify({{
      id: "evidence", object: "chat.completion.chunk", model: statusFixture.active_model,
      choices: [{{ index: 0, delta: at === 0 ? {{ role: "assistant", content: word }}
                                             : {{ content: " " + word }} }}],
    }}) + "\\n\\n");
    frames.push("data: " + JSON.stringify({{ id: "evidence",
      object: "chat.completion.chunk", model: statusFixture.active_model,
      choices: [{{ index: 0, delta: {{}}, finish_reason: "stop" }}] }}) + "\\n\\n");
    frames.push("data: [DONE]\\n\\n");
    return new Response(sse(frames), {{
      status: 200, headers: {{ "content-type": "text/event-stream" }} }});
  }};
}})();
"""


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    *,
    model: str = "unsloth/Qwen3-1.7B-GGUF",
    variant: str = "Q4_K_M",
    context_length: int = 8192,
    **_: object,
) -> tuple[list[Path], dict]:
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
        "supports_reasoning": True,
        # The whole point: the composer only offers Deep research to a tool-capable model,
        # and the PR's handoff rides the tool loop.
        "supports_tools": True,
        "context_length": context_length,
        "max_context_length": context_length,
        "native_context_length": context_length,
        "gpu_memory_mode": "manual",
        "gpu_layers": 0,
        "requested_context_length": context_length,
        "requested_parallel_slots": 1,
        "parallel_slots": 1,
    }
    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(),
        [],
    )

    facts: dict = {"model": model, "variant": variant, "greeting": GREETING}
    shots: list[Path] = []

    async with open_chat(
        session.base_url,
        init_scripts=[auth_script, _fixture_script(status)],
        viewport=(1280, 900),
        headless=True,
    ) as sp:
        page = sp.page
        composer = page.locator(COMPOSER)
        await composer.wait_for(state="visible", timeout=90_000)
        await page.wait_for_function(
            "model => document.body.innerText.includes(model.split('/').pop())",
            arg=model,
            timeout=90_000,
        )

        # 1. A chat of its own. A reused home still holds the last run's thread, and this
        #    scene is about a thread whose one research is untouched.
        await page.get_by_role("button", name="New chat").first.click()
        await composer.wait_for(state="visible", timeout=60_000)
        await page.wait_for_timeout(1_500)

        # 2. Arm Deep Research, and prove it armed: the pill is the only thing that says so,
        #    and a missed menu item looks like a clean run. The toggle is remembered, and
        #    after this PR it survives a run, so clicking unconditionally can DISARM it.
        pill = page.locator(RESEARCH_PILL)
        facts["armed_before_opening_menu"] = await pill.is_visible()
        if not facts["armed_before_opening_menu"]:
            await open_menu(
                page,
                page.locator(TOOLS_MENU),
                page.get_by_role("menuitem", name="Deep research"),
            )
            await page.get_by_role("menuitem", name="Deep research").click()
        await pill.wait_for(state="visible", timeout=30_000)
        facts["armed_before_sending"] = True

        # 3. Say hello. This is the message the PR is about.
        await composer.click()
        await composer.fill(GREETING)
        await page.locator(SEND).click()
        await page.wait_for_selector(
            'button[aria-label="Stop generating"]', state="detached", timeout=120_000
        )
        await page.wait_for_timeout(4_000)

        wire = await page.evaluate("() => window.__pr9726")
        facts["completion_requests"] = len(wire["completions"])
        facts["research_run_posts"] = len(wire["researchPosts"])
        facts["completion_bodies"] = wire["completions"]
        facts["research_post_bodies"] = wire["researchPosts"]
        facts["deep_research_armed_on_wire"] = (
            wire["completions"][0]["deep_research_armed"] if wire["completions"] else None
        )
        facts["enable_tools_on_wire"] = (
            wire["completions"][0]["enable_tools"] if wire["completions"] else None
        )

        transcript = await page.locator("main").first.inner_text()
        facts["reply_text_visible"] = REPLY.split("!")[0] in transcript
        facts["research_card_visible"] = "esearch" in transcript
        # The pill is the offer. Gone means this chat's one research was spent on "hi".
        facts["research_still_offered"] = await pill.is_visible()

        shot = out_dir / f"{label.lower()}_deep_research_greeting.png"
        await page.screenshot(path=str(shot), full_page=False)
        shots.append(shot)

    # Read from the server that was photographed, not from the screen.
    threads = api_get(session, "/api/chat/threads?limit=5")
    rows = threads.get("threads") or threads.get("items") or []
    facts["thread_count"] = len(rows)
    thread_id = rows[0]["id"] if rows else None
    facts["thread_id_present"] = bool(thread_id)
    if thread_id:
        active = api_get(session, f"/api/chat/research-runs/active?threadId={thread_id}")
        facts["server_runs_for_greeting"] = len(active.get("runs") or [])
        facts["server_has_run"] = bool(active.get("hasRun"))
        facts["server_run_statuses"] = [
            run.get("status") for run in (active.get("runs") or [])
        ]
    return shots, facts
