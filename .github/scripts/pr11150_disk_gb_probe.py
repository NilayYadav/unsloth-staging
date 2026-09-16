#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

REPO_ID = "unsloth/Qwen3-1.7B-GGUF"
QUANT = "Q8_0"
FILENAME = "Qwen3-1.7B-Q8_0.gguf"
TINY = Path(os.environ["TINY_CACHE_ROOT"])
ART = Path(os.environ.get("ART_DIR", "pr11150-artifacts")).resolve()
LOGS = Path(os.environ.get("PRIVATE_LOG_DIR", "/tmp/pr11150-private")).resolve()
REF = os.environ.get("GITHUB_REF_NAME", "")
SIDE = "before" if "-before-" in REF else "after" if "-after-" in REF else "unknown"
VIEWPORT = {"width": 1366, "height": 768}

facts: dict = {"side": SIDE, "ref": REF, "sha": os.environ.get("GITHUB_SHA"), "viewport": VIEWPORT}
checks: list[tuple[str, bool, str]] = []
secrets_to_redact: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    checks.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)
    return ok


def write_facts() -> None:
    facts["checks"] = [{"name": n, "ok": o, "detail": d} for n, o, d in checks]
    (ART / "facts.json").write_text(json.dumps(facts, indent=2), encoding="utf-8")


def fmt_binary_gb(b: int) -> str:
    return f"{b / 1024 ** 3:.1f} GB"


def fmt_decimal_gb(b: int) -> str:
    return f"{b / 1e9:.1f} GB"


def js_to_fixed(v: float, d: int) -> str:
    return f"{math.floor(v * 10 ** d + 0.5) / 10 ** d:.{d}f}"


def hub_format_bytes(b: int) -> str:
    if b == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = min(max(math.floor(math.log(b) / math.log(1000)), 0), len(units) - 1)
    value = b / 1000 ** i
    decimals = 1 if i > 0 and float(js_to_fixed(value, 1)) < 10 else 0
    while i < len(units) - 1 and float(js_to_fixed(value, decimals)) >= 1000:
        i += 1
        value = b / 1000 ** i
        decimals = 1 if float(js_to_fixed(value, 1)) < 10 else 0
    return f"{js_to_fixed(value, decimals)} {units[i]}"


def old_free_space(b: int) -> str:
    gb = b / 1024 ** 3
    return f"{round(gb)} GB free" if gb >= 10 else f"{gb:.1f} GB free"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def find_unsloth() -> str:
    for c in (shutil.which("unsloth"), str(Path.home() / ".local/bin/unsloth")):
        if c and Path(c).exists():
            return c
    raise SystemExit("FAIL unsloth CLI not found")


