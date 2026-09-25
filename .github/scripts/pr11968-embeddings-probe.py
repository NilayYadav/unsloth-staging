#!/usr/bin/env python3
"""PR 11968 probe: /v1/embeddings `dimensions` on a resident embedding GGUF.

PASS: every dimensions=128 request is either honored (128-wide) or rejected 400.
FAIL: a 200 that carries the model's full width instead.
"""

from __future__ import annotations

import base64
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

REPO = "CompendiumLabs/bge-small-en-v1.5-gguf"
FILE = "bge-small-en-v1.5-q8_0.gguf"
DIMS = 128
NEW_PASSWORD = "UnslothStudioCI2026!"


def log(msg: str) -> None:
    print(msg, flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base: str, path: str, payload: dict | None, token: str | None = None, timeout: int = 900):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{base}{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        headers=headers,
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode()
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw[:500]


def width(payload) -> int | None:
    try:
        vec = payload["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError):
        return None
    return len(base64.b64decode(vec)) // 4 if isinstance(vec, str) else len(vec)


def main() -> int:
    home = Path(os.environ["UNSLOTH_STUDIO_HOME"]).resolve()
    out = Path(os.environ.get("STUDIO_ARTIFACT_DIR", "artifacts")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    side = os.environ.get("PROBE_SIDE", "unknown")

    from huggingface_hub import hf_hub_download

    gguf = hf_hub_download(REPO, FILE, cache_dir=str(out.parent / "models"))
    log(f"model file: {gguf}")

    unsloth = next(p for p in [home / "bin" / "unsloth", home / "unsloth_studio" / "bin" / "unsloth"] if p.is_file())
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    studio_log = (out / "studio.log").open("w")
    proc = subprocess.Popen([str(unsloth), "studio", "-H", "127.0.0.1", "-p", str(port)],
                            stdout=studio_log, stderr=subprocess.STDOUT, start_new_session=True,
                            env={**os.environ, "UNSLOTH_STUDIO_HOME": str(home)})
    try:
        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"{base}/api/health", timeout=3)
                break
            except Exception:
                time.sleep(2)
        else:
            log("FAIL setup: Studio never became healthy")
            return 2
        boot = (home / "auth" / ".bootstrap_password").read_text().strip()
        _, tok = call(base, "/api/auth/login", {"username": "unsloth", "password": boot})
        _, tok = call(base, "/api/auth/change-password",
                      {"current_password": boot, "new_password": NEW_PASSWORD}, tok["access_token"])
        token = tok["access_token"]
        log("PASS setup: Studio healthy and logged in")

        status, body = call(base, "/api/inference/load", {"model_path": gguf, "max_seq_length": 512}, token)
        log(f"load -> {status}")
        for _ in range(300):
            _, st = call(base, "/api/inference/status", None, token)
            if st.get("active_model") and not st.get("loading"):
                break
            time.sleep(2)
        model = st.get("active_model")
        log(f"active_model={model}")

        results = {"side": side, "active_model": model}
        s, b = call(base, "/v1/embeddings", {"model": model, "input": "alpha"}, token)
        results["control"] = {"status": s, "width": width(b)}
        if s != 200 or not width(b):
            log(f"FAIL setup: control embedding call failed: {s} {str(b)[:300]}")
            return 2
        full = width(b)
        log(f"control (no dimensions) -> {s}, width {full}")

        ok = True
        for fmt in ("float", "base64"):
            s, b = call(base, "/v1/embeddings",
                        {"model": model, "input": "alpha", "dimensions": DIMS, "encoding_format": fmt}, token)
            w = width(b)
            detail = b.get("detail") if isinstance(b, dict) else None
            results[fmt] = {"status": s, "width": w, "detail": detail}
            log(f"dimensions={DIMS} encoding_format={fmt} -> HTTP {s}, width {w}, detail {detail!r}")
            if s == 200 and w != DIMS:
                log(f"FAIL repro: asked for {DIMS} dimensions, got HTTP 200 with {w}-wide vectors")
                ok = False
            elif s == 400 and detail and "'dimensions' is not supported" in detail:
                log(f"PASS {fmt}: rejected with 400 {detail!r}")
            elif s == 200 and w == DIMS:
                log(f"PASS {fmt}: honored")
            else:
                log(f"FAIL unexpected response {s} {str(b)[:300]}")
                ok = False
        _, mon = call(base, "/api/inference/monitor", None, token)
        results["monitor"] = [e.get("status") for e in mon.get("entries", []) if e.get("endpoint") == "/v1/embeddings"]
        (out / "result.json").write_text(json.dumps(results, indent=2))
        log(json.dumps(results))
        return 0 if ok else 1
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
