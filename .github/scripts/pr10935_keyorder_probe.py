#!/usr/bin/env python3
"""PR 10935 / #10839 probe: does a nested optional key written out of declared order survive?

Two real paths against one Studio with a real GGUF loaded:
  api: /v1/chat/completions passthrough (routes/inference.py _build_passthrough_payload)
  ui:  the chat page with a stdio MCP tool on (core/inference/llama_cpp.py tool loop)
Notion declares start_cursor BEFORE page_size; the model is asked to write page_size first.
Exit 0 = cursor kept everywhere, 1 = cursor dropped (the #10839 symptom), 2 = inconclusive.
"""

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ["BASE_URL"]
PASSWORD = os.environ["STUDIO_PASSWORD"]
OUT = Path(os.environ.get("OUT_DIR", "pr10935-evidence"))
LABEL = os.environ.get("SIDE_LABEL", "side")
STUB = Path(os.environ["STUB_PATH"]).resolve()
STUB_LOG = Path(os.environ.get("STUB_LOG", "/tmp/pr10935_stub_calls.jsonl"))
TOOL_NAME = "notion-query-data-sources"
VIEW_URL = "https://www.notion.so/acme/Tasks-1f2e?v=9a8b7c"
API_CURSORS = ["s:mcp_non_archived_1", "s:page_2_7f3e", "s:cursor_b91c", "s:next_4d20"]
UI_CURSOR = "s:ui_page_2_c0ffee"

sys.path.insert(0, str(STUB.parent))
from pr10935_notion_stub_mcp import TOOL  # noqa: E402

OUT.mkdir(parents = True, exist_ok = True)


def request(path, body = None, token = None, method = None, timeout = 600):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", data = data, headers = headers,
                                 method = method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout = timeout) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:2000]


def arguments_for(cursor):
    return {"data": {"mode": "view", "view_url": VIEW_URL, "page_size": 25, "start_cursor": cursor}}


def instruction(cursor):
    return (f"Call the {TOOL_NAME} tool once with exactly these arguments, keeping every key "
            f"in this order: {json.dumps(arguments_for(cursor))}")


def api_trial(token, model_id, cursor):
    status, body = request("/v1/chat/completions", {
        "model": model_id,
        "messages": [
            {"role": "system", "content": "You call tools with exactly the arguments the user gives."},
            {"role": "user", "content": instruction(cursor)},
        ],
        "tools": [{"type": "function", "function": {
            "name": TOOL_NAME, "description": TOOL["description"],
            "parameters": TOOL["inputSchema"]}}],
        "tool_choice": "required",
        "temperature": 0,
        "seed": 3407,
        "max_tokens": 300,
        "stream": False,
        "enable_thinking": False,
    }, token = token)
    trial = {"cursor": cursor, "http_status": status}
    if status != 200 or not isinstance(body, dict):
        trial["error"] = str(body)[:500]
        return trial
    calls = (body["choices"][0]["message"].get("tool_calls") or [])
    trial["tool_calls"] = len(calls)
    if calls:
        raw = calls[0]["function"].get("arguments") or ""
        trial["raw_arguments"] = raw
        try:
            data = json.loads(raw).get("data") or {}
        except (ValueError, AttributeError):
            data = {}
        trial["key_order"] = list(data)
        trial["start_cursor"] = data.get("start_cursor")
        trial["cursor_kept"] = data.get("start_cursor") == cursor
    return trial


