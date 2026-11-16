#!/usr/bin/env python3
# Generated live Studio probe. Never print provider keys, passwords, tokens or API keys.
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from studio_test_kit.auth import StudioAuth, anthropic_provider, gemini_provider, openai_provider, seed_init_script
from studio_test_kit.compose import hstack_images, hstack_videos
from studio_test_kit.flows import image_generation, multi_turn_chat, tool_pills, vision_upload
from studio_test_kit.lifecycle import health_body_ok
from studio_test_kit.ui import StudioPage, open_chat, send_prompt, wait_for_stream

SCENARIO = "smoke"
PROVIDER = "gemini"
MODEL = "gemini-2.5-flash"
PROMPT = "Say hello from Studio CI in one short sentence."
TURNS = ["Translate 'good morning' into Japanese.", "Now answer in a pirate voice.", "Summarize this thread in 5 words."]
RECORD_VIDEO = False
LOCAL_MODEL = "unsloth/gemma-3-270m-it-GGUF"
LOCAL_GGUF_VARIANT = "UD-Q4_K_XL"
LOCAL_MAX_SEQ_LENGTH = 1024
LOCAL_PARALLEL = 1
PRE_REF = ""
INSTALL_UNIX = "bash ./install.sh --local --no-torch"
NEW_PASSWORD = "StudioLive-" + uuid.uuid4().hex[:12] + "-Pw1!"
_PW_RE = re.compile(r"(?i)(?:bootstrap|initial|generated)\s*password(?:\s+is)?\s*[:=]?\s+(\S+)")
_SAVED_RE = re.compile(r"password saved to:\s*(\S+)")


def pass_log(message: str) -> None:
    print(f"PASS {message}", flush=True)


def warn(message: str) -> None:
    print(f"WARN {message}", flush=True)


