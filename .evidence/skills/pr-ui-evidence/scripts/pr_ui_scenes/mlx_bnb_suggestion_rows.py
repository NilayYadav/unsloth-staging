# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: the model picker's suggestion rows on an Apple Silicon (MLX) host.

Serves PR 9722. mlx-lm cannot read bitsandbytes NF4 weights, so
``FastMLXModel.from_pretrained`` silently rewrites ``unsloth/<model>-bnb-4bit``
to its full-precision base and downloads THAT. Every bnb row Studio suggests on
a Mac is therefore a download that gets thrown away: pick
``unsloth/Qwen2-VL-2B-Instruct-bnb-4bit`` (1.56 GB) and the load fetches
``unsloth/Qwen2-VL-2B-Instruct`` (4.43 GB) instead, with nothing on screen
saying so.

The rows come from ``/api/models/list``, which is curated defaults plus the
remote unsloth-by-downloads ranking. Both halves carry bnb repos on a Mac, so
both halves are counted here; the ranking is live, so the count of bnb rows is
the claim, not the exact list.

``/api/inference/validate`` is read for the same pick because
``mlx_loads_base_model`` is the field both the notice and the progress target are
driven from.

The load is photographed with the BASE repo absent from the Hub cache -- parked
aside for the duration and put back afterwards -- because that is the only state
in which the two sides can disagree about what is being downloaded. With the base
already cached both builds finish instantly and the shot proves nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import sys
import time
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

BNB_SUFFIXES = ("-unsloth-bnb-4bit", "-bnb-4bit")
DEFAULT_PICK = "unsloth/Qwen2-VL-2B-Instruct-bnb-4bit"
MLX_TOAST = "MLX cannot use 4-bit bitsandbytes weights"
CACHED_LINE = "Loading cached model into memory."
# One clock for both sides. The earlier version shot on the arrival of the MLX
# notice and fell back to a timer when it never came, which watched the two builds
# for different lengths of time; at head the notice and the progress toast are
# written in the same tick, so a fixed offset is both simpler and stricter -- the
# frames differ by what Studio had to say at 95s, not by how long each was watched.
# 95 and not 20: the worker announces the swap ~13s in and the xet transport only
# opens ~18s in. The bar still reads 0.0 GB even here: an unauthenticated xet
# transfer materialises no measurable bytes into the cache the progress endpoint
# reads for at least that long. The 4.4 GB TOTAL is the retarget, not the percent.
SHOT_AT_SECONDS = 95.0
PROGRESS_RE = re.compile(r"\bof\s+[\d.]+\s*[KMG]B", re.I)
# The curated list is 21 entries; anything above that means the background
# ranking fetch has landed and both halves of the list are on screen.
CURATED_LEN = 21


def _bnb(names) -> list[str]:
    return [n for n in names if isinstance(n, str) and n.endswith(BNB_SUFFIXES)]


def _base_of(repo_id: str) -> str:
    """The repo mlx-lm actually loads, by the loader's own suffix rule.

    Derived here rather than read from ``mlx_loads_base_model`` on purpose: that
    field does not exist on the BEFORE build, and the cache has to be in the same
    state on both sides or the pair compares two different downloads.
    """
    for suffix in BNB_SUFFIXES:
        if repo_id.endswith(suffix):
            return repo_id[: -len(suffix)]
    return repo_id


async def _pause_stack(page, toast) -> bool:
    """Park the pointer on the toast stack and leave it there.

    sonner pauses every dismiss timer while the pointer is over the stack, which is
    the only way the notice (~4s) and the progress bar are ever in one frame. It is
    the TOAST that has to be hovered, not the ``<ol data-sonner-toaster>`` around it:
    the list is ``pointer-events: none`` in this build, so Playwright's actionability
    check on it fails and the earlier version silently never paused anything.
    """
    try:
        await toast.first.hover(timeout=3_000)
        return True
    except Exception:  # noqa: BLE001
        pass
    try:
        box = await toast.first.bounding_box()
        if box:
            await page.mouse.move(box["x"] + box["width"] / 2,
                                  box["y"] + box["height"] / 2)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _hub_cache() -> Path:
    """The Hub cache both this driver and the Studio it launched are reading."""
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


