# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: pressing Stop before a turn produces anything, then sending again (PR 9489, issue 9484).

The reporter's steps, driven through the real composer: send a prompt, press Stop before a
single token arrives, send the same prompt again. What the second send puts on the wire is the
whole question, so the fixture RECORDS every ``/v1/chat/completions`` body and answers the way
the strict local template in the report does.

Everything the browser talks to is a fixture, so nothing here depends on weights, a GPU, or a
download; the only thing that differs between the two shots is the separately built frontend.

  * ``/api/inference/status`` reports a resident ``unsloth/gemma-3-270m-it-GGUF``.
  * The first completion holds the stream open and emits NOTHING, which is what makes Stop
    land on a turn with no output -- the exact shape #9484 is about.
  * The second completion is answered like llama-server answers it: the Stop sentinel (an
    assistant turn with no content, no tool_calls and no reasoning_content) is dropped, as
    ``_normalize_local_assistant_message`` does, and the remaining roles are checked for
    alternation, as the vendored ``gemma3_template`` does. Two user turns in a row get the
    report's own 500; an alternating history gets a reply.

The fixture forwards the messages verbatim, which is the tool-passthrough contract the
reporter's log came through (``_openai_messages_for_gguf_chat`` coalesces instead, where the
same history costs a duplicated prompt rather than an error). Either way the recorded request
body is the fact this PR moves, and it is returned in the scene facts.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

# Passed as arrow functions, never bare expressions: the page ships script-src 'self'
# with no unsafe-eval, and Playwright evaluates a bare expression string with eval().
COMPOSER = 'textarea[aria-label="Message input"]'
SEND = 'button[aria-label="Send message"]'
STOP = 'button[aria-label="Stop generating"]'

PROMPT = "Write a haiku about the sea"

# The message llama-server returns for this history, from the issue's llama-server log.
TEMPLATE_ERROR = (
    "Jinja Exception: After the optional system message, conversation roles must "
    "alternate user and assistant roles except for tool calls and results."
)


def _fixture_script(status: dict) -> str:
    return f"""
(() => {{
  const statusFixture = {json.dumps(status)};
  const TEMPLATE_ERROR = {json.dumps(TEMPLATE_ERROR)};
  const realFetch = window.fetch.bind(window);
  window.__pr9489 = {{ requests: [], responses: [] }};

  const sse = (chunks) => new ReadableStream({{
    start(controller) {{
      const encode = new TextEncoder();
      for (const chunk of chunks) controller.enqueue(encode.encode(chunk));
      controller.close();
    }},
  }});

  // A stream that never yields and never ends: the turn is live, nothing has arrived, and
  // Stop is the only thing that can end it. Exactly the state #9484 starts from.
  const silence = (signal) => new ReadableStream({{
    start(controller) {{
      if (signal) signal.addEventListener("abort", () => {{
        try {{ controller.close(); }} catch {{ /* already closed */ }}
      }});
    }},
  }});

  // routes/inference.py::_normalize_local_assistant_message -- a bare assistant turn is a
  // Stop sentinel and never reaches the template.
  const dropSentinels = (messages) => messages.filter((m) =>
    !(m.role === "assistant" && !m.content && !m.tool_calls && !m.reasoning_content));

  window.fetch = async (input, init) => {{
    const raw = typeof input === "string" ? input
      : input instanceof Request ? input.url : String(input);
    const url = new URL(raw, window.location.origin);

    if (url.pathname === "/api/inference/status") {{
      return new Response(JSON.stringify(statusFixture), {{
        status: 200, headers: {{ "content-type": "application/json" }} }});
    }}
    if (url.pathname === "/api/inference/chat/count_tokens") {{
      return new Response(JSON.stringify({{ detail: "count disabled by evidence fixture" }}), {{
        status: 503, headers: {{ "content-type": "application/json" }} }});
    }}
    if (url.pathname !== "/v1/chat/completions") {{
      return realFetch(input, init);
    }}

    const body = JSON.parse((init && init.body) || "{{}}");
    const index = window.__pr9489.requests.length;
    window.__pr9489.requests.push(
      (body.messages || []).map((m) => ({{ role: m.role, content: m.content }})));

    if (index === 0) {{
      window.__pr9489.responses.push({{ status: 200, kind: "held-open-no-output" }});
      return new Response(silence(init && init.signal), {{
        status: 200, headers: {{ "content-type": "text/event-stream" }} }});
    }}

    const roles = dropSentinels(body.messages || []).map((m) => m.role);
    const adjacent = roles.some((r, i) => i > 0 && r === "user" && roles[i - 1] === "user");
    if (adjacent) {{
      window.__pr9489.responses.push({{ status: 500, kind: "template-refused", roles }});
      return new Response(JSON.stringify({{ error: {{
        code: 500, type: "server_error", message: "\\n" + TEMPLATE_ERROR }} }}), {{
        status: 500, headers: {{ "content-type": "application/json" }} }});
    }}
    window.__pr9489.responses.push({{ status: 200, kind: "answered", roles }});
    const reply = "Salt on the grey swell / a gull leans into the wind / the shore lets it go";
    const frames = reply.split(" ").map((word, at) => "data: " + JSON.stringify({{
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
    model: str = "unsloth/gemma-3-270m-it-GGUF",
    variant: str = "Q4_K_M",
    context_length: int = 32768,
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
    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(),
        [],
    )

    facts: dict = {"model": model, "variant": variant, "prompt": PROMPT}
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

        # 1. Send, and Stop the moment the request is in flight -- before any token exists.
        await composer.click()
        await composer.fill(PROMPT)
        await page.locator(SEND).click()
        await page.wait_for_function("() => window.__pr9489.requests.length === 1", timeout=60_000)
        stop = page.locator(STOP)
        await stop.wait_for(state="visible", timeout=60_000)
        await stop.click()
        await page.wait_for_selector(STOP, state="detached", timeout=60_000)
        await page.wait_for_timeout(1_500)
        facts["first_request"] = await page.evaluate("() => window.__pr9489.requests[0]")

        # 2. Send the same prompt again. This is the request the PR changes.
        await composer.click()
        await composer.fill(PROMPT)
        await page.locator(SEND).click()
        await page.wait_for_function("() => window.__pr9489.requests.length === 2", timeout=60_000)
        await page.wait_for_selector(STOP, state="detached", timeout=60_000)
        await page.wait_for_timeout(2_500)

        second = await page.evaluate("() => window.__pr9489.requests[1]")
        facts["second_request"] = second
        facts["second_request_roles"] = [m["role"] for m in second]
        facts["second_request_user_turns"] = sum(1 for m in second if m["role"] == "user")
        facts["second_request_adjacent_user_turns"] = any(
            a["role"] == "user" and b["role"] == "user" for a, b in zip(second, second[1:])
        )
        facts["responses"] = await page.evaluate("() => window.__pr9489.responses")
        facts["backend_verdict"] = facts["responses"][1]["kind"]

        transcript = await page.locator("main").first.inner_text()
        facts["template_error_visible"] = "roles must alternate" in transcript
        facts["reply_visible"] = "gull leans into the wind" in transcript

        shot = out_dir / f"{label.lower()}_cancelled_turn_resend.png"
        await page.screenshot(path=str(shot), full_page=False)
        shots.append(shot)

    return shots, facts
