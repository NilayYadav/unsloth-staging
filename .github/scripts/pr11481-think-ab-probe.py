#!/usr/bin/env python3
"""PR 11481 probe: literal <think> text with Thinking off stays one reply in the chat UI.

Exit 0 = PASS, 1 = FAIL at the PR assertion, 2 = harness/setup problem (not evidence).
Never prints passwords or tokens.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_test_kit.auth import StudioAuth, seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat, pick_model, send_prompt, wait_for_stream  # noqa: E402

MODEL = "unsloth/gemma-3-270m-it-GGUF"
VARIANT = "Q4_K_M"
ANSWER = "Use <think>hi</think> in your prompt."
PROMPT = "Show me a think tag."
TEMPLATE = (
    "{%- set _think = enable_thinking | default(true) -%}\n"
    "<start_of_turn>user\n" + PROMPT + "<end_of_turn>\n"
    "<start_of_turn>model\n" + ANSWER + "<end_of_turn>\n"
    "<start_of_turn>user\n" + PROMPT + "<end_of_turn>\n"
    "<start_of_turn>model\n" + ANSWER + "<end_of_turn>\n"
    "{% for message in messages %}"
    "<start_of_turn>{{ 'model' if message['role'] == 'assistant' else 'user' }}\n"
    "{{ message['content'] }}<end_of_turn>\n"
    "{% endfor %}<start_of_turn>model\n"
)
NEW_PASSWORD = "UnslothStudioCI2026!"


def log(msg: str) -> None:
    print(msg, flush=True)


def harness(msg: str) -> None:
    print(f"HARNESS {msg}", flush=True)
    raise SystemExit(2)


def req(base: str, path: str, payload: dict | None = None, token: str | None = None,
        timeout: int = 1800) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(f"{base}{path}", data=data, headers=headers,
                               method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def find_bin(home: Path) -> Path:
    for c in [home / "bin" / "unsloth", home / "unsloth_studio" / "bin" / "unsloth",
              *home.glob(".venv*/*/unsloth")]:
        if c.is_file():
            return c
    harness(f"no unsloth CLI under {home}")


async def run() -> int:
    home = Path(os.environ["UNSLOTH_STUDIO_HOME"]).resolve()
    art = Path(os.environ.get("STUDIO_ARTIFACT_DIR", "artifacts")).resolve()
    art.mkdir(parents=True, exist_ok=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    logf = (art / "studio.log").open("w")
    proc = subprocess.Popen([str(find_bin(home)), "studio", "-H", "127.0.0.1", "-p", str(port)],
                            stdout=logf, stderr=subprocess.STDOUT, start_new_session=True,
                            env={**os.environ, "UNSLOTH_STUDIO_HOME": str(home)})
    try:
        deadline = time.time() + 300
        while True:
            try:
                urllib.request.urlopen(f"{base}/healthz", timeout=3)
                break
            except (urllib.error.URLError, OSError):
                if time.time() > deadline:
                    harness("Studio never became healthy")
                time.sleep(2)
        boot = home / "auth" / ".bootstrap_password"
        for _ in range(60):
            if boot.exists() and boot.read_text().strip():
                break
            time.sleep(1)
        else:
            harness("no bootstrap password")
        bootstrap = boot.read_text().strip()
        tok = req(base, "/api/auth/login", {"username": "unsloth", "password": bootstrap})
        tok = req(base, "/api/auth/change-password",
                  {"current_password": bootstrap, "new_password": NEW_PASSWORD},
                  token=tok["access_token"])
        token = tok["access_token"]
        log("SETUP login ok")

        req(base, "/api/inference/load", {"model_path": MODEL, "gguf_variant": VARIANT,
                                          "max_seq_length": 2048, "n_parallel": 1,
                                          "chat_template_override": TEMPLATE}, token=token)
        deadline = time.time() + 1800
        while True:
            st = req(base, "/api/inference/status", token=token)
            if st.get("active_model") and not st.get("loading"):
                break
            if time.time() > deadline:
                harness("model never loaded")
            time.sleep(3)
        model_id = st["active_model"]
        log(f"SETUP loaded {model_id} reasoning_style={st.get('reasoning_style')}")
        if st.get("reasoning_style") != "enable_thinking":
            harness(f"expected enable_thinking style, got {st.get('reasoning_style')}")

        api = req(base, "/v1/chat/completions", {
            "model": model_id, "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": 48, "temperature": 0, "seed": 1234, "stream": False,
            "enable_thinking": False}, token=token)
        api_content = api["choices"][0]["message"].get("content") or ""
        log(f"FACT api_content={api_content!r}")
        if "<think>hi</think>" not in api_content:
            harness("model did not type the literal tags; the UI check would prove nothing")

        auth = StudioAuth(access_token=token, refresh_token=tok.get("refresh_token", ""),
                          base_url=base)
        async with open_chat(base, init_scripts=[seed_init_script(auth, [])],
                             viewport=(1280, 800)) as sp:
            page = sp.page
            composer = page.locator("form:has(textarea) textarea").first
            await composer.wait_for(state="visible", timeout=60_000)
            try:
                await pick_model(sp, model_id)
            except Exception:  # noqa: BLE001 -- resident model already selected
                pass
            for _ in range(3):
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(400)
            pill = page.locator('[data-pill-label="Thinking"]').first
            await pill.wait_for(state="visible", timeout=60_000)
            if (await pill.get_attribute("data-active")) == "true":
                await pill.click()
                await page.wait_for_timeout(1_000)
            if (await pill.get_attribute("data-active")) == "true":
                harness("Thinking pill still on")
            await send_prompt(sp, PROMPT)
            await wait_for_stream(sp, timeout_ms=180_000)
            await page.wait_for_timeout(3_000)
            bubbles = await page.locator(".aui-assistant-message-content").all_inner_texts()
            if not bubbles:
                harness("no assistant bubble")
            live = bubbles[-1].strip()
            await page.screenshot(path=str(art / "live.png"))

            threads = req(base, "/api/chat/threads", token=token)["threads"]
            thread = max(threads, key=lambda t: t.get("updatedAt") or t.get("createdAt") or 0)
            msgs = req(base, f"/api/chat/threads/{thread['id']}/messages", token=token)["messages"]
            last = [m for m in msgs if m["role"] == "assistant"][-1]
            parts = [p.get("type") for p in last.get("content") or []]

            await page.goto(f"{base}/chat?thread={thread['id']}", wait_until="domcontentloaded")
            await page.locator(".aui-assistant-message-content").first.wait_for(
                state="visible", timeout=60_000)
            await page.wait_for_timeout(2_000)
            reopened = (await page.locator(
                ".aui-assistant-message-content").all_inner_texts())[-1].strip()
            await page.screenshot(path=str(art / "reopened.png"))

        folded = lambda t: "Worked for" in t or "Thought for" in t  # noqa: E731
        facts = {"ui_reply": live, "ui_reply_has_reasoning_block": folded(live),
                 "stored_assistant_part_types": parts,
                 "ui_reply_after_reopen": reopened,
                 "ui_reopen_has_reasoning_block": folded(reopened)}
        (art / "facts.json").write_text(json.dumps(facts, indent=2))
        for k, v in facts.items():
            log(f"FACT {k}={v!r}")
        ok = (not folded(live) and not folded(reopened) and parts == ["text"]
              and "hi" in live)
        log("PASS literal think text stayed one reply (live, stored, reopened)" if ok else
            "FAIL literal think text was folded into a reasoning block with Thinking off")
        return 0 if ok else 1
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