def fail(message: str) -> None:
    print(f"FAIL {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def find_unsloth_bin(home: Path) -> Path:
    candidates = [
        home / "bin" / "unsloth",
        home / "bin" / "unsloth.exe",
        home / "unsloth_studio" / "bin" / "unsloth",
        home / "unsloth_studio" / "Scripts" / "unsloth.exe",
    ]
    candidates.extend(home.glob(".venv*/bin/unsloth"))
    candidates.extend(home.glob(".venv*/Scripts/unsloth.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    fail(f"could not find unsloth CLI under {home}")


def read_bootstrap_password(home: Path, log_path: Path, timeout_s: int = 60) -> str | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        files = [home / "auth" / ".bootstrap_password"]
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            log_text = ""
        files.extend(Path(m) for m in _SAVED_RE.findall(log_text))
        for path in files:
            try:
                text = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if text:
                return text
        match = _PW_RE.search(log_text)
        if match:
            return match.group(1).strip().strip(".,")
        time.sleep(1)
    return None


def read_api_key(log_path: Path, timeout_s: int = 900) -> str:
    deadline = time.time() + timeout_s
    pattern = re.compile(r"API Key:\s+(sk-unsloth-[a-f0-9]+)")
    while time.time() < deadline:
        try:
            match = pattern.search(log_path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            match = None
        if match:
            return match.group(1)
        time.sleep(2)
    fail("timed out waiting for the Studio run API key in the log")


def wait_for_health(base_url: str, timeout_s: int = 300) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for path in ("/api/health", "/healthz"):
            if health_body_ok(base_url + path):
                return path
        time.sleep(2)
    fail(f"Studio gave no JSON health body on /api/health or /healthz within {timeout_s}s")


def start_process(cmd: list[str], log_path: Path, env: dict[str, str]) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict = {"stdout": None, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    print("Launching: " + " ".join(cmd[:3]) + " ...", flush=True)
    with log_path.open("w", encoding="utf-8") as handle:
        kwargs["stdout"] = handle
        return subprocess.Popen(cmd, **kwargs)


def studio_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["UNSLOTH_STUDIO_HOME"] = str(home)
    env.pop("STUDIO_HOME", None)
    return env


def start_plain_studio(home: Path, log_path: Path, port: int) -> subprocess.Popen:
    cmd = [str(find_unsloth_bin(home)), "studio", "-H", "127.0.0.1", "-p", str(port)]
    return start_process(cmd, log_path, studio_env(home))


def start_local_studio_run(home: Path, log_path: Path, port: int) -> subprocess.Popen:
    cmd = [
        str(find_unsloth_bin(home)), "studio", "run",
        "--model", LOCAL_MODEL,
        "--port", str(port),
        "--host", "127.0.0.1",
        "--api-key-name", "ci",
        "--max-seq-length", str(LOCAL_MAX_SEQ_LENGTH),
        "--parallel", str(LOCAL_PARALLEL),
        "--disable-tools",
    ]
    if LOCAL_GGUF_VARIANT:
        cmd.extend(["--gguf-variant", LOCAL_GGUF_VARIANT])
    return start_process(cmd, log_path, studio_env(home))


def stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        proc.terminate()
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            proc.kill()
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                proc.kill()


def provider_seed(model: str):
    builders = {"gemini": gemini_provider, "openai": openai_provider, "anthropic": anthropic_provider}
    secret = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}[PROVIDER]
    key = os.environ.get(secret)
    if not key:
        fail(f"{secret} is not available to this job (setup failure, not product evidence)")
    return builders[PROVIDER](api_key=key, models=[model])


async def auth_init(base_url: str, password: str, providers: list | None = None) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{base_url}/api/auth/login", json={"username": "unsloth", "password": password})
        r.raise_for_status()
        body = r.json()
        if body.get("must_change_password"):
            r = await client.post(
                f"{base_url}/api/auth/change-password",
                headers={"Authorization": "Bearer " + body["access_token"]},
                json={"current_password": password, "new_password": NEW_PASSWORD},
            )
            r.raise_for_status()
            body = r.json()
            pass_log("Studio first-boot password change completed")
    pass_log("Studio API login succeeded")
    auth = StudioAuth(access_token=body["access_token"], refresh_token=body.get("refresh_token", ""), base_url=base_url)
    return seed_init_script(auth, providers or [])


@asynccontextmanager
async def _open_non_chromium(base_url, init_scripts, video_dir, video_name, browser_name):
    from playwright.async_api import async_playwright
    sp = None
    before = set(video_dir.glob("page@*.webm")) if video_dir else set()
    try:
        async with async_playwright() as p:
            browser = await getattr(p, browser_name).launch(headless=True)
            kw: dict = {"viewport": {"width": 1440, "height": 900}}
            if video_dir is not None:
                video_dir.mkdir(parents=True, exist_ok=True)
                kw["record_video_dir"] = str(video_dir)
                kw["record_video_size"] = {"width": 1440, "height": 900}
            context = await browser.new_context(**kw)
            for script in init_scripts:
                await context.add_init_script(script)
            page = await context.new_page()
            await page.goto(f"{base_url}/chat", wait_until="domcontentloaded")
            sp = StudioPage(page=page, context=context, base_url=base_url)
            try:
                yield sp
            finally:
                await context.close()
                await browser.close()
    finally:
        if sp is not None and video_dir is not None:
            fresh = sorted(v for v in video_dir.glob("page@*.webm") if v not in before)
            if fresh:
                final = video_dir / f"{video_name}.webm"
                final.unlink(missing_ok=True)
                fresh[-1].rename(final)
                sp.video_webm = final


def session(base_url: str, init_scripts: list[str], artifact_dir: Path, label: str, browser_name: str):
    vdir = artifact_dir / "video" / label if RECORD_VIDEO else None
    name = f"{label}-{browser_name}"
    if browser_name == "chromium":
        return open_chat(base_url, init_scripts=init_scripts, video_dir=vdir, video_name=name)
    return _open_non_chromium(base_url, init_scripts, vdir, name, browser_name)


STRIPES = ('[data-slot="sidebar-inset"]{background: repeating-linear-gradient(115deg,#f2c14e 0 90px,#3fa7d6 90px 180px,'
           '#e4572e 180px 270px,#eef 270px 360px) !important} [data-sonner-toaster], .toast-surface{display:none !important}')
GLASS_INIT = ("localStorage.setItem('unsloth_window_material', JSON.stringify({state:{material:'liquid-3d',"
              "style:'liquid-3d'},version:0}));")
READ_STYLES = """() => {
  const sc = document.querySelector('.composer-run-settings');
  const one = (sel) => { const e = sc.querySelector(sel); const c = getComputedStyle(e); const b = getComputedStyle(e, '::before');
    return {bg: c.backgroundColor, before_bg: b.backgroundColor + ' ' + b.backgroundImage, before_blur: b.backdropFilter || b.webkitBackdropFilter}; };
  const menu = sc.closest('[data-slot$="content"]');
  return {glass: document.documentElement.getAttribute('data-glass'), menus: document.documentElement.hasAttribute('data-glass-menus'),
          menu_bg: getComputedStyle(menu).backgroundColor, header: one('[data-config-header-row]'), footer: one('[data-pinned-footer]'),
          stuck: sc.hasAttribute('data-header-stuck'), scroll: sc.scrollTop};
}"""


def fetch_tiny_gguf(home: Path) -> None:
    names = ("python.exe", "python") if os.name == "nt" else ("python", "python3")
    found = [c for d in (home / "unsloth_studio" / "bin", home / "unsloth_studio" / "Scripts", find_unsloth_bin(home).parent)
             for n in names for c in [d / n] if c.is_file()]
    python = found[0] if found else Path(sys.executable)
    if not found:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "huggingface_hub"], check=False, timeout=600)
    code = ("from huggingface_hub import hf_hub_download; "
            "print(hf_hub_download('unsloth/gemma-3-270m-it-GGUF', 'gemma-3-270m-it-UD-Q4_K_XL.gguf'))")
    out = subprocess.run([str(python), "-c", code], capture_output=True, text=True, timeout=900)
    if out.returncode != 0:
        fail("SETUP could not download the tiny GGUF: " + out.stderr[-400:])
    pass_log("tiny GGUF is in the Hugging Face cache")


async def scenario_smoke(base_url, password, browser_name, artifact_dir):
    from playwright.async_api import async_playwright
    init = await auth_init(base_url, password)
    async with async_playwright() as p:
        browser = await getattr(p, browser_name).launch(headless=True)
        context = await browser.new_context(viewport={"width": 1500, "height": 950}, device_scale_factor=2, color_scheme="dark")
        await context.add_init_script(init)
        await context.add_init_script(GLASS_INIT)
        page = await context.new_page()
        await page.goto(f"{base_url}/chat", wait_until="domcontentloaded")
        await page.locator("form:has(textarea) textarea").first.wait_for(state="visible", timeout=90_000)
        await page.add_style_tag(content=STRIPES)
        await page.get_by_text("Select model").first.click(timeout=30_000)
        gear = page.locator('[aria-label^="Run settings for"]').first
        try:
            await gear.wait_for(state="attached", timeout=60_000)
        except Exception:
            await page.screenshot(path=str(artifact_dir / "setup-no-model.png"))
            fail("SETUP the model picker lists no local model with Run settings")
        await gear.click(force=True, timeout=15_000)
        panel = page.locator(".composer-run-settings")
        await panel.wait_for(state="visible", timeout=30_000)
        await page.wait_for_timeout(2500)

        async def clip():
            box = await page.evaluate("""() => { const m = document.querySelector('.composer-run-settings').closest('[data-slot$="content"]').getBoundingClientRect();
              const y = Math.max(0, m.top - 30); return {x: Math.max(0, m.left - 30), y, width: m.width + 60, height: Math.min(m.height + 60, innerHeight - y)}; }""")
            return box

        rest = await page.evaluate(READ_STYLES)
        await page.screenshot(path=str(artifact_dir / "run-settings-top.png"), clip=await clip())
        switch = panel.get_by_role("switch").first
        if await switch.get_attribute("aria-checked") != "true":
            await switch.click(force=True)
        await page.wait_for_timeout(2000)
        await page.evaluate("() => { const sc = document.querySelector('.composer-run-settings'); sc.scrollTop = 150; }")
        await page.wait_for_timeout(2000)
        scrolled = await page.evaluate(READ_STYLES)
        await page.screenshot(path=str(artifact_dir / "run-settings-scrolled.png"), clip=await clip())
        (artifact_dir / "run-settings-styles.json").write_text(json.dumps({"rest": rest, "scrolled": scrolled}, indent=1), encoding="utf-8")
        await context.close()
        await browser.close()

    print("ENGAGED glass=%s menus=%s menu_bg=%s scroll=%s stuck=%s" % (rest["glass"], rest["menus"], rest["menu_bg"], scrolled["scroll"], scrolled["stuck"]), flush=True)
    if not rest["glass"] or not rest["menus"] or "/ 0." not in rest["menu_bg"] and "rgba(" not in rest["menu_bg"]:
        fail("SETUP Glass menus are not active, so the changed rules cannot apply")
    if scrolled["scroll"] < 100:
        fail("SETUP the Run settings list did not scroll")
    clear = ("rgba(0, 0, 0, 0)", "transparent")
    fills = {"header": scrolled["header"]["bg"], "footer": scrolled["footer"]["bg"], "header@rest": rest["header"]["bg"]}
    if any(v not in clear for v in fills.values()):
        fail(f"pinned Run settings rows paint the glass menu fill a second time: {fills}")
    blurs = {"header": scrolled["header"]["before_blur"], "footer": scrolled["footer"]["before_blur"]}
    if any("blur(16px)" not in (v or "") for v in blurs.values()):
        fail(f"pinned Run settings rows have no frost layer: {blurs}")
    pass_log(f"pinned Run settings rows add no second tint and frost what scrolls under them ({browser_name}): fills={fills} blurs={blurs}")


async def scenario_provider_chat(base_url, password, browser_name, artifact_dir):
    init = await auth_init(base_url, password, [provider_seed(MODEL)])
    async with session(base_url, [init], artifact_dir, "provider-chat", browser_name) as sp:
        result = await multi_turn_chat(sp, MODEL, TURNS, artifact_dir / "provider-chat")
    result.attach_video(sp)
    pass_log(f"provider chat completed {result.artefacts.get('turn_count', len(TURNS))} turns")


async def scenario_image_gen(base_url, password, browser_name, artifact_dir):
    init = await auth_init(base_url, password, [provider_seed(MODEL)])
    async with session(base_url, [init], artifact_dir, "image-gen", browser_name) as sp:
        result = await image_generation(sp, MODEL, PROMPT, artifact_dir / "image-gen")
    result.attach_video(sp)
    pass_log(f"image generation saved {result.artefacts.get('image_bytes')} bytes")


async def scenario_tools_pills(base_url, password, browser_name, artifact_dir):
    init = await auth_init(base_url, password, [provider_seed(MODEL)])
    async with session(base_url, [init], artifact_dir, "tools-pills", browser_name) as sp:
        result = await tool_pills(sp, MODEL, artifact_dir / "tools-pills")
    result.attach_video(sp)
    pass_log("Search and Code composer pill flow completed")


async def scenario_vision_upload(base_url, password, browser_name, artifact_dir):
    image_path = Path(os.environ.get("STUDIO_VISION_IMAGE", ""))
    if not image_path.is_file():
        image_path = artifact_dir / "vision" / "input.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkAAIAAAoAAv/lxKUAAAAASUVORK5CYII="))
    init = await auth_init(base_url, password, [provider_seed(MODEL)])
    async with session(base_url, [init], artifact_dir, "vision-upload", browser_name) as sp:
        result = await vision_upload(sp, MODEL, image_path, PROMPT, artifact_dir / "vision-upload")
    result.attach_video(sp)
    pass_log("vision upload flow completed")


