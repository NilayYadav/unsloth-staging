#!/usr/bin/env python3
# PR 15843 probe: load GGUFs with context on Auto on a real Mac runner, record the context llama-server got.
from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
from studio_test_kit.auth import StudioAuth, seed_init_script
from studio_test_kit.lifecycle import health_body_ok
from studio_test_kit.ui import open_chat

ARM = os.environ.get("PROBE_ARM", "unknown")
CAP = 65536
MODELS = [
    ("unsloth/Llama-3.2-1B-Instruct-GGUF", "Q4_K_M", 0),
    ("unsloth/LFM2-350M-GGUF", "Q4_K_M", 0),
    ("unsloth/LFM2-350M-GGUF", "Q4_K_M", 100000),
]
NEW_PASSWORD = "StudioLive-" + uuid.uuid4().hex[:12] + "-Pw1!"
_SAVED_RE = re.compile(r"password saved to:\s*(\S+)")


def log(msg: str) -> None:
    print(msg, flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def find_unsloth_bin(home: Path) -> Path:
    cands = [home / "bin" / "unsloth", home / "unsloth_studio" / "bin" / "unsloth"]
    cands.extend(home.glob(".venv*/bin/unsloth"))
    for c in cands:
        if c.is_file():
            return c
    raise SystemExit(f"FAIL setup: no unsloth CLI under {home}")


def read_password(home: Path, log_path: Path, timeout_s: int = 120) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        files = [home / "auth" / ".bootstrap_password"]
        try:
            files.extend(Path(m) for m in _SAVED_RE.findall(log_path.read_text(errors="ignore")))
        except OSError:
            pass
        for f in files:
            try:
                t = f.read_text().strip()
            except OSError:
                continue
            if t:
                return t
        time.sleep(1)
    raise SystemExit("FAIL setup: no bootstrap password")


def llama_server_procs() -> list[dict]:
    out = subprocess.run(["ps", "-axww", "-o", "pid=,rss=,command="], capture_output=True, text=True).stdout
    procs = []
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or "llama-server" not in parts[2].split(" ")[0]:
            continue
        argv = parts[2].split()
        ctx = None
        for i, a in enumerate(argv):
            if a in ("-c", "--ctx-size") and i + 1 < len(argv):
                ctx = argv[i + 1]
        procs.append({"pid": int(parts[0]), "rss_mb": int(parts[1]) // 1024, "ctx_arg": ctx,
                      "argv": [a for a in argv if not a.startswith("sk-")]})
    return procs


async def main() -> None:
    home = Path(os.environ["UNSLOTH_STUDIO_HOME"]).resolve()
    art = Path(os.environ.get("STUDIO_ARTIFACT_DIR", "art")).resolve()
    art.mkdir(parents=True, exist_ok=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    impl = subprocess.run(["grep", "-c", "_APPLE_AUTO_CTX", "studio/backend/core/inference/llama_cpp.py"],
                          capture_output=True, text=True).stdout.strip()
    hw = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string", "hw.memsize"], capture_output=True, text=True).stdout.split("\n")
    global ARM
    if ARM == "unknown":
        ARM = "head" if impl not in ("", "0") else "base"
    log(f"ARM {ARM} sha {sha} _APPLE_AUTO_CTX refs in llama_cpp.py: {impl} host {platform.platform()} {hw[:2]}")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_path = art / "studio.log"
    env = os.environ.copy()
    env["UNSLOTH_STUDIO_HOME"] = str(home)
    fh = log_path.open("w")
    proc = subprocess.Popen([str(find_unsloth_bin(home)), "studio", "-H", "127.0.0.1", "-p", str(port)],
                            stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                            start_new_session=True)
    results = []
    try:
        deadline = time.time() + 600
        while not health_body_ok(base + "/api/health"):
            if time.time() > deadline:
                raise SystemExit("FAIL setup: Studio not healthy")
            time.sleep(2)
        pw = read_password(home, log_path)
        async with httpx.AsyncClient(timeout=httpx.Timeout(2400)) as c:
            r = await c.post(f"{base}/api/auth/login", json={"username": "unsloth", "password": pw})
            r.raise_for_status()
            body = r.json()
            if body.get("must_change_password"):
                r = await c.post(f"{base}/api/auth/change-password",
                                 headers={"Authorization": "Bearer " + body["access_token"]},
                                 json={"current_password": pw, "new_password": NEW_PASSWORD})
                r.raise_for_status()
                body = r.json()
            tok = body["access_token"]
            hdr = {"Authorization": "Bearer " + tok}
            init = seed_init_script(StudioAuth(access_token=tok, refresh_token=body.get("refresh_token", ""),
                                               base_url=base), [])
            h = (await c.get(f"{base}/api/health", headers=hdr)).json()
            log("HEALTH " + json.dumps({k: h.get(k) for k in ("chat_only", "device", "platform", "gpu", "version") if k in h}))
            for idx, (repo, variant, req_ctx) in enumerate(MODELS):
                tag = f"{repo.split('/')[-1]}-{'auto' if not req_ctx else req_ctx}"
                t0 = time.time()
                r = await c.post(f"{base}/api/inference/load", headers=hdr,
                                 json={"model_path": repo, "gguf_variant": variant, "max_seq_length": req_ctx})
                text = r.text.strip()
                try:
                    lb = json.loads(text)
                except ValueError:
                    lb = {"raw": text[:500]}
                st = (await c.get(f"{base}/api/inference/status", headers=hdr)).json()
                procs = llama_server_procs()
                res = {
                    "arm": ARM, "model": repo, "variant": variant,
                    "requested_ctx": req_ctx or "Auto", "load_http": r.status_code,
                    "load_s": round(time.time() - t0, 1),
                    "status_context_length": st.get("context_length"),
                    "status_max_context_length": st.get("max_context_length"),
                    "status_native_context_length": st.get("native_context_length"),
                    "llama_server_ctx_arg": procs[0]["ctx_arg"] if procs else None,
                    "llama_server_rss_mb": procs[0]["rss_mb"] if procs else None,
                    "load_error": None if r.status_code == 200 else str(lb)[:400],
                }
                log("RESULT " + json.dumps(res))
                if procs:
                    log("ARGV " + " ".join(procs[0]["argv"]))
                results.append(res)
                if idx == 0 and r.status_code == 200:
                    try:
                        async with open_chat(base, init_scripts=[init]) as sp:
                            await sp.page.wait_for_timeout(8000)
                            await sp.screenshot(art / f"chat-{ARM}-{tag}.png")
                            hdr_text = await sp.page.locator("header").first.inner_text(timeout=10000)
                            log("HEADER " + " | ".join(hdr_text.split()))
                    except Exception as exc:
                        log(f"WARN screenshot: {exc}")
                await c.post(f"{base}/api/inference/unload", headers=hdr, json={"model_path": repo})
                await asyncio.sleep(3)
    finally:
        (art / f"results-{ARM}.json").write_text(json.dumps(results, indent=2))
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            pass
        fh.close()
        tail = log_path.read_text(errors="ignore").splitlines()
        for line in tail:
            if re.search(r"(?i)auto|context|ctx|metal|unified|paravirtual|fit", line) and "sk-unsloth" not in line \
                    and "password" not in line.lower():
                print("LOG " + line[:300])
    bad = []
    for res in results:
        native = res["status_native_context_length"] or 0
        arg = int(res["llama_server_ctx_arg"]) if res["llama_server_ctx_arg"] else None
        if res["requested_ctx"] != "Auto":
            print(f"INFO explicit {res['model']} {res['requested_ctx']}: load {res['load_http']} -c {arg}", flush=True)
        elif res["load_http"] != 200 or arg is None:
            bad.append(f"{res['model']} {res['requested_ctx']}: load {res['load_http']} ctx_arg {arg}")
        elif res["requested_ctx"] == "Auto" and native > CAP and arg > CAP:
            bad.append(f"{res['model']} Auto: llama-server -c {arg} > {CAP} (native {native})")
    for b in bad:
        print(f"FAIL {b}", flush=True)
    if bad:
        raise SystemExit(1)
    print("PASS every Auto load stayed at or under 65536", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