async def ui_trial(tokens):
    from playwright.async_api import async_playwright

    for row in request("/api/mcp/servers/", token = tokens["access_token"])[1] or []:
        request(f"/api/mcp/servers/{row['id']}", token = tokens["access_token"], method = "DELETE")
    STUB_LOG.unlink(missing_ok = True)
    status, created = request("/api/mcp/servers/", {
        "display_name": "Notion",
        "url": f"{sys.executable} {STUB} {STUB_LOG}",
        "is_enabled": True,
    }, token = tokens["access_token"])
    facts = {"mcp_register_status": status}
    if status not in (200, 201):
        facts["mcp_register_error"] = str(created)[:500]
        return facts, None

    seed = json.dumps({"unsloth_auth_token": tokens["access_token"],
                       "unsloth_refresh_token": tokens.get("refresh_token", "")})
    init = f"(() => {{ const s = {seed}; for (const k in s) localStorage.setItem(k, s[k]); }})();"
    shot = OUT / f"{'before' if LABEL.startswith('BEFORE') else 'after'}_ui_tool_card.png"
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(viewport = {"width": 1280, "height": 1000})
        await context.add_init_script(init)
        page = await context.new_page()
        try:
            await page.goto(f"{BASE}/chat", wait_until = "domcontentloaded")
            composer = page.locator("form:has(textarea) textarea").first
            await composer.wait_for(state = "visible", timeout = 90_000)
            await page.wait_for_timeout(3_000)

            pill = page.locator('form:has(textarea) button[aria-label="MCP servers"]').first
            if await pill.count() == 0:
                await page.get_by_role("button", name = "Tools and attachments").first.click(timeout = 30_000)
                await page.wait_for_timeout(1_500)
                menu = page.locator("[data-radix-popper-content-wrapper]").last
                await menu.get_by_text("MCP", exact = True).first.click(timeout = 15_000)
                await page.wait_for_timeout(1_500)
            await pill.wait_for(state = "visible", timeout = 15_000)

            await composer.click()
            await composer.fill(instruction(UI_CURSOR) + " Then tell me what the tool returned.")
            await composer.press("Enter")

            card = page.locator('[data-slot="tool-fallback-root"]').first
            await card.wait_for(state = "visible", timeout = 600_000)
            deadline = time.time() + 900
            while time.time() < deadline and not STUB_LOG.exists():
                await page.wait_for_timeout(1_000)
            stop = page.locator('button[aria-label="Stop generating"]').first
            while time.time() < deadline and await stop.count() and await stop.is_visible():
                await page.wait_for_timeout(1_000)
            await page.wait_for_timeout(3_000)

            trigger = card.locator('[data-slot="tool-fallback-trigger"]').first
            if await card.locator('[data-slot="tool-fallback-args"]').count() == 0:
                await trigger.click()
                await page.wait_for_timeout(1_500)
            args_el = card.locator('[data-slot="tool-fallback-args"]').first
            result_el = card.locator('[data-slot="tool-fallback-result"]').first
            facts["ui_args_text"] = (await args_el.inner_text()) if await args_el.count() else None
            facts["ui_result_text"] = (await result_el.inner_text()) if await result_el.count() else None
            await card.scroll_into_view_if_needed()
            await page.wait_for_timeout(800)
            await page.screenshot(path = str(shot))
        except Exception as exc:  # keep the frame that failed
            facts["ui_error"] = f"{type(exc).__name__}: {exc}"[:500]
            await page.screenshot(path = str(shot))
        finally:
            await context.close()
            await browser.close()

    calls = [json.loads(line) for line in STUB_LOG.read_text().splitlines()] if STUB_LOG.exists() else []
    facts["ui_tool_executions"] = len(calls)
    if calls:
        data = calls[0]["arguments"].get("data") or {}
        facts["ui_executed_arguments"] = calls[0]["arguments"]
        facts["ui_key_order"] = calls[0]["key_order"]
        facts["ui_cursor_kept"] = data.get("start_cursor") == UI_CURSOR
    return facts, shot


def main():
    status, tokens = request("/api/auth/login", {"username": "unsloth", "password": PASSWORD})
    if status != 200:
        print(f"INCONCLUSIVE: login {status}")
        return 2
    health = request("/api/inference/status", token = tokens["access_token"])[1] or {}
    model_id = health.get("active_model") or health.get("model_id") or "default"

    facts = {"side": LABEL, "model": model_id, "sha": os.environ.get("GITHUB_SHA"),
             "llama_cpp": health.get("llama_cpp_version") or health.get("backend_version")}
    facts["api_trials"] = [api_trial(tokens["access_token"], model_id, c) for c in API_CURSORS]
    made = [t for t in facts["api_trials"] if t.get("tool_calls")]
    facts["api_calls_made"] = len(made)
    facts["api_cursor_kept"] = sum(bool(t.get("cursor_kept")) for t in made)
    ui_facts, shot = asyncio.run(ui_trial(tokens))
    facts.update(ui_facts)
    facts["screenshot"] = shot.name if shot else None

    (OUT / "facts.json").write_text(json.dumps(facts, indent = 2))
    print(json.dumps(facts, indent = 2))

    total = len(API_CURSORS)
    if facts["api_calls_made"] < total or not facts.get("ui_tool_executions"):
        print(f"INCONCLUSIVE: api calls {facts['api_calls_made']}/{total}, "
              f"ui executions {facts.get('ui_tool_executions')}")
        return 2
    print(f"[{LABEL}] api start_cursor kept {facts['api_cursor_kept']}/{total}; "
          f"ui start_cursor kept {facts.get('ui_cursor_kept')}")
    if facts["api_cursor_kept"] == total and facts.get("ui_cursor_kept"):
        print("PASS: start_cursor survived on every call")
        return 0
    print("FAIL: start_cursor dropped (#10839)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