async def scenario_local_chat(base_url, api_key, browser_name, artifact_dir, password):
    payload = {"model": LOCAL_MODEL, "messages": [{"role": "user", "content": PROMPT}],
               "stream": False, "max_tokens": 64, "temperature": 0}
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(f"{base_url}/v1/chat/completions",
                              headers={"Authorization": f"Bearer {api_key}"}, json=payload)
        r.raise_for_status()
        body = r.json()
    (artifact_dir / "local-chat-response.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
    content = (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if not content:
        fail("local-chat API returned no assistant content")
    pass_log("local tiny-model chat API returned assistant content")
    if not password:
        warn("no bootstrap password found; local-chat UI leg not run (API leg above is the evidence)")
        return
    init = await auth_init(base_url, password)
    async with session(base_url, [init], artifact_dir, "local-chat", browser_name) as sp:
        await send_prompt(sp, PROMPT)
        await wait_for_stream(sp, timeout_ms=180_000)
        await sp.screenshot(artifact_dir / f"local-chat-{browser_name}.png")
    pass_log("local tiny-model chat UI accepted a prompt and finished streaming")


def install_pre_ref(root: Path, log_path: Path) -> Path:
    if os.name == "nt":
        fail("side-by-side is Unix-only")
    repo, home = root / "pre-repo", root / "pre-home"
    origin = subprocess.check_output(["git", "remote", "get-url", "origin"], text=True).strip()
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "fetch", "-q", "--depth", "1", origin, PRE_REF], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=repo, check=True)
    got = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    print(f"pre-ref {PRE_REF} -> {got}", flush=True)
    home.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        subprocess.run(INSTALL_UNIX, cwd=repo, env=studio_env(home), stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=2700, shell=True)
    return home


