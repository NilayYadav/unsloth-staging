"""Scene: the export panel for a GGUF export whose destination is the Hugging Face Hub.

The panel streams the export worker's stdout live, so a Hub export on the merge base
shows the whole merge -> convert -> quantize sequence twice: once for the local save,
then again inside push_to_hub_gguf, which re-runs it into its own directory before
uploading. The fix uploads the files the first pass already built.

Everything photographed is real: a real Studio on each side, a real model loaded from
a plain directory, a real llama.cpp conversion, a real request to the Hub endpoint the
Studio was launched with. That endpoint is the one stand-in, pointed at a local server
that answers token validation, repo creation and commits, and logs what it received.
The facts come from the same Studio's /api/export/logs ring buffer, polled during the
run, and from that Hub log.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

CONVERT = "Converting to GGUF format"
QUANTIZE = "Quantizing to"
COMPLETE = re.compile(r"GGUF export complete -> (.+?)\s*$")
PUSHING = "Pushing GGUF model to Hub"
ELAPSED = re.compile(r"\b(?:\d+h \d{2}m \d{2}s|\d+m \d{2}s|\d+s)\b")
# Shape the validator accepts, and nothing a real Hub would.
EVIDENCE_TOKEN = "hf_" + ("evidence" * 5)[:34]


def _poll_logs(session: Session, cursor: int) -> tuple[list[dict], int, bool]:
    data = api_get(session, f"/api/export/logs?since={cursor}", timeout=60)
    return data.get("entries", []), int(data.get("cursor", cursor)), bool(data.get("active"))


def _zoo_identity(home: Path) -> dict:
    """The unsloth_zoo each side actually runs: both installs pull it from git."""
    import hashlib
    out: dict = {"unsloth_zoo_version": None, "mlx_utils_sha256_12": None}
    for meta in home.glob("unsloth_studio/lib/python3*/site-packages/unsloth_zoo-*.dist-info/METADATA"):
        for line in meta.read_text(errors="replace").splitlines():
            if line.startswith("Version:"):
                out["unsloth_zoo_version"] = line.split(":", 1)[1].strip()
                break
    for utils in home.glob("unsloth_studio/lib/python3*/site-packages/unsloth_zoo/mlx/utils.py"):
        out["mlx_utils_sha256_12"] = hashlib.sha256(utils.read_bytes()).hexdigest()[:12]
    return out


def _hub_events(log_path: str, offset: int) -> list[dict]:
    if not log_path or not Path(log_path).exists():
        return []
    with open(log_path, "rb") as handle:
        handle.seek(offset)
        raw = handle.read().decode(errors="replace")
    events = []
    for line in raw.splitlines():
        try:
            events.append(json.loads(line))
        except Exception:
            pass
    return events


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    model_dir: str = "",
    hf_username: str = "evidence-user",
    model_name: str = "Qwen2.5-0.5B-Instruct-GGUF",
    quant_label: str = "Q4_K_M",
    fake_hub_log: str = "",
    timeout_s: int = 3600,
    hf_token: str = EVIDENCE_TOKEN,
    quant_labels: list[str] | None = None,
    private_repo: bool = False,
    **_: object,
) -> tuple[list[Path], dict]:
    quant_labels = list(quant_labels or [quant_label])
    hub_offset = Path(fake_hub_log).stat().st_size if fake_hub_log and Path(fake_hub_log).exists() else 0

    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(),
        [],
        extra_local_storage={"unsloth_hf_token": hf_token},
    )
    async with open_chat(session.base_url, init_scripts=[auth_script],
                         viewport=(1440, 1100), headless=True) as sp:
        page = sp.page
        await page.goto(f"{session.base_url}/export", wait_until="domcontentloaded")

        # Source: a plain directory, typed. No Hub lookup is involved in loading it.
        await page.get_by_role("tab", name="Local Model").click()
        # While the (isolated, empty) cache scan runs the field reads "Scanning..." and is
        # not accepting input; wait for the settled placeholder before typing.
        box = page.get_by_placeholder("./models/my-model")
        await box.first.wait_for(state="visible", timeout=180_000)
        await box.first.fill(model_dir)
        await box.first.press("Enter")

        # Method and quant.
        # Not name=regex: Playwright's role selector cannot carry a "/" inside the pattern.
        await page.get_by_role("button").filter(
            has_text=re.compile(r"GGUF.*Llama\.cpp")
        ).first.click()
        for q_label in quant_labels:
            quant = page.get_by_role("button", name=re.compile(rf"^{re.escape(q_label)}\b"))
            if await quant.count() == 0:
                quant = page.get_by_text(q_label, exact=True)
            await quant.first.click()

        # The page's own token field, when the Local tab shows one; the store is seeded either way.
        token_box = page.get_by_placeholder("hf_...")
        if await token_box.count() and await token_box.first.is_visible():
            await token_box.first.fill(hf_token)

        export_btn = page.get_by_role("button", name="Export Model", exact=True)
        await export_btn.first.wait_for(state="visible", timeout=60_000)
        for _ in range(60):
            if await export_btn.first.is_enabled():
                break
            await page.wait_for_timeout(500)
        await export_btn.first.click()

        # Destination: the Hub, with the username the panel asks for.
        await page.get_by_role("button", name="Push to Hub", exact=True).first.click()
        await page.get_by_placeholder("your-username").first.fill(hf_username)
        await page.get_by_placeholder("my-model-GGUF").first.fill(model_name)
        # The panel carries its own token field for the push; fill every visible one.
        panel_tokens = page.get_by_placeholder("hf_...")
        for i in range(await panel_tokens.count()):
            if await panel_tokens.nth(i).is_visible():
                await panel_tokens.nth(i).fill(hf_token)
        if private_repo:
            switch = page.locator("#export-private-repo")
            await switch.first.wait_for(state="visible", timeout=10_000)
            if (await switch.first.get_attribute("aria-checked")) != "true":
                await switch.first.click()

        started = time.time()
        await page.get_by_role("button", name="Start Export", exact=True).first.click()

        # Poll the log while the run is live: the buffer is a ring, so read it as it fills.
        entries: list[dict] = []
        cursor = 0
        done = page.get_by_role("button", name="Done", exact=True)
        deadline = started + timeout_s
        terminal = False
        while time.time() < deadline:
            new, cursor, _active = _poll_logs(session, cursor)
            entries.extend(new)
            if await done.count() and await done.first.is_visible():
                terminal = True
                break
            await page.wait_for_timeout(3_000)
        new, cursor, _active = _poll_logs(session, cursor)
        entries.extend(new)
        wall = round(time.time() - started, 1)
        if not terminal:
            raise RuntimeError(f"export did not reach a terminal state within {timeout_s}s")

        panel = page.locator("div.rounded-2xl").filter(has=done.first).first
        await panel.scroll_into_view_if_needed()
        # Studio's own update toast (a real llama.cpp release check) can sit over the log.
        later = page.get_by_role("button", name="Remind me later")
        if await later.count() and await later.first.is_visible():
            await later.first.click()
            await page.wait_for_timeout(500)
        await page.wait_for_timeout(1_000)
        shot = out_dir / f"{label.lower()}_gguf_hub_export_panel.png"
        await panel.screenshot(path=str(shot))
        text = " ".join((await panel.inner_text()).split())

        # Second shot: the log scrolled so "Pushing GGUF model to Hub" is the top line. The
        # tail view cannot hold both passes; this one shows what follows the push on each side.
        log_box = panel.locator("div.overflow-auto").filter(has_text=PUSHING).first
        scrolled = await log_box.evaluate(
            """(box, needle) => {
                 const el = [...box.querySelectorAll('*')].find(
                   n => n.children.length === 0 && (n.textContent || '').includes(needle));
                 if (!el) return false;
                 box.scrollTop = Math.max(0, el.offsetTop - box.offsetTop - 4);
                 return true;
               }""",
            PUSHING,
        )
        await page.wait_for_timeout(500)
        shot_push = out_dir / f"{label.lower()}_gguf_hub_export_after_push_line.png"
        await panel.screenshot(path=str(shot_push))

    lines = [e.get("line", "") for e in entries]
    complete_dirs = [m.group(1) for line in lines if (m := COMPLETE.search(line))]
    hub = _hub_events(fake_hub_log, hub_offset)
    commits = [ev for ev in hub if "commit" in ev]
    elapsed = ELAPSED.search(text)
    facts = {
        "model_dir": model_dir,
        "quant": quant_label,
        "quants": quant_labels,
        "repo_id": f"{hf_username}/{model_name}",
        "private_repo": private_repo,
        "destination": "hub",
        "conversion_passes": sum(CONVERT in line for line in lines),
        "quantize_passes": sum(QUANTIZE in line for line in lines),
        "export_complete_dirs": complete_dirs,
        "second_conversion_dir": complete_dirs[1] if len(complete_dirs) > 1 else None,
        "hub_push_started": any(PUSHING in line for line in lines),
        "log_line_count": len(lines),
        "panel_elapsed_text": elapsed.group(0) if elapsed else None,
        "wall_clock_seconds": wall,
        "panel_success": "Export finished" in text,
        "panel_reports_error": ("error" in text.lower()) or ("failed" in text.lower()),
        "hub_repo_created": any(ev.get("path") == "/api/repos/create" for ev in hub),
        "hub_files_committed": sorted({f.get("path") for ev in commits for f in ev.get("commit", [])}),
        "hub_request_count": len(hub),
        "last_log_lines": lines[-6:],
        "push_line_scrolled_into_view": bool(scrolled),
        **_zoo_identity(Path(session.home)),
    }
    return [shot, shot_push], facts
