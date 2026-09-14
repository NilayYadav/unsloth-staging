# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: the tool card an MCP Apps result draws when a conversation is reopened.

Both sides are seeded with the SAME stored conversation through the real chat-history
API, and the same ``ui://`` template is served to the page by one fetch interceptor.
Only the frontend build differs, so the card is the single variable: a merge-base Studio
has no widget renderer and falls back to serialising the result, while the head build
recognises the envelope and draws the template in its sandboxed frame.

Reopening is the path being photographed on purpose: the chat stream never runs again for
a stored thread, so this is what an MCP Apps server looks like on a restart.
"""

from __future__ import annotations

import json
import os
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
THREAD_ID = "pr9301-mcp-app-thread"
MESSAGE_ID = "pr9301-mcp-app-message"
THREAD_TITLE = "MCP Apps widget evidence"
CREATED_AT = 1_755_000_000_000

# The template the server would answer resources/read with. Deliberately tiny and
# self-contained: it must render identically on both sides so any difference in the
# composite is the card, not the widget.
WIDGET_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  body{margin:0;font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;color:#0f172a;
       background:#f8fafc}
  .card{padding:14px 16px}
  h1{margin:0 0 10px;font-size:15px;letter-spacing:.01em}
  .row{display:flex;justify-content:space-between;padding:6px 0;
       border-bottom:1px solid #e2e8f0}
  .row:last-child{border-bottom:0}
  .k{color:#475569} .v{font-variant-numeric:tabular-nums;font-weight:600}
  button{margin-top:12px;padding:6px 12px;border:1px solid #cbd5e1;border-radius:6px;
         background:#fff;font:inherit;cursor:pointer}
</style></head>
<body><div class="card">
  <h1>Weather &mdash; San Francisco</h1>
  <div class="row"><span class="k">Temperature</span><span class="v">18&deg;C</span></div>
  <div class="row"><span class="k">Humidity</span><span class="v">72%</span></div>
  <div class="row"><span class="k">Wind</span><span class="v">14 km/h</span></div>
  <button type="button">Refresh</button>
</div></body></html>
"""


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


def _seed(session: Session, tool_name: str, resource_uri: str) -> dict:
    """Store one assistant turn whose MCP tool result carries the widget envelope."""
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
    args = {"city": "San Francisco"}
    # The shape the head backend's __MCP_UI__ envelope parses into, which is also what
    # the adapter persists: the server's content blocks in order, plus the structured
    # payload. A merge-base build has no reader for the `ui` key at all.
    answer = "San Francisco: 18C, humidity 72%, wind 14 km/h."
    result = {
        "text": answer,
        "ui": {
            "resourceUri": resource_uri,
            "content": [{"type": "text", "text": answer}],
            "structuredContent": {"tempC": 18, "humidity": 72, "windKph": 14},
        },
    }
    message = {
        "id": MESSAGE_ID,
        "threadId": THREAD_ID,
        "role": "assistant",
        "content": [
            {
                "type": "tool-call",
                "toolCallId": "call_pr9301",
                "toolName": tool_name,
                "argsText": json.dumps(args),
                "args": args,
                "result": result,
            }
        ],
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
    *,
    server_id: str = "a3f9c1d2e4b6f807",
    bare_tool: str = "get_weather",
    resource_uri: str = "ui://weather-server/dashboard",
    **_: object,
) -> tuple[list[Path], dict]:
    """Photograph the tool card for a stored MCP Apps result."""
    tool_name = f"mcp__{server_id}__{bare_tool}"
    saved = _seed(session, tool_name, resource_uri)
    stored = api_get(session, f"/api/chat/threads/{THREAD_ID}/messages")

    # The template endpoint only exists on the head build, so it is served to the page
    # on BOTH sides. That keeps the widget's own bytes out of the comparison: what the
    # composite shows is whether the card asks for it and draws it at all.
    resource_payload = {
        "uri": resource_uri,
        "mime_type": "text/html;profile=mcp-app",
        "text": WIDGET_HTML,
        "ui": {},
    }
    fixture_script = f"""
(() => {{
  const resource = {json.dumps(resource_payload)};
  window.__pr9301ResourceRequests = 0;
  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {{
    const raw = typeof input === "string"
      ? input : input instanceof Request ? input.url : String(input);
    const url = new URL(raw, window.location.origin);
    if (url.pathname.endsWith("/ui-resource")) {{
      window.__pr9301ResourceRequests += 1;
      return new Response(JSON.stringify(resource), {{
        status: 200,
        headers: {{"content-type": "application/json"}},
      }});
    }}
    return realFetch(input, init);
  }};
}})();
"""
    auth_script = seed_init_script(
        type(
            "A",
            (),
            {"access_token": session.access_token, "refresh_token": session.refresh_token},
        )(),
        [],
    )

    async with open_chat(
        session.base_url,
        init_scripts=[auth_script, fixture_script],
        viewport=(1280, 900),
        headless=True,
    ) as sp:
        page = sp.page
        # Open the seeded conversation from the history list, the way a user returns to
        # it. Asserting the title is on screen afterwards guards the click that misses.
        entry = page.get_by_text(THREAD_TITLE, exact=True).first
        await entry.wait_for(state="visible", timeout=90_000)
        await entry.click()

        card = page.locator('[data-slot="tool-fallback-root"]').first
        await card.wait_for(state="visible", timeout=60_000)
        # The card is collapsed by default and the widget sits OUTSIDE the collapsible,
        # so a widget that renders is visible without expanding anything. Expand anyway:
        # the merge-base side has nothing to show until the result pane is open.
        trigger = card.locator('[data-slot="tool-fallback-trigger"]').first
        await trigger.click()
        await page.wait_for_timeout(2_500)

        frame = card.locator(f'iframe[title="{bare_tool} app"]')
        frame_count = await frame.count()
        widget_text = None
        if frame_count:
            # The shell document.write()s the template in, so the body is empty until
            # that lands and a handle taken before it reads blank. Poll rather than
            # sleep once, or the fact says "" for a widget the screenshot shows painted.
            widget_text = ""
            for _ in range(30):
                try:
                    body = await frame.first.content_frame().locator("body").inner_text()
                except Exception:  # noqa: BLE001 -- mid-rewrite, or never painted
                    body = ""
                if body.strip():
                    widget_text = " ".join(body.split())
                    break
                await page.wait_for_timeout(500)

        shot = out_dir / f"{label.lower()}_mcp_app_widget_card.png"
        await card.screenshot(path=str(shot))

        card_text = " ".join((await card.inner_text()).split())
        facts = {
            "seeded_message_id": saved.get("id"),
            "stored_message_count": len(stored.get("messages", [])),
            "tool_name": tool_name,
            "resource_uri": resource_uri,
            "widget_iframe_count": frame_count,
            "widget_rendered": bool(frame_count),
            "widget_body_text": widget_text,
            "ui_resource_requests": await page.evaluate("window.__pr9301ResourceRequests"),
            "card_text": card_text,
            "card_shows_raw_resource_uri": resource_uri in card_text,
            "card_char_count": len(card_text),
        }
        return [shot], facts