async def scenario_side_by_side(post_base, post_password, browser_name, artifact_dir):
    root = artifact_dir / "side-by-side"
    pre_home = install_pre_ref(root, artifact_dir / "pre-install.log")
    pre_port = free_port()
    pre_base = f"http://127.0.0.1:{pre_port}"
    pre_proc = start_plain_studio(pre_home, artifact_dir / "pre-studio.log", pre_port)
    try:
        wait_for_health(pre_base)
        pre_password = read_bootstrap_password(pre_home, artifact_dir / "pre-studio.log")
        if not pre_password:
            fail("could not read the pre-ref Studio bootstrap password")

        async def drive(label, base, password):
            init = await auth_init(base, password, [provider_seed(MODEL)])
            async with session(base, [init], artifact_dir, label, browser_name) as sp:
                result = await multi_turn_chat(sp, MODEL, TURNS, artifact_dir / label)
            return result.attach_video(sp)

        pre = await drive("pre", pre_base, pre_password)
        post = await drive("post", post_base, post_password)
        combined = artifact_dir / "combined"
        combined.mkdir(parents=True, exist_ok=True)
        for idx, (left, right) in enumerate(zip(pre.screenshots, post.screenshots), start=1):
            hstack_images(left, right, combined / f"sxs_{idx:02d}.png", label_left="base", label_right="head")
        if pre.video_webm and post.video_webm and shutil.which("ffmpeg"):
            try:
                hstack_videos(pre.video_webm, post.video_webm, combined / "sxs.mp4")
            except Exception as exc:
                warn(f"side-by-side video composition skipped: {exc}")
        pass_log("side-by-side base/head provider chat completed")
    finally:
        stop_process(pre_proc)


