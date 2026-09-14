# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: the video surface Studio serves and documents at /docs.

No model is loaded and none is needed: the claim is about the API surface itself.
The merge base serves video only through Studio's own ``/api/inference/video/*``
routes, so an OpenAI SDK client asking for ``/v1/videos`` gets a 404 and the docs
page lists no such operation. The head build mounts the videos router on both
prefixes, so five ``/v1/videos`` operations join the same page, ``GET /v1/videos``
answers with an OpenAI list envelope, and the Studio-native rows stay put.

Both sides are filtered to operations whose path contains "video", so the shot
carries its own control: the native rows must appear on BOTH sides. An empty
BEFORE panel would mean the base install broke, not that the PR added a surface.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

# Keep only the video operations, and drop the page furniture around them. Walks the
# rendered rows rather than using :has(), which silently matched nothing.
FILTER_JS = """
() => {
  const isVideo = (el) => {
    const p = el.querySelector(".opblock-summary-path");
    return !!p && /video/i.test(p.textContent || "");
  };
  let kept = 0;
  document.querySelectorAll(".opblock").forEach((op) => {
    if (isVideo(op)) { kept += 1; } else { op.style.display = "none"; }
  });
  document.querySelectorAll(".opblock-tag-section").forEach((sec) => {
    if (!sec.querySelector(".opblock:not([style*='display: none'])")) {
      sec.style.display = "none";
    }
  });
  document.querySelectorAll(".information-container, .scheme-container, .models")
    .forEach((el) => { el.style.display = "none"; });
  return kept;
}
"""


def _probe(session: Session, path: str) -> dict:
    """Raw status + body for one endpoint; a 404 is the point on the base side."""
    req = urllib.request.Request(
        session.base_url.rstrip("/") + path,
        headers = {"Authorization": f"Bearer {session.access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout = 120) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    try:
        body = json.loads(raw)
    except ValueError:
        body = {}
    return {"status": status, "body": body if isinstance(body, dict) else {}}


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph the docs page's video rows and record the live API surface."""
    schema = _probe(session, "/openapi.json")["body"]
    paths = schema.get("paths") or {}
    video_paths = sorted(p for p in paths if p.startswith("/v1/videos"))
    operations = sorted(
        f"{method.upper()} {path}"
        for path in video_paths
        for method in (paths.get(path) or {})
        if method.lower() in ("get", "post", "delete", "put", "patch")
    )
    listing = _probe(session, "/v1/videos")

    facts = {
        "openapi_v1_videos_paths": video_paths,
        "openapi_v1_videos_operations": operations,
        "openapi_v1_videos_operation_count": len(operations),
        "get_v1_videos_status": listing["status"],
        "get_v1_videos_object": listing["body"].get("object"),
        # Control: the pre-existing Studio-native routes must be untouched on both
        # sides. The trailing slash matters -- the PR also mounts its own router at
        # /api/inference/videos, and a bare "/api/inference/video" prefix counts those
        # too, which turns the control into a second copy of the claim.
        "studio_native_video_op_count": sum(
            1
            for p in paths
            if p.startswith("/api/inference/video/")
            for m in (paths.get(p) or {})
            if m.lower() in ("get", "post", "delete", "put", "patch")
        ),
    }

    auth_script = seed_init_script(
        type(
            "A",
            (),
            {
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
            },
        )(),
        [],
    )

    shots: list[Path] = []
    async with open_chat(
        session.base_url,
        init_scripts = [auth_script],
        viewport = (1200, 1400),
        headless = True,
    ) as sp:
        docs_url = session.base_url.rstrip("/") + "/docs"
        rendered = False
        # Swagger fetches /openapi.json itself and shows "Failed to load API
        # definition" if that race is lost; a clean retry is the difference
        # between evidence and a blank AFTER panel.
        for _attempt in range(5):
            await sp.page.goto(docs_url, wait_until = "domcontentloaded")
            try:
                await sp.page.wait_for_selector(".opblock", timeout = 25_000)
                rendered = True
                break
            except Exception:  # noqa: BLE001 -- retry the load, not the assertion
                await sp.page.wait_for_timeout(3000)
        facts["docs_page_rendered"] = rendered
        if not rendered:
            raise RuntimeError(f"[{label}] /docs never rendered an operation row")

        kept = await sp.page.evaluate(FILTER_JS)
        facts["docs_video_rows_shown"] = int(kept)
        await sp.page.wait_for_timeout(500)
        shot = out_dir / f"{label}-docs-videos.png"
        await sp.screenshot(shot, full_page = True)
        shots.append(shot)

    return shots, facts
