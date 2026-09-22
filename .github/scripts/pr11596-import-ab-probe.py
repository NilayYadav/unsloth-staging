#!/usr/bin/env python3
"""PR 11596 live probe: import a chat file that reuses a tool call id across turns and
read back each tool card's result in real Studio. Never prints passwords or tokens."""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

LABEL = os.environ.get("PROBE_LABEL", "unlabelled")
CHAT_NAME = "weather-tool-chat"
CALL = {"id": "tool_call_0", "type": "function"}
MESSAGES = [
    {"role": "user", "content": "What's the weather in Paris?"},
    {"role": "assistant", "content": None, "tool_calls": [
        {**CALL, "function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\"}"}}]},
    {"role": "tool", "tool_call_id": "tool_call_0", "name": "get_weather",
     "content": "Paris: 18°C, light rain"},
    {"role": "assistant", "content": "It is 18°C with light rain in Paris."},
    {"role": "user", "content": "And in Tokyo?"},
    {"role": "assistant", "content": None, "tool_calls": [
        {**CALL, "function": {"name": "get_weather", "arguments": "{\"city\": \"Tokyo\"}"}}]},
    {"role": "tool", "tool_call_id": "tool_call_0", "name": "get_weather",
     "content": "Tokyo: 27°C, clear sky"},
    {"role": "assistant", "content": "It is 27°C and clear in Tokyo."},
]
EXPECTED = ["Paris: 18°C, light rain", "Tokyo: 27°C, clear sky"]


def log(msg: str) -> None:
    print(msg, flush=True)


def fail(msg: str) -> None:
    print(f"FAIL {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def find_bin(home: Path) -> Path:
    cands = [home / "bin" / "unsloth", home / "unsloth_studio" / "bin" / "unsloth"]
    cands += list(home.glob(".venv*/bin/unsloth"))
    for c in cands:
        if c.is_file():
            return c
    fail(f"no unsloth CLI under {home}")


def wait_health(base: str, timeout_s: int = 240) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for path in ("/healthz", "/api/health"):
            try:
                with urllib.request.urlopen(base + path, timeout=3) as r:
                    if r.status < 500:
                        return
            except (urllib.error.URLError, OSError, TimeoutError):
                pass
        time.sleep(2)
    fail("Studio never became healthy")


def bootstrap_password(home: Path, log_path: Path) -> str:
    deadline = time.time() + 120
    while time.time() < deadline:
        for rel in ("auth/.bootstrap_password", ".bootstrap_password"):
            try:
                text = (home / rel).read_text().strip()
                if text:
                    return text
            except OSError:
                pass
        m = re.search(r"(?i)(?:bootstrap|initial|generated)\s*password(?:\s+is)?\s*[:=]?\s+(\S+)",
                      log_path.read_text(errors="ignore") if log_path.exists() else "")
        if m:
            return m.group(1).strip(".,")
        time.sleep(2)
    fail("no bootstrap password")


async def tokens(base: str, password: str) -> tuple[str, str]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{base}/api/auth/login", json={"username": "unsloth", "password": password})
        r.raise_for_status()
        body = r.json()
        if body.get("must_change_password"):
            r = await c.post(f"{base}/api/auth/change-password",
                             headers={"Authorization": f"Bearer {body['access_token']}"},
                             json={"current_password": password, "new_password": "UnslothStudioCI2026!"})
            r.raise_for_status()
            body = r.json()
    log("PASS Studio API login succeeded")
    return body["access_token"], body.get("refresh_token", "")


async def expand_all(page) -> None:
    for _ in range(6):
        closed = page.locator(
            "[data-slot=tool-group-trigger][data-state=closed], "
            "[data-slot=tool-fallback-trigger][data-state=closed]")
        n = await closed.count()
        if n == 0:
            return
        await closed.first.click()
        await page.wait_for_timeout(400)


async def drive(base: str, access: str, refresh: str, out: Path) -> dict:
    chat_file = out / f"{CHAT_NAME}.jsonl"
    chat_file.write_text("\n".join(json.dumps(m, ensure_ascii=False) for m in MESSAGES) + "\n")
    seed = json.dumps({
        "unsloth_auth_token": access,
        "unsloth_refresh_token": refresh,
        "unsloth_settings_active_tab": "data",
    })
    init = f"(() => {{ const s = {seed}; for (const k in s) try {{ localStorage.setItem(k, s[k]); }} catch (e) {{}} }})();"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1366, "height": 1000})
        await ctx.add_init_script(init)
        page = await ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            await page.goto(f"{base}/chat", wait_until="domcontentloaded")
            await page.locator("form:has(textarea) textarea").first.wait_for(state="visible", timeout=60_000)
            await page.locator('[aria-label$="account menu"]').first.click()
            await page.get_by_role("menuitem", name=re.compile(r"^Settings")).first.click()
            dialog = page.get_by_role("dialog")
            await dialog.wait_for(state="visible", timeout=15_000)
            await dialog.locator("button, [role=tab]").filter(has_text=re.compile(r"^\s*Data\s*$")).first.click()
            file_input = page.locator('input[type=file][accept=".json,.jsonl,.ndjson,.csv"]')
            await file_input.wait_for(state="attached", timeout=30_000)
            await page.screenshot(path=str(out / "01-settings-data.png"))
            await file_input.set_input_files(str(chat_file))
            await page.get_by_text("Imported 1 conversation").first.wait_for(timeout=30_000)
            log("PASS chat file imported through Settings > Data > Import chats")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
            link = page.get_by_text(CHAT_NAME, exact=True).first
            await link.wait_for(state="visible", timeout=30_000)
            await link.click()
            await page.get_by_text("It is 27°C and clear in Tokyo.").first.wait_for(timeout=30_000)
            await expand_all(page)
            results = page.locator("[data-slot=tool-fallback-result] pre")
            await results.first.wait_for(state="visible", timeout=15_000)
            got = [t.strip() for t in await results.all_inner_texts()]
            args = [t.strip() for t in await page.locator("[data-slot=tool-fallback-args]").all_inner_texts()]
            await page.locator("[data-slot=tool-fallback-root]").first.scroll_into_view_if_needed()
            await page.screenshot(path=str(out / f"02-thread-{LABEL}.png"))
        except Exception:
            await page.screenshot(path=str(out / f"error-{LABEL}.png"))
            raise
        finally:
            await ctx.close()
            await browser.close()
    return {"label": LABEL, "tool_args": args, "tool_results": got, "expected": EXPECTED,
            "page_errors": errors}


async def main() -> None:
    home = Path(os.environ["UNSLOTH_STUDIO_HOME"]).resolve()
    out = Path(os.environ.get("STUDIO_ARTIFACT_DIR", "artifacts")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_path = out / "studio.log"
    env = {**os.environ, "UNSLOTH_STUDIO_HOME": str(home)}
    with log_path.open("w") as fh:
        proc = subprocess.Popen([str(find_bin(home)), "studio", "-H", "127.0.0.1", "-p", str(port)],
                                stdout=fh, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    try:
        wait_health(base)
        log(f"PASS Studio healthy on {base}")
        access, refresh = await tokens(base, bootstrap_password(home, log_path))
        facts = await drive(base, access, refresh, out)
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            pass
    (out / "facts.json").write_text(json.dumps(facts, indent=2, ensure_ascii=False))
    log("FACTS " + json.dumps(facts, ensure_ascii=False))
    if facts["tool_results"] != EXPECTED:
        fail(f"tool card results {facts['tool_results']} != expected {EXPECTED}")
    log("PASS each imported tool card shows its own result")


if __name__ == "__main__":
    asyncio.run(main())