def redact_copy(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    text = src.read_text(encoding="utf-8", errors="replace")
    for s in secrets_to_redact:
        if s:
            text = text.replace(s, "<redacted>")
    text = re.sub(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+", "<jwt>", text)
    text = re.sub(r"sk-unsloth-[a-f0-9]+", "<api-key>", text)
    dst.write_text(text, encoding="utf-8")


def main() -> int:
    ART.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    check("side resolved from branch", SIDE in ("before", "after"), f"ref={REF} side={SIDE}")

    usage = shutil.disk_usage(TINY)
    facts["tiny_fs"] = {"path": str(TINY), "total": usage.total, "free_at_start": usage.free}

    env = os.environ.copy()
    for k in ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_XET_CACHE", "HF_TOKEN"):
        env.pop(k, None)
    env["XDG_CACHE_HOME"] = str(TINY)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_path = LOGS / "studio.log"
    home = Path(env.get("UNSLOTH_STUDIO_HOME", str(Path.home() / ".unsloth/studio")))
    shutil.rmtree(home / "auth", ignore_errors=True)
    with log_path.open("w") as lf:
        proc = subprocess.Popen(
            [find_unsloth(), "studio", "-H", "127.0.0.1", "-p", str(port)],
            stdout=lf, stderr=subprocess.STDOUT, env=env, start_new_session=True,
        )
    try:
        return run(base, home, log_path)
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=20)
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass
        redact_copy(log_path, ART / "studio.redacted.log")
        write_facts()


def run(base: str, home: Path, log_path: Path) -> int:
    os.environ["STUDIO_BASE"] = base
    deadline = time.time() + 300
    healthy = False
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base}/api/health", timeout=5)
            if r.status_code == 200 and r.json().get("status") == "healthy":
                healthy = True
                break
        except Exception:
            pass
        time.sleep(2)
    if not check("studio healthy", healthy, base):
        return 1

    pw_file = None
    for _ in range(60):
        cands = list(home.glob("**/.bootstrap_password"))
        if cands:
            pw_file = cands[0]
            break
        time.sleep(1)
    if not check("bootstrap password file found", pw_file is not None, ""):
        return 1
    old_pw = pw_file.read_text().strip()
    secrets_to_redact.append(old_pw)
    c = httpx.Client(base_url=base, timeout=60)
    r = c.post("/api/auth/login", json={"username": "unsloth", "password": old_pw})
    if not check("login", r.status_code == 200, f"status={r.status_code}"):
        return 1
    tok = r.json()
    if tok.get("must_change_password"):
        new_pw = "CIpr11150-" + secrets.token_urlsafe(16)
        secrets_to_redact.append(new_pw)
        r = c.post(
            "/api/auth/change-password",
            headers={"Authorization": f"Bearer {tok['access_token']}"},
            json={"current_password": old_pw, "new_password": new_pw},
        )
        if not check("change password", r.status_code == 200, f"status={r.status_code}"):
            return 1
        tok = r.json()
    access, refresh = tok["access_token"], tok.get("refresh_token", "")
    secrets_to_redact.extend([access, refresh])
    H = {"Authorization": f"Bearer {access}"}

    r = c.get("/api/settings/hugging-face-cache", headers=H)
    cache = r.json() if r.status_code == 200 else {"status": r.status_code, "body": r.text[:300]}
    facts["hf_cache_api"] = cache
    check("HF cache API on tiny fs and not env-managed",
          r.status_code == 200 and str(cache.get("cache_home", "")).startswith(str(TINY)) and cache.get("source") != "environment",
          json.dumps({k: cache.get(k) for k in ("cache_home", "source", "free_bytes")}))

    r = c.get("/api/hub/gguf-variants", params={"repo_id": REPO_ID}, headers=H)
    variant = None
    if r.status_code == 200:
        for v in r.json().get("variants", []):
            if v.get("quant") == QUANT:
                variant = v
    facts["variant_api"] = variant
    check("gguf-variants lists Q8_0", variant is not None, json.dumps(variant)[:300] if variant else r.text[:300])

    init = (
        "try{localStorage.setItem('unsloth_auth_token',%s);"
        "localStorage.setItem('unsloth_auth_refresh_token',%s);"
        "localStorage.removeItem('unsloth_auth_must_change_password');}catch(e){}"
    ) % (json.dumps(access), json.dumps(refresh))

    ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=1, color_scheme="light")
        ctx.add_init_script(init)
        page = ctx.new_page()
        console_errors: list[str] = []
        page.on("console", lambda m: console_errors.append(m.text[:300]) if m.type == "error" else None)
        disk_bodies: list[dict] = []

        def on_response(resp):
            if "/api/hub/" not in resp.url:
                return
            try:
                body = resp.text()
            except Exception:
                return
            if "Not enough disk space" in body:
                disk_bodies.append({"url": resp.url.split("?")[0], "body": body[:1200]})

        page.on("response", on_response)
        try:
            ok &= scene_dialog(page)
        except Exception as exc:
            check("scene dialog", False, f"{type(exc).__name__}: {exc}"[:500])
            page.screenshot(path=str(ART / "dialog-failure.png"))
            ok = False
        try:
            ok &= scene_download(page, disk_bodies)
        except Exception as exc:
            check("scene download", False, f"{type(exc).__name__}: {exc}"[:500])
            page.screenshot(path=str(ART / "download-failure.png"))
            ok = False
        facts["console_errors"] = console_errors[:30]
        ctx.close()
        browser.close()
    return 0 if ok and all(o for _, o, _ in checks) else 1


def scene_dialog(page) -> bool:
    page.goto(os.environ["STUDIO_BASE"] + "/hub?tab=downloaded", wait_until="domcontentloaded")
    btn = page.get_by_role("button", name="Add folder")
    btn.wait_for(state="visible", timeout=90_000)
    btn.click()
    dialog = page.get_by_role("dialog").filter(has_text="On-device locations")
    dialog.wait_for(state="visible", timeout=30_000)
    loc_input = dialog.get_by_label("Model download location")
    free_line = dialog.locator("p").filter(has_text=re.compile(r"\bfree$"))
    free_line.first.wait_for(state="visible", timeout=30_000)
    time.sleep(1)
    text = free_line.first.inner_text().strip()
    cache_home = loc_input.input_value()
    free_bytes = shutil.disk_usage(cache_home).free
    m = re.search(r"([\d.]+ [KMGT]?B free)$", text)
    shown = m.group(1) if m else None
    expected_before = old_free_space(free_bytes)
    expected_after = f"{hub_format_bytes(free_bytes)} free"
    facts["dialog"] = {
        "line_text": text,
        "free_text": shown,
        "cache_home_input": cache_home,
        "disk_usage_free_bytes": free_bytes,
        "free_gib": free_bytes / 1024 ** 3,
        "free_gb_decimal": free_bytes / 1e9,
        "expected_binary_text": expected_before,
        "expected_decimal_text": expected_after,
    }
    dialog.screenshot(path=str(ART / "dialog.png"))
    page.screenshot(path=str(ART / "dialog-page.png"))
    ok = check("dialog shows free-space text", shown is not None, text)
    ok &= check("dialog location is tiny fs", cache_home.startswith(str(TINY)), cache_home)
    ok &= check("binary and decimal renderings differ", expected_before != expected_after, f"{expected_before} vs {expected_after}")
    want = expected_before if SIDE == "before" else expected_after
    ok &= check(f"dialog free text matches {SIDE} formula", shown == want,
                f"shown={shown!r} expected={want!r} disk_usage.free={free_bytes}")
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden", timeout=10_000)
    return ok