@contextlib.contextmanager
def _base_repo_parked(repo_id: str):
    """Hide an already-cached repo for the length of one side, then put it back.

    RENAME, never delete: this is the caller's own multi-GB Hub cache, and the
    point is only that the load has to fetch the base rather than find it. The
    partial download the scene provokes is what gets thrown away at the end; the
    original directory is moved back exactly as it was. A run killed between the
    two halves leaves the parked copy behind, so the next one restores it first.
    """
    hub = _hub_cache()
    slug = repo_id.replace("/", "--")
    live, parked = hub / f"models--{slug}", hub / f".uidiff-parked--{slug}"
    if parked.is_dir() and not live.exists():
        parked.rename(live)
    moved = False
    if live.is_dir():
        shutil.rmtree(parked, ignore_errors=True)
        live.rename(parked)
        moved = True
    try:
        yield moved
    finally:
        if moved:
            shutil.rmtree(live, ignore_errors=True)
            parked.rename(live)


def _settled_hardware(session: Session, timeout_s: int = 600) -> tuple[dict, float]:
    """Wait out the MLX self-heal before reading anything.

    A FRESH install boots chat-only -- mlx_lm fails to import on a tokenizers pin --
    reports no GPU, and serves the GGUF curated list; a background reinstall then
    fixes it about seventy seconds later. Every fact this scene collects is
    device-dependent, so a read inside that window photographs a machine that is not
    the one the load runs on, and the two sides land in the window by different
    amounts. Gate on the healed state so both are read on the same host.
    """
    t0 = time.monotonic()
    hw = {}
    while time.monotonic() - t0 < timeout_s:
        hw = api_get(session, "/api/system/hardware")
        if (hw.get("gpu") or {}).get("gpu_name") and hw.get("export_supported"):
            break
        time.sleep(5)
    return hw, round(time.monotonic() - t0, 1)


def _settled_model_list(session: Session, timeout_s: int = 90) -> dict:
    """Read /api/models/list until the remote ranking has been folded in.

    The fetch is a background thread kicked by the FIRST read, so an immediate
    call returns curated defaults alone -- and a pair where one side waited and
    the other did not compares two different halves of the same feature.
    """
    deadline = time.monotonic() + timeout_s
    data = api_get(session, "/api/models/list")
    while time.monotonic() < deadline:
        if len(data.get("default_models") or []) > CURATED_LEN:
            return data
        time.sleep(3)
        data = api_get(session, "/api/models/list")
    return data


