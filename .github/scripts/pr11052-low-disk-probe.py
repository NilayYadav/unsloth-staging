#!/usr/bin/env python3
"""PR 11052 A/B probe: low-disk GGUF variant fallback on a real Studio.

Never prints passwords, tokens, or API keys.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import httpx
from studio_test_kit.auth import login, seed_init_script
from studio_test_kit.ui import open_chat, send_prompt, wait_for_stream

REPO = "unsloth/Qwen3-4B-GGUF"
REQUESTED = "BF16"
EXPECTED_FALLBACK = "Q4_1"
GIB = 1024**3
TARGET_FREE = int(7.45 * GIB)

HOME = Path(os.environ["UNSLOTH_STUDIO_HOME"]).resolve()
ART = Path(os.environ["STUDIO_ARTIFACT_DIR"]).resolve()
HUB = Path(os.environ["HF_HUB_CACHE"]).resolve()
BALLAST = Path(os.environ["LOW_DISK_BALLAST"]).resolve()
SIDE = os.environ.get("AB_SIDE", "unknown")
facts: dict = {"side": SIDE, "sha": os.environ.get("GITHUB_SHA"), "repo": REPO, "requested": REQUESTED}


def log(msg: str) -> None:
    print(msg, flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def find_bin() -> Path:
    cands = [HOME / "bin" / "unsloth", HOME / "unsloth_studio" / "bin" / "unsloth"]
    cands += list(HOME.glob(".venv*/bin/unsloth"))
    for c in cands:
        if c.is_file():
            return c
    raise SystemExit(f"FAIL setup: unsloth CLI not found under {HOME}")


def wait_health(base: str, timeout_s: int = 600) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=3) as r:
                if r.status < 500:
                    return
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    raise SystemExit("FAIL setup: Studio never became healthy")


def bootstrap_password(log_path: Path) -> str:
    deadline = time.time() + 300
    while time.time() < deadline:
        for rel in ("auth/.bootstrap_password", ".bootstrap_password"):
            p = HOME / rel
            if p.is_file() and p.read_text().strip():
                return p.read_text().strip()
        m = re.search(
            r"(?i)(?:bootstrap|initial|generated)\s*password(?:\s+is)?\s*[:=]?\s+(\S+)",
            log_path.read_text(errors="ignore") if log_path.exists() else "",
        )
        if m:
            return m.group(1).strip().strip(".,")
        time.sleep(2)
    raise SystemExit("FAIL setup: bootstrap password not found")


def free_bytes() -> int:
    return shutil.disk_usage(HUB).free


def set_free(target: int) -> int:
    if BALLAST.exists():
        BALLAST.unlink()
    extra = free_bytes() - target
    if extra > 0:
        subprocess.run(["fallocate", "-l", str(extra), str(BALLAST)], check=True)
    return free_bytes()


def cached_ggufs() -> dict[str, int]:
    out: dict[str, int] = {}
    for snap in HUB.glob("models--unsloth--Qwen3-4B-GGUF/snapshots/*"):
        for f in snap.glob("*.gguf"):
            try:
                out[f.name] = f.resolve().stat().st_size
            except OSError:
                pass
    return out


async def api(client: httpx.AsyncClient, method: str, path: str, **kw) -> tuple[int, dict]:
    r = await client.request(method, path, **kw)
    text = r.text.strip()
    try:
        body = json.loads(text) if text else {}
    except json.JSONDecodeError:
        body = {"raw": text[:2000]}
    return r.status_code, body


async def load(client: httpx.AsyncClient, label: str) -> None:
    before = free_bytes()
    t0 = time.time()
    code, body = await api(
        client,
        "POST",
        "/api/inference/load",
        json={"model_path": REPO, "gguf_variant": REQUESTED, "max_seq_length": 2048},
        timeout=3600,
    )
    _, status = await api(client, "GET", "/api/inference/status", timeout=60)
    facts[label] = {
        "free_gib_before": round(before / GIB, 3),
        "http": code,
        "load_status": body.get("status"),
        "memory_warning": body.get("memory_warning"),
        "error": body.get("detail") or body.get("error") or body.get("raw"),
        "status_gguf_variant": status.get("gguf_variant"),
        "status_model": status.get("active_model") or status.get("model"),
        "cached_ggufs": cached_ggufs(),
        "seconds": round(time.time() - t0, 1),
    }
    log(f"FACT {label} {json.dumps(facts[label])}")


async def ui_shot(base: str, init: str, name: str, chat: bool) -> None:
    try:
        async with open_chat(base, init_scripts=[init], viewport=(1366, 800)) as sp:
            await sp.page.wait_for_timeout(6000)
            header = await sp.page.locator("body").inner_text()
            m = re.search(r"GGUF\s*·\s*([A-Za-z0-9_\-]+)", header)
            facts[f"{name}_header_variant"] = m.group(1) if m else None
            toast = sp.page.locator("[data-sonner-toast]").filter(has_text="Model loaded with a warning")
            facts[f"{name}_toast"] = (await toast.first.inner_text()) if await toast.count() else None
            await sp.screenshot(ART / f"{name}-header.png", full_page=False)
            if chat:
                await send_prompt(sp, "/no_think What is the capital of France? Reply with one word.")
                try:
                    await wait_for_stream(sp, timeout_ms=300_000)
                except Exception as exc:
                    facts[f"{name}_chat_wait"] = f"{type(exc).__name__}"
                await sp.page.wait_for_timeout(1500)
                await sp.screenshot(ART / f"{name}-chat.png", full_page=False)
    except Exception as exc:
        facts[f"{name}_ui_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    log(f"FACT {name}_header_variant={facts.get(f'{name}_header_variant')}")


async def main() -> int:
    ART.mkdir(parents=True, exist_ok=True)
    HUB.mkdir(parents=True, exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_path = ART / "studio-private.log"
    env = os.environ.copy()
    env.pop("STUDIO_HOME", None)
    with log_path.open("w") as lh:
        proc = subprocess.Popen(
            [str(find_bin()), "studio", "-H", "127.0.0.1", "-p", str(port)],
            stdout=lh, stderr=subprocess.STDOUT, env=env, start_new_session=True,
        )
    try:
        wait_health(base)
        pw = bootstrap_password(log_path)
        from studio_test_kit.auth import StudioAuth

        async with httpx.AsyncClient(base_url=base, timeout=30) as c:
            r = await c.post("/api/auth/login", json={"username": "unsloth", "password": pw})
            r.raise_for_status()
            body = r.json()
            auth = StudioAuth(body["access_token"], body.get("refresh_token", ""), base)
            if body.get("must_change_password"):
                r = await c.post(
                    "/api/auth/change-password",
                    headers={"Authorization": f"Bearer {auth.access_token}"},
                    json={"current_password": pw, "new_password": "UnslothStudioCI2026!"},
                )
                r.raise_for_status()
                auth.access_token = r.json()["access_token"]
                auth.refresh_token = r.json().get("refresh_token", "")
            facts["password_change_required"] = bool(body.get("must_change_password"))
        init = seed_init_script(auth, [])
        headers = {"Authorization": f"Bearer {auth.access_token}"}
        async with httpx.AsyncClient(base_url=base, headers=headers) as client:
            facts["phase1_free_gib_set"] = round(set_free(TARGET_FREE) / GIB, 3)
            await load(client, "phase1_low_disk")
            await ui_shot(base, init, "phase1", chat=True)

            if BALLAST.exists():
                BALLAST.unlink()
            await load(client, "phase2_space_freed")
            await ui_shot(base, init, "phase2", chat=False)
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=30)
        except Exception:
            proc.kill()
        text = log_path.read_text(errors="ignore")
        keep = [
            ln for ln in text.splitlines()
            if re.search(r"(?i)gguf|variant|disk|fallback|download|warning|error|traceback", ln)
            and not re.search(r"(?i)password|token|api key|bearer|sk-", ln)
        ]
        (ART / "studio-filtered.log").write_text("\n".join(keep[-400:]))
        log_path.unlink()

    p1, p2 = facts.get("phase1_low_disk", {}), facts.get("phase2_space_freed", {})
    p1_files, p2_files = p1.get("cached_ggufs", {}), p2.get("cached_ggufs", {})
    checks = {
        "A1 low disk serves the largest quant leaving the reserve (Q4_1)":
            list(p1_files) == [f"Qwen3-4B-{EXPECTED_FALLBACK}.gguf"],
        "A2 status reports the served quant, not the requested one":
            p1.get("status_gguf_variant") == EXPECTED_FALLBACK,
        "A3 load response names both quants":
            REQUESTED in (p1.get("memory_warning") or "") and EXPECTED_FALLBACK in (p1.get("memory_warning") or ""),
        "A4 after freeing space BF16 is downloaded and loaded":
            f"Qwen3-4B-{REQUESTED}.gguf" in p2_files and p2.get("status_gguf_variant") == REQUESTED,
    }
    facts["checks"] = checks
    (ART / "facts.json").write_text(json.dumps(facts, indent=2))
    for name, ok in checks.items():
        log(f"{'PASS' if ok else 'FAIL'} {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