def scene_download(page, disk_bodies) -> bool:
    url = os.environ["STUDIO_BASE"] + "/hub?" + urllib.parse.urlencode({"model": REPO_ID, "file": FILENAME})
    page.goto(url, wait_until="domcontentloaded")
    trigger = (
        page.locator("button.hub-menu-trigger")
        .filter(has_text=re.compile(rf"\b{QUANT}\b"))
        .filter(has_text="GGUF")
        .first
    )
    trigger.wait_for(state="visible", timeout=120_000)
    time.sleep(2)
    trigger_text = " ".join(trigger.inner_text().split())
    size_m = re.search(r"([\d.]+ [KMGT]?B)\s*$", trigger_text)
    card_size = size_m.group(1) if size_m else None
    card = trigger.locator("xpath=ancestor::div[.//button[contains(@class,'hub-action-btn')]][1]")
    facts["card"] = {"trigger_text": trigger_text, "size_text": card_size}
    ok = check("card trigger selects Q8_0", re.search(rf"\b{QUANT}\b", trigger_text) is not None, trigger_text)
    ok &= check("card shows decimal size 1.8 GB", card_size == "1.8 GB", f"{card_size!r}")
    try:
        card.first.screenshot(path=str(ART / "card.png"))
    except Exception:
        pass

    dl = card.first.locator("button.hub-action-btn").filter(has_text="Download").first
    dl.wait_for(state="visible", timeout=20_000)
    free_before_click = shutil.disk_usage(TINY).free
    dl.click()
    panel = page.locator(".hub-download-panel")
    err = panel.locator(".text-destructive").filter(has_text="Not enough disk space").first
    err.wait_for(state="visible", timeout=240_000)
    page.screenshot(path=str(ART / "refusal-page.png"))
    try:
        panel.first.screenshot(path=str(ART / "refusal-panel.png"))
    except Exception:
        pass
    refusal = " ".join(err.inner_text().split())
    panel_text = " ".join(panel.first.inner_text().split())
    free_after = shutil.disk_usage(TINY).free
    m = re.search(r"need about ([\d.]+ GB) free in (\S+), but only ([\d.]+ GB) is available", refusal)
    need_txt, root_txt, free_txt = (m.group(1), m.group(2), m.group(3)) if m else (None, None, None)
    expected_bytes = int((facts.get("variant_api") or {}).get("download_size_bytes") or 1_834_426_944)
    facts["refusal"] = {
        "ui_text": refusal,
        "panel_text": panel_text,
        "need_text": need_txt,
        "root_text": root_txt,
        "free_text": free_txt,
        "variant_download_size_bytes": expected_bytes,
        "need_binary": fmt_binary_gb(expected_bytes),
        "need_decimal": fmt_decimal_gb(expected_bytes),
        "disk_usage_free_before_click": free_before_click,
        "disk_usage_free_after_refusal": free_after,
        "free_binary": fmt_binary_gb(free_after),
        "free_decimal": fmt_decimal_gb(free_after),
        "network_bodies": disk_bodies[:4],
    }
    ok &= check("refusal parsed", m is not None, refusal)
    ok &= check("refusal root on tiny fs", bool(root_txt) and root_txt.startswith(str(TINY)), f"{root_txt}")
    fmt = fmt_binary_gb if SIDE == "before" else fmt_decimal_gb
    ok &= check(f"refusal need matches {SIDE} formula", need_txt == fmt(expected_bytes),
                f"need={need_txt!r} expected={fmt(expected_bytes)!r} bytes={expected_bytes}")
    ok &= check(f"refusal free matches {SIDE} formula", free_txt in (fmt(free_before_click), fmt(free_after)),
                f"free={free_txt!r} expected={fmt(free_after)!r} disk_usage.free={free_after}")
    facts["refusal"]["need_matches_card"] = need_txt == card_size
    print(f"FACT refusal_need={need_txt!r} card_size={card_size!r} need_matches_card={need_txt == card_size}", flush=True)
    return ok


if __name__ == "__main__":
    sys.exit(main())
