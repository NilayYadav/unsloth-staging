#!/usr/bin/env python3
"""PR 11970: a Hugging Face cache folder (HF_HOME layout) added as a model location.

Seeds <tmp>/existing_hf_home/hub/models--unsloth--SmolLM2-135M-Instruct-GGUF with a real
hf_hub_download, points Studio's own HF cache at an empty isolated dir, registers the
HF_HOME folder via POST /api/models/scan-folders, then records what /api/hub/local lists,
photographs Hub > On Device, and tries to load + chat with the listed row.
Never prints tokens or passwords.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import httpx
from studio_test_kit.auth import seed_init_script
from studio_test_kit.ui import open_chat

REPO_ID = "unsloth/SmolLM2-135M-Instruct-GGUF"
FILENAME = "SmolLM2-135M-Instruct-Q4_K_M.gguf"
SEARCH = "SmolLM2"


def log(msg: str) -> None:
    print(msg, flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def find_unsloth_bin(home: Path) -> Path:
    cands = [home / "bin" / "unsloth", home / "unsloth_studio" / "bin" / "unsloth"]
    cands += list(home.glob(".venv*/bin/unsloth"))
    for c in cands:
        if c.is_file():
            return c
    sys.exit(f"FAIL: unsloth CLI not found under {home}")


def wait_health(base: str, timeout_s: int = 300) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/healthz", timeout = 3) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    sys.exit("FAIL: Studio never became healthy")


def read_bootstrap(home: Path, log_path: Path, timeout_s: int = 60) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        f = home / "auth" / ".bootstrap_password"
        if f.is_file() and f.read_text().strip():
            return f.read_text().strip()
        m = re.search(r"(?i)(?:bootstrap|initial|generated)\s*password(?:\s+is)?\s*[:=]?\s+(\S+)",
                      log_path.read_text(errors = "ignore") if log_path.exists() else "")
        if m:
            return m.group(1).strip(".,")
        time.sleep(1)
    sys.exit("FAIL: no bootstrap password")


def seed_hf_home(root: Path) -> Path:
    from huggingface_hub import hf_hub_download

    hf_home = root / "existing_hf_home"
    path = hf_hub_download(REPO_ID, FILENAME, cache_dir = str(hf_home / "hub"))
    log(f"seeded {Path(path).relative_to(root)} ({Path(path).resolve().stat().st_size} bytes)")
    return hf_home


async def main() -> None:
    home = Path(os.environ["UNSLOTH_STUDIO_HOME"]).resolve()
    art = Path(os.environ["STUDIO_ARTIFACT_DIR"]).resolve()
    art.mkdir(parents = True, exist_ok = True)
    side = os.environ.get("PR_SIDE", "unknown")
    tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")).resolve() / "pr11970"
    tmp.mkdir(parents = True, exist_ok = True)

    hf_home = seed_hf_home(tmp)
    isolated = tmp / "studio_hf"
    env = os.environ.copy()
    env.update({
        "UNSLOTH_STUDIO_HOME": str(home),
        "HF_HOME": str(isolated),
        "HF_HUB_CACHE": str(isolated / "hub"),
        "HF_XET_CACHE": str(isolated / "xet"),
        "XDG_CACHE_HOME": str(tmp / "xdg"),
    })
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_path = art / "studio.log"
    with log_path.open("w") as lf:
        proc = subprocess.Popen([str(find_unsloth_bin(home)), "studio", "-H", "127.0.0.1", "-p", str(port)],
                                stdout = lf, stderr = subprocess.STDOUT, env = env, start_new_session = True)
    facts: dict = {"side": side, "registered_folder": "<tmp>/existing_hf_home", "repo": REPO_ID}
    try:
        wait_health(base)
        boot = read_bootstrap(home, log_path)
        new_pw = "UnslothStudioCI2026!"
        async with httpx.AsyncClient(base_url = base, timeout = 600) as c:
            tok = (await c.post("/api/auth/login", json = {"username": "unsloth", "password": boot})).json()
            r = await c.post("/api/auth/change-password", json = {"current_password": boot, "new_password": new_pw},
                             headers = {"Authorization": f"Bearer {tok['access_token']}"})
            tok = r.json() if r.status_code == 200 and "access_token" in r.json() else tok
            h = {"Authorization": f"Bearer {tok['access_token']}"}

            r = await c.post("/api/models/scan-folders", json = {"path": str(hf_home)}, headers = h)
            facts["add_scan_folder_http"] = r.status_code

            inv = (await c.get("/api/hub/local", headers = h)).json()
            models = inv.get("models", []) if isinstance(inv, dict) else []
            rows = [m for m in models if "smollm2" in str(m.get("model_id", "")).lower()]
            facts["inventory_rows_total"] = len(models)
            facts["smollm2_rows"] = len(rows)
            facts["smollm2_row"] = {k: rows[0].get(k) for k in ("model_id", "source", "format", "load_id")} if rows else None
            folders = (await c.get("/api/models/scan-folders", headers = h)).json()
            facts["scan_folder_status"] = [f.get("status") for f in (folders if isinstance(folders, list) else folders.get("folders", []))]

            init = seed_init_script(type("A", (), {"access_token": tok["access_token"],
                                                    "refresh_token": tok.get("refresh_token", "")})(), [])
            async with open_chat(base, init_scripts = [init], viewport = (1500, 1000)) as sp:
                page = sp.page
                await page.goto(f"{base}/hub", wait_until = "domcontentloaded")
                radio = page.get_by_role("radio", name = "On Device").first
                await radio.wait_for(state = "visible", timeout = 60_000)
                await radio.click()
                search = page.get_by_placeholder("Search on-device models").first
                await search.wait_for(state = "visible", timeout = 60_000)
                await search.fill(SEARCH)
                loading = page.get_by_text(re.compile(r"Loading local inventory", re.I)).first
                try:
                    await loading.wait_for(state = "visible", timeout = 10_000)
                except Exception:
                    pass
                await loading.wait_for(state = "hidden", timeout = 300_000)
                await page.wait_for_timeout(4_000)
                body = " ".join((await page.locator("body").inner_text()).split())
                facts["ui_row_visible"] = bool(re.search(r"SmolLM2-135M-Instruct", body, re.I))
                await page.screenshot(path = str(art / f"{side}_on_device.png"),
                                      clip = {"x": 340, "y": 96, "width": 1110, "height": 620})
                await page.screenshot(path = str(art / f"{side}_on_device_full.png"), full_page = True)

            if rows:
                load_id = rows[0].get("load_id") or rows[0].get("path")
                t0 = time.time()
                r = await c.post("/api/inference/load", headers = h, json = {
                    "model_path": load_id, "gguf_variant": "Q4_K_M", "max_seq_length": 1024})
                facts["load_http"] = r.status_code
                facts["load_seconds"] = round(time.time() - t0, 1)
                if r.status_code != 200:
                    facts["load_error"] = r.text[:300]
                else:
                    r = await c.post("/v1/chat/completions", headers = h, json = {
                        "model": load_id, "messages": [{"role": "user", "content": "Say hi in three words."}],
                        "max_tokens": 16, "temperature": 0, "stream": False})
                    facts["chat_http"] = r.status_code
                    try:
                        facts["chat_reply"] = r.json()["choices"][0]["message"]["content"][:120]
                    except Exception:
                        facts["chat_error"] = r.text[:300]
    finally:
        proc.terminate()
        (art / f"{side}_facts.json").write_text(json.dumps(facts, indent = 2))
        log("FACTS " + json.dumps(facts))

    if facts.get("smollm2_rows", 0) >= 1 and facts.get("ui_row_visible"):
        log("PASS: HF_HOME scan folder lists the cached model in On Device")
    else:
        sys.exit("FAIL: HF_HOME scan folder shows no SmolLM2 row (API rows=%s, UI visible=%s)"
                 % (facts.get("smollm2_rows"), facts.get("ui_row_visible")))


if __name__ == "__main__":
    asyncio.run(main())
