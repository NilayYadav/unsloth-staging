"""Scene: the Images viewer after a Transform run whose upload is a phone photo (PR 10553).

A photo off a phone stores its pixels landscape and carries an EXIF Orientation tag saying
"show me rotated". The browser honours the tag, so the dropzone preview is upright; the
backend decoded the raw pixels, so the img2img source -- and therefore the OUTPUT SIZE, which
the conditioned workflows take from the upload -- was sideways.

The photograph is the Images viewer holding the finished Transform result. The facts carry
what a picture of a generated image cannot settle on its own: the size the gallery recorded
for the run, and which quadrant of the SOURCE ended up top-left after the decode. The upload
is a four-quadrant colour card at low strength, so the result keeps the layout and the
orientation is readable both by eye and by pixel.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import time
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

DEFAULT_REPO = "unsloth/Z-Image-Turbo-GGUF"
DEFAULT_FILE = "z-image-turbo-Q4_K_S.gguf"

# One colour per stored quadrant, so a mirror is distinguishable from a rotation.
RED, GREEN, BLUE, YELLOW = (220, 20, 20), (20, 200, 20), (20, 20, 220), (220, 200, 20)
NAMES = {RED: "red", GREEN: "green", BLUE: "blue", YELLOW: "yellow"}
# Orientation 6 = rotate 90 clockwise for display, so the stored bottom-left lands top-left.
BROWSER_TRUTH = ["blue", "red", "yellow", "green"]


def _phone_photo(width: int, height: int, orientation: int = 6) -> str:
    from PIL import Image

    img = Image.new("RGB", (width, height))
    img.paste(RED, (0, 0, width // 2, height // 2))
    img.paste(GREEN, (width // 2, 0, width, height // 2))
    img.paste(BLUE, (0, height // 2, width // 2, height))
    img.paste(YELLOW, (width // 2, height // 2, width, height))
    exif = img.getexif()
    exif[0x0112] = orientation
    buf = io.BytesIO()
    img.save(buf, format = "JPEG", quality = 95, subsampling = 0, exif = exif)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _quadrants(img) -> list:
    w, h = img.size
    out = []
    for fx, fy in ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)):
        px = img.getpixel((int(w * fx), int(h * fy)))[:3]
        out.append(NAMES[min(NAMES, key = lambda c: sum((a - b) ** 2 for a, b in zip(c, px)))])
    return out


def _fetch(session: Session, url: str) -> bytes:
    """The generated PNG's bytes, from the same server that was photographed."""
    import urllib.request

    path = url if url.startswith("/") else "/" + url
    req = urllib.request.Request(
        f"{session.base_url}{path}",
        headers = {"Authorization": f"Bearer {session.access_token}"},
    )
    with urllib.request.urlopen(req, timeout = 300) as r:
        return r.read()


def _load_and_wait(session: Session, body: dict, timeout_s: int = 3600) -> dict:
    api_post(session, "/api/inference/images/unload", {}, timeout = 300)
    api_post(session, "/api/inference/images/load", body, timeout = timeout_s)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(5)
        status = api_get(session, "/api/inference/images/status")
        if status.get("loaded"):
            return status
        progress = api_get(session, "/api/inference/images/load-progress")
        if progress.get("error"):
            raise RuntimeError(f"load failed: {str(progress['error'])[:400]}")
        if progress.get("phase") == "downloading":
            print(f"  [load] downloading {progress.get('fraction', 0):.1%}", flush = True)
    raise RuntimeError(f"model did not load within {timeout_s}s")


async def drive(session: Session, out_dir: Path, label: str,
                repo: str = DEFAULT_REPO, filename: str = DEFAULT_FILE,
                src_w: int = 768, src_h: int = 448, steps: int = 10,
                strength: float = 0.30, **_: object) -> tuple[list[Path], dict]:
    from PIL import Image

    status = _load_and_wait(session, {"model_path": repo, "gguf_filename": filename,
                                      "model_kind": "gguf"})
    facts: dict = {"loaded": status.get("loaded")}

    data_url = _phone_photo(src_w, src_h)
    stored = Image.open(io.BytesIO(base64.b64decode(data_url.partition(",")[2])))
    facts["stored_pixels_on_the_wire"] = list(stored.size)
    facts["exif_orientation_tag"] = 6
    facts["browser_preview_size"] = [src_h, src_w]
    facts["browser_truth_quadrants"] = BROWSER_TRUTH

    before_ids = {r.get("id") for r in (api_get(session, "/api/inference/images/gallery")
                                        .get("images") or [])}

    # img2img bounds the upload by the Resolution box and never enlarges, so a 768x448 source
    # inside a 1024x1024 box comes out at its own size -- 768x448 when the tag is ignored,
    # 448x768 when it is applied. Both are already multiples of 16, so the snap is a no-op and
    # the recorded size is the decode's answer with nothing else mixed in.
    records = (api_post(session, "/api/inference/images/generate", {
        "prompt": "a flat four-colour test card, sharp edges",
        "width": 1024,
        "height": 1024,
        "steps": steps,
        "guidance": 1.0,
        "seed": 12345,
        "strength": strength,
        "init_image": data_url,
    }, timeout = 3600).get("images") or [])
    if not records:
        raise RuntimeError("generate persisted no gallery record")
    fresh = [r for r in records if r.get("id") not in before_ids] or records
    rec = fresh[0]

    facts["gallery_recorded_size"] = [rec.get("width"), rec.get("height")]
    facts["gallery_workflow"] = rec.get("workflow")
    facts["result_is_portrait"] = bool(rec.get("height", 0) > rec.get("width", 0))

    # The pixels themselves: the record's size could in principle be right while the picture is
    # mirrored, and only a mirror-sensitive read separates orientation 6 from orientation 5.
    raw = _fetch(session, rec["url"])
    out = Image.open(io.BytesIO(raw)).convert("RGB")
    facts["result_pixel_size"] = list(out.size)
    facts["result_quadrants"] = _quadrants(out)
    facts["result_matches_browser_truth"] = facts["result_quadrants"] == BROWSER_TRUTH

    shots: list[Path] = []
    init = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(), []
    )
    async with open_chat(session.base_url, init_scripts = [init],
                         viewport = (1600, 1000), headless = True) as sp:
        page = sp.page
        await page.goto(f"{session.base_url}/images", wait_until = "domcontentloaded")
        await page.wait_for_timeout(10_000)
        shot = out_dir / f"{label.lower()}_transform_result.png"
        await page.screenshot(path = str(shot))
        shots.append(shot)

    # The decoded source as the model actually received it, saved beside the UI shot so the
    # composite shows the sideways/upright pair even where the viewer scales the thumbnail.
    src_shot = out_dir / f"{label.lower()}_result_pixels.png"
    out.save(src_shot)
    shots.append(src_shot)

    api_post(session, "/api/inference/images/unload", {}, timeout = 300)
    return shots, facts


if __name__ == "__main__":
    import argparse

    from pr_ui_scenes._common import studio_session

    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required = True)
    ap.add_argument("--home", type = Path, required = True)
    ap.add_argument("--password", required = True)
    ap.add_argument("--out", type = Path, required = True)
    ap.add_argument("--label", default = "AFTER")
    a = ap.parse_args()
    a.out.mkdir(parents = True, exist_ok = True)
    s = studio_session(a.url, a.home, a.password)
    print(json.dumps(asyncio.run(drive(s, a.out, a.label))[1], indent = 2))
