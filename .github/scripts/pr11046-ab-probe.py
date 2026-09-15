#!/usr/bin/env python3
"""PR 11046 A/B probe: a local GGUF chat with a Notion-sized stdio MCP catalog connected.

The workflow and this probe are identical on both branches. A branch whose name contains
"-before" carries upstream main only and must refuse the prompt for context; the other carries
the PR and must answer it.
"""

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

from studio_test_kit.auth import StudioAuth, seed_init_script
from studio_test_kit.ui import open_chat, send_prompt

HERE = Path(__file__).resolve().parent
CATALOG = HERE / "pr11046_catalog.json"
MODEL = "unsloth/Qwen3-0.6B-GGUF"
VARIANT = "Q4_K_M"
CTX = 16384
PROMPT = "Reply with one short friendly sentence."
REFUSAL = re.compile(
    r"message too long|exceeds the [\w-]* ?context|context size|too long for the", re.I
)

SERVER = r'''
import json, sys
tools = json.load(open(sys.argv[1]))
def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except ValueError:
        continue
    method, rid = req.get("method"), req.get("id")
    if rid is None:
        continue
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": req.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "notion-catalog", "version": "1.0.0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})
    elif method == "tools/call":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": "ok"}], "isError": False}})
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown"}})
'''


def log(msg: str) -> None:
    print(msg, flush = True)


def fail(msg: str) -> None:
    print(f"FAIL {msg}", file = sys.stderr, flush = True)
    raise SystemExit(1)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def find_unsloth_bin(home: Path) -> Path:
    for candidate in (home / "bin" / "unsloth", home / "unsloth_studio" / "bin" / "unsloth"):
        if candidate.is_file():
            return candidate
    fail(f"could not find unsloth CLI under {home}")


def read_password(home: Path) -> str:
    deadline = time.time() + 300
    while time.time() < deadline:
        for rel in ("auth/.bootstrap_password", ".bootstrap_password"):
            try:
                text = (home / rel).read_text(encoding = "utf-8").strip()
            except OSError:
                continue
            if text:
                return text
        time.sleep(2)
    fail("bootstrap password not found")


def wait_for_health(base_url: str, timeout_s: int = 600) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/health", timeout = 3) as resp:
                if resp.status < 500:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    fail("Studio did not become healthy")