async def main() -> None:
    home = Path(os.environ["UNSLOTH_STUDIO_HOME"]).resolve()
    browser_name = os.environ.get("STUDIO_BROWSER", "chromium")
    artifact_dir = Path(os.environ.get("STUDIO_ARTIFACT_DIR", "studio-live-artifacts")).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    proc = None
    try:
        if SCENARIO == "local-chat":
            log_path = artifact_dir / "studio-run.log"
            proc = start_local_studio_run(home, log_path, port)
            api_key = read_api_key(log_path)
            pass_log(f"Studio run healthy at {wait_for_health(base_url, timeout_s=900)}")
            await scenario_local_chat(base_url, api_key, browser_name, artifact_dir,
                                      read_bootstrap_password(home, log_path, timeout_s=10))
            return
        fetch_tiny_gguf(home)
        log_path = artifact_dir / "studio.log"
        proc = start_plain_studio(home, log_path, port)
        pass_log(f"Studio healthy at {wait_for_health(base_url)}")
        password = read_bootstrap_password(home, log_path)
        if not password:
            fail("could not read the Studio bootstrap password from the install home or log")
        if SCENARIO == "side-by-side":
            await scenario_side_by_side(base_url, password, browser_name, artifact_dir)
        else:
            await {
                "smoke": scenario_smoke,
                "provider-chat": scenario_provider_chat,
                "image-gen": scenario_image_gen,
                "tools-pills": scenario_tools_pills,
                "vision-upload": scenario_vision_upload,
            }[SCENARIO](base_url, password, browser_name, artifact_dir)
    finally:
        stop_process(proc)


if __name__ == "__main__":
    asyncio.run(main())