async def drive(session: Session, out_dir: Path, label: str,
                pick: str = DEFAULT_PICK, clip: dict | None = None,
                **_: object) -> tuple[list[Path], dict]:
    """Photograph the open picker; return what the same server says the rows are."""
    facts: dict = {}

    # The control: this whole effect is MLX-only, so the pair is worthless unless both
    # sides agree they ran on the same Apple Silicon host.
    try:
        hw, waited = _settled_hardware(session)
        facts["gpu_name"] = (hw.get("gpu") or {}).get("gpu_name")
        facts["export_supported"] = hw.get("export_supported")
        facts["hardware_settle_seconds"] = waited
    except Exception as exc:  # noqa: BLE001 -- the rows are still the evidence
        facts["hardware_error"] = f"{type(exc).__name__}: {exc}"

    data = _settled_model_list(session)
    defaults = data.get("default_models") or []
    curated, fetched = defaults[:CURATED_LEN], defaults[CURATED_LEN:]
    facts["suggested_total"] = len(defaults)
    facts["bnb_suggestions"] = _bnb(defaults)
    facts["bnb_suggestion_count"] = len(facts["bnb_suggestions"])
    facts["bnb_in_curated"] = _bnb(curated)
    facts["bnb_in_fetched_ranking"] = _bnb(fetched)
    facts["ranking_fetched"] = len(fetched) > 0

    # The same swap, on the field the chat toast reads. None on the pre-PR side.
    try:
        v = api_post(session, "/api/inference/validate",
                     {"model_path": pick, "load_in_4bit": True})
        facts["validate_pick"] = pick
        facts["validate_valid"] = v.get("valid")
        facts["mlx_loads_base_model"] = v.get("mlx_loads_base_model")
    except Exception as exc:  # noqa: BLE001
        facts["validate_error"] = f"{type(exc).__name__}: {exc}"

    shots: list[Path] = []
    base_repo = _base_of(pick)
    facts["mlx_base_repo_expected"] = base_repo
    # The whole load happens with the base repo hidden, so BOTH builds face the same
    # uncached 4.43 GB fetch. It is restored on the way out, partial download and all.
    with _base_repo_parked(base_repo) as parked:
        facts["base_repo_was_cached"] = parked
        init = seed_init_script(
            type("A", (), {"access_token": session.access_token,
                           "refresh_token": session.refresh_token})(), []
        )
        async with open_chat(session.base_url, init_scripts=[init],
                             viewport=(1500, 1000), headless=True) as sp:
            page = sp.page
            # The trigger lives in the header, NOT in the composer form: the kit's
            # pick_model looks for a composer button and times out on this build.
            await page.get_by_role("button", name="Select model").first.click(timeout=60_000)
            await page.wait_for_timeout(6_000)

            # Reached by its exact repo id through the Hub search, so the SAME row is
            # clicked on both sides. On the AFTER side the curated list no longer offers
            # it, and picking a row that only one side has would compare two flows.
            box = page.get_by_placeholder("Search Unsloth models").first
            await box.fill(pick.split("/", 1)[1])
            rows = page.locator(".model-list-scroll button")
            row = rows.filter(has_text=pick.split("/", 1)[1]).first
            await row.wait_for(state="visible", timeout=60_000)
            facts["picked_row"] = (await row.inner_text()).strip().replace("\n", " | ")
            await row.click()

            toast = page.locator("[data-sonner-toast]")
            seen: list[str] = []

            async def _live() -> list[str]:
                out = []
                for i in range(await toast.count()):
                    text = (await toast.nth(i).inner_text()).strip().replace("\n", " | ")
                    if text:
                        out.append(text)
                return out

            async def _collect() -> list[str]:
                live = await _live()
                for text in live:
                    if text not in seen:
                        seen.append(text)
                return live

            # sonner dismisses the notice after ~4s and pauses every timer while the
            # toaster is hovered, so hover as soon as anything is on screen -- ~0.3s,
            # before either toast is due. Without it the 12s frame has lost the notice.
            t0 = time.monotonic()
            deadline = t0 + SHOT_AT_SECONDS
            hovered = False
            live: list[str] = []
            while time.monotonic() < deadline - 0.35:
                live = await _collect()
                if not hovered and live:
                    hovered = await _pause_stack(page, toast)
                await page.wait_for_timeout(120)
            rest = deadline - time.monotonic()
            if rest > 0:
                await page.wait_for_timeout(int(rest * 1000))
            live = await _collect()
            facts["hovered_to_pause"] = hovered
            facts["shot_at_seconds"] = round(time.monotonic() - t0, 2)
            out_dir.mkdir(parents=True, exist_ok=True)
            shot = out_dir / f"{label.lower()}_mlx_pick.png"
            if clip:
                await page.screenshot(path=str(shot), clip=clip)
            else:
                await page.screenshot(path=str(shot))
            shots.append(shot)

            # What the photographed frame actually says, so the image and the facts are
            # the same reading rather than two independent claims.
            loading = next((t for t in live if "Cancel" in t), "")
            facts["toasts_on_screen"] = live
            facts["load_toast_text"] = loading
            facts["load_toast_headline"] = loading.split(" | ", 1)[0] if loading else ""
            facts["load_toast_says_cached"] = CACHED_LINE in loading
            # Over the WHOLE frame, and against the derived id rather than validate's
            # answer: by 20s the load toast is a byte counter, and the base is named by
            # the notice beside it -- and the predicate has to mean the same thing on a
            # build that has no such field.
            facts["frame_names_base"] = any(base_repo in t for t in live)
            progress = PROGRESS_RE.search(loading)
            facts["download_progress_visible"] = bool(progress)
            facts["download_progress_text"] = progress.group(0) if progress else ""
            facts["toasts_seen"] = seen
            facts["mlx_toast_seen"] = any(MLX_TOAST in t for t in seen)
            facts["mlx_toast_text"] = next((t for t in seen if MLX_TOAST in t), "")

        # The click started a real load of a multi-GB repo. Stop it: /unload is the same
        # cancel_load path the PR touches, so nothing is left downloading behind the shot.
        try:
            # Bounded: /unload pads its response while a download is in flight, and the
            # default 600s let a finished scene sit there for ten minutes. The cancel is
            # best-effort cleanup, never evidence.
            api_post(session, "/api/inference/unload", {"model_path": pick}, timeout=90)
        except Exception as exc:  # noqa: BLE001
            facts["unload_error"] = f"{type(exc).__name__}: {exc}"
    return shots, facts


if __name__ == "__main__":
    import argparse

    from pr_ui_scenes._common import studio_session

    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--label", default="AFTER")
    ap.add_argument("--pick", default=DEFAULT_PICK)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    s = studio_session(a.url, a.home, a.password)
    print(asyncio.run(drive(s, a.out, a.label, pick=a.pick)))