async def authenticate(base_url: str, password: str) -> StudioAuth:
    async with httpx.AsyncClient(timeout = 30) as client:
        resp = await client.post(
            f"{base_url}/api/auth/login", json = {"username": "unsloth", "password": password}
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("must_change_password"):
            resp = await client.post(
                f"{base_url}/api/auth/change-password",
                headers = {"Authorization": f"Bearer {body['access_token']}"},
                json = {"current_password": password, "new_password": "UnslothStudioCI2026!"},
            )
            resp.raise_for_status()
            body = resp.json()
    return StudioAuth(body["access_token"], body.get("refresh_token", ""), base_url)


def body_of(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return resp.text[:2000]


async def enable_mcp_pill(page) -> bool:
    pill = page.locator('form:has(textarea) button[aria-label="MCP servers"]').first
    if await pill.count() == 0:
        await page.get_by_role("button", name = "Tools and attachments").first.click(timeout = 30_000)
        await page.wait_for_timeout(1_500)
        menu = page.locator("[data-radix-popper-content-wrapper]").last
        await menu.get_by_text("MCP", exact = True).first.click(timeout = 15_000)
        await page.wait_for_timeout(1_500)
        await page.keyboard.press("Escape")
    await pill.wait_for(state = "visible", timeout = 15_000)
    return True


async def wait_outcome(page, baseline_refusals: int) -> tuple[str, str]:
    start = time.time()
    saw_stop = False
    quiet = 0
    tail = ""
    while time.time() - start < 1200:
        text = " ".join((await page.locator("body").first.inner_text()).split())
        tail = text.split(PROMPT)[-1] if PROMPT in text else text
        if len(REFUSAL.findall(text)) > baseline_refusals:
            return "refused", tail[:800]
        stop = page.locator('button[aria-label="Stop generating"]').first
        if await stop.count() and await stop.is_visible():
            saw_stop, quiet = True, 0
        elif saw_stop:
            quiet += 1
            if quiet >= 3:
                return "answered", tail[:800]
        await page.wait_for_timeout(3_000)
    return "timeout", tail[:800]


async def run(base_url: str, home: Path, artifacts: Path, side: str, facts: dict) -> None:
    auth = await authenticate(base_url, read_password(home))
    headers = {"Authorization": f"Bearer {auth.access_token}"}
    server = artifacts / "pr11046_catalog_server.py"
    server.write_text(SERVER, encoding = "utf-8")
    async with httpx.AsyncClient(base_url = base_url, headers = headers, timeout = 1800) as client:
        resp = await client.post("/api/mcp/servers/", json = {
            "display_name": "Notion",
            "url": f"{sys.executable} {server} {CATALOG}",
            "is_enabled": True,
        })
        facts["mcp_register_status"] = resp.status_code
        if resp.status_code >= 300:
            fail(f"MCP registration failed: {body_of(resp)}")
        resp = await client.post("/api/inference/load", json = {
            "model_path": MODEL, "gguf_variant": VARIANT, "max_seq_length": CTX,
        })
        facts["load_status"] = resp.status_code
        loaded = body_of(resp)
        if resp.status_code >= 300:
            fail(f"model load failed: {loaded}")
        facts["context_length"] = loaded.get("context_length") if isinstance(loaded, dict) else None
        log(f"loaded {MODEL} {VARIANT} ctx={facts['context_length']}")

    async with open_chat(base_url, init_scripts = [seed_init_script(auth, [])], viewport = (1280, 900)) as sp:
        page = sp.page
        try:
            await page.locator("form:has(textarea) textarea").first.wait_for(state = "visible", timeout = 120_000)
            await page.wait_for_timeout(3_000)
            facts["mcp_pill_enabled"] = await enable_mcp_pill(page)
            body = " ".join((await page.locator("body").first.inner_text()).split())
            await send_prompt(sp, PROMPT)
            outcome, tail = await wait_outcome(page, len(REFUSAL.findall(body)))
            await page.wait_for_timeout(2_000)
            facts["outcome"], facts["transcript_after_prompt"] = outcome, tail
            bar = page.locator('button[aria-label^="Context usage:"], button[aria-label^="Context window:"]').first
            facts["context_bar_label"] = await bar.get_attribute("aria-label") if await bar.count() else None
        except Exception as exc:
            facts["ui_error"] = f"{type(exc).__name__}: {exc}"[:600]
            raise
        finally:
            await page.screenshot(path = str(artifacts / f"pr11046-{side}.png"))

    async with httpx.AsyncClient(base_url = base_url, headers = headers, timeout = 600) as client:
        resp = await client.post("/api/inference/chat/count_tokens", json = {
            "model": MODEL,
            "messages": [{"role": "user", "content": PROMPT}],
            "enable_tools": True,
            "mcp_enabled": True,
        })
        facts["count_tokens_status"] = resp.status_code
        facts["count_tokens"] = body_of(resp)


def main() -> None:
    home = Path(os.environ["UNSLOTH_STUDIO_HOME"]).resolve()
    artifacts = Path(os.environ.get("STUDIO_ARTIFACT_DIR", "studio-live-artifacts")).resolve()
    artifacts.mkdir(parents = True, exist_ok = True)
    ref = os.environ.get("GITHUB_REF_NAME", "")
    side = "before" if "-before" in ref else "after"
    expect = "answered"
    tools_py = Path("studio/backend/core/inference/tools.py").read_text(encoding = "utf-8")
    catalog = json.loads(CATALOG.read_text(encoding = "utf-8"))
    facts: dict = {
        "side": side,
        "expect": expect,
        "ref": ref,
        "sha": subprocess.run(["git", "rev-parse", "HEAD"], capture_output = True, text = True).stdout.strip(),
        "pr_code_present": "mcp_tool_schema" in tools_py,
        "catalog_tools": len(catalog),
        "catalog_chars": sum(len(json.dumps(t)) for t in catalog),
        "model": f"{MODEL}:{VARIANT}",
        "requested_ctx": CTX,
    }
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = dict(os.environ, UNSLOTH_STUDIO_HOME = str(home))
    log_file = (artifacts / "studio.log").open("w", encoding = "utf-8")
    proc = subprocess.Popen(
        [str(find_unsloth_bin(home)), "studio", "-H", "127.0.0.1", "-p", str(port)],
        stdout = log_file, stderr = subprocess.STDOUT, env = env, start_new_session = True,
    )
    try:
        wait_for_health(base_url)
        asyncio.run(run(base_url, home, artifacts, side, facts))
    finally:
        (artifacts / f"pr11046-{side}-facts.json").write_text(json.dumps(facts, indent = 2), encoding = "utf-8")
        log(json.dumps(facts, indent = 2))
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout = 20)
        except Exception:
            proc.kill()
        log_file.close()
    if facts.get("outcome") != expect:
        fail(f"{side}: expected {expect}, got {facts.get('outcome')}")
    log(f"PASS {side}: {expect}")


if __name__ == "__main__":
    main()
