# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: what a canvas tells the user when the network-access setting blocked it.

Both sides are seeded with the SAME stored thread through the real chat-history API,
carrying one assistant turn whose ```html fence is a full document that pulls a single
script from a CDN. "Allow canvas network access" is left at its default (off) on both
sides, so the preview frame's strict CSP refuses that script and posts one
``unsloth:artifact-blocked`` report back to the parent. No internet is needed: the
policy refuses the load before a request goes out.

Only the frontend build differs, so the notice is the single variable. The merge base
answers with a one-line strip along the BOTTOM of the canvas that names a count and a
host and offers a single "Allow for this canvas" button -- it never mentions that a
setting is what turned this off. The head build answers at the TOP with a titled alert
that names the setting, links into Settings -> Chat, and can be dismissed.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

# Fixed ids: the shot must be reproducible, and the thread is looked up by title.
THREAD_ID = "pr9894-canvas-network-thread"
MESSAGE_ID = "pr9894-canvas-network-message"
THREAD_TITLE = "Canvas network access evidence"
CREATED_AT = 1_755_000_000_000

# One external host, so the singular "Blocked 1 external resource from ..." string is
# what both sides render. Keeping the count identical is the control: the host and the
# number must NOT move between sides, only the presentation around them.
BLOCKED_HOST = "cdn.jsdelivr.net"
BLOCKED_URL = f"https://{BLOCKED_HOST}/npm/chart.js@4.4.1/dist/chart.umd.min.js"

# Must start with <!doctype html> or markdown-text.tsx leaves it a plain code block
# instead of collapsing it into an artifact card (isFullHtmlDocument).
CANVAS_HTML = f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  body{{margin:0;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#0f172a;
       background:#f8fafc}}
  .wrap{{padding:18px 20px}}
  h1{{margin:0 0 6px;font-size:16px}}
  p{{margin:0 0 14px;color:#475569}}
  .panel{{height:180px;border:1px dashed #cbd5e1;border-radius:8px;display:flex;
         align-items:center;justify-content:center;color:#94a3b8;background:#fff}}
</style></head>
<body><div class="wrap">
  <h1>Quarterly revenue</h1>
  <p>Rendered with Chart.js from a CDN.</p>
  <div class="panel" id="chart">chart area</div>
</div>
<script src="{BLOCKED_URL}"></script>
</body></html>
"""

# The message body is ONLY the fence, so the Streamdown block the card replaces is
# exactly this document and nothing shares the block with it.
MESSAGE_TEXT = f"```html\n{CANVAS_HTML}```"


def _put(session: Session, path: str, payload: dict, timeout: int = 120) -> dict:
    """Authenticated PUT. The message store has no POST route, and seeding through
    the real endpoint is what keeps both sides reading identical stored bytes."""
    req = urllib.request.Request(
        f"{session.base_url}{path}",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {session.access_token}",
        },
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _seed(session: Session) -> dict:
    """Store one assistant turn whose whole body is the CDN-loading canvas."""
    api_post(
        session,
        "/api/chat/threads",
        {
            "id": THREAD_ID,
            "title": THREAD_TITLE,
            "modelType": "base",
            "modelId": "",
            "createdAt": CREATED_AT,
            "updatedAt": CREATED_AT,
        },
    )
    message = {
        "id": MESSAGE_ID,
        "threadId": THREAD_ID,
        "role": "assistant",
        "content": [{"type": "text", "text": MESSAGE_TEXT}],
        "createdAt": CREATED_AT,
    }
    return _put(
        session,
        f"/api/chat/threads/{THREAD_ID}/messages/{MESSAGE_ID}",
        message,
    )


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph the blocked-canvas notice with network access left off."""
    saved = _seed(session)
    stored = api_get(session, f"/api/chat/threads/{THREAD_ID}/messages")

    auth = type(
        "A",
        (),
        {"access_token": session.access_token, "refresh_token": session.refresh_token},
    )()
    # artifactsEnabled gates the fence -> card collapse; the network flag is written
    # explicitly rather than left absent so the shot cannot be blamed on a stale home.
    auth_script = seed_init_script(
        auth,
        [],
        extra_local_storage={
            "unsloth_chat_artifacts_enabled": "true",
            "unsloth_chat_allow_artifact_network_access": "false",
        },
    )

    async with open_chat(
        session.base_url,
        init_scripts=[auth_script],
        viewport=(1280, 900),
        headless=True,
    ) as sp:
        page = sp.page
        entry = page.get_by_text(THREAD_TITLE, exact=True).first
        await entry.wait_for(state="visible", timeout=90_000)
        await entry.click()

        # The card, not the raw fence: if this never appears the artifact path is off
        # and the rest of the scene would photograph a code block on both sides.
        card = page.get_by_role("button", name=re.compile(r"^Open .* preview$")).first
        await card.wait_for(state="visible", timeout=60_000)
        await card.click()

        surface = page.locator('section[aria-label$=" canvas"]').first
        await surface.wait_for(state="visible", timeout=60_000)
        frame = surface.locator("iframe").first
        await frame.wait_for(state="visible", timeout=60_000)

        # The report only lands after the shell document.write()s the page and the CSP
        # refuses the script, so poll for the notice rather than sleeping once.
        # Scope every lookup to the iframe's own wrapper. The surface header ends in a
        # `absolute inset-x-0 bottom-0 h-px` divider, and a document-order querySelector
        # over the whole section finds THAT first: it reads as an empty banner and the
        # merge-base side silently photographs "no notice at all".
        banner_js = """(root) => {
          const iframe = root.querySelector('iframe');
          const frame = iframe ? iframe.parentElement : root;
          const alert = frame.querySelector('[data-slot="alert"]');
          const strip = frame.querySelector('div.absolute.inset-x-0.bottom-0');
          const node = alert || strip;
          if (!node) return null;
          const r = node.getBoundingClientRect();
          // Measured against the canvas itself, not the section, so "top" and
          // "bottom" mean where the notice sits over the page it is about.
          const f = (iframe || frame).getBoundingClientRect();
          const buttons = Array.from(node.querySelectorAll('button')).map(
            (b) => (b.getAttribute('aria-label') || b.innerText || '').trim(),
          );
          return {
            is_alert_component: Boolean(alert),
            text: node.innerText || '',
            centre_fraction: (r.top + r.height / 2 - f.top) / f.height,
            buttons,
            title: (node.querySelector('[data-slot="alert-title"]')?.innerText
                    || '').trim(),
          };
        }"""

        geometry = None
        banner_text = ""
        for _ in range(40):
            geometry = await surface.evaluate(banner_js)
            if geometry and (geometry.get("text") or "").strip():
                banner_text = " ".join(geometry["text"].split())
                break
            await page.wait_for_timeout(500)

        shot = out_dir / f"{label.lower()}_canvas_network_blocked_alert.png"
        await surface.screenshot(path=str(shot))

        geometry = geometry or {}
        buttons = geometry.get("buttons") or []
        centre = geometry.get("centre_fraction")
        facts = {
            "seeded_message_id": saved.get("id"),
            "stored_message_count": len(stored.get("messages", [])),
            # Control: identical on both sides, or the pair is not comparable.
            "blocked_host": BLOCKED_HOST,
            "network_setting_enabled": False,
            "banner_present": bool(banner_text),
            "banner_text": banner_text,
            "banner_char_count": len(banner_text),
            "banner_names_host": BLOCKED_HOST in banner_text,
            # The PR's claims, one fact each.
            "uses_alert_component": bool(geometry.get("is_alert_component")),
            "banner_title": geometry.get("title", ""),
            "banner_centre_fraction": (
                round(centre, 3) if isinstance(centre, (int, float)) else None
            ),
            "banner_at_top": (
                bool(centre < 0.5) if isinstance(centre, (int, float)) else None
            ),
            "banner_buttons": buttons,
            "banner_button_count": len(buttons),
            "has_open_settings_button": any("settings" in b.lower() for b in buttons),
            "has_dismiss_button": any("dismiss" in b.lower() for b in buttons),
            "has_allow_for_canvas_button": any("allow" in b.lower() for b in buttons),
            "mentions_the_setting_by_name": "network access" in banner_text.lower(),
        }
        return [shot], facts
