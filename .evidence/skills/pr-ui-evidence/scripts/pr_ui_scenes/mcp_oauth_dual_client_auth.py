"""Scene: adding an OAuth MCP server whose authorization server enforces Notion's
"one client authentication method per token request" rule.

The MCP server and its authorization server are `_mock_oauth_mcp_server.py`, started
per side on its own port. It reproduces what mcp.notion.com was measured doing:
dynamic registration assigns `client_secret_basic`, and `/token` answers a request
carrying BOTH a Basic header and a body `client_id` with 400 invalid_request
"Client must not use multiple authentication methods". Its `/authorize` auto-approves,
standing in for the human at the consent screen -- the one part of the real flow that
cannot run unattended.

Both sides run the identical flow through the real UI: open the composer's MCP menu,
Manage MCP servers, then Refresh tools on the row. What differs is only the token
request the backend builds, which the authorization server's own event log records.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post, pick_free_ports  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

MOCK_SERVER = Path(__file__).resolve().parent / "_mock_oauth_mcp_server.py"
DISPLAY_NAME = "Notion MCP (local stand-in)"


def _start_mock(port: int, state: Path, tools: list[str]) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, str(MOCK_SERVER), "--port", str(port),
         "--state", str(state), "--tools", ",".join(tools)],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/_probe", timeout=2).read()
            return proc
        except Exception:  # noqa: BLE001 -- not up yet
            if proc.poll() is not None:
                raise RuntimeError("mock OAuth/MCP server exited during startup")
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError(f"mock OAuth/MCP server never answered on :{port}")


def _delete(session: Session, path: str) -> None:
    req = urllib.request.Request(
        f"{session.base_url}{path}",
        headers={"Authorization": f"Bearer {session.access_token}"},
        method="DELETE",
    )
    urllib.request.urlopen(req, timeout=60).read()


def _events(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/_probe", timeout=10) as r:
        return json.loads(r.read())["events"]


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    *,
    tool_names: list[str] | None = None,
    **_: object,
) -> tuple[list[Path], dict]:
    tool_names = list(tool_names or ["search", "fetch_page", "create_page"])
    # Its own band: the Studio ports come from the driver's default 8990+ range and a
    # collision here would point the scene at a Studio instead of the mock server.
    port = pick_free_ports(1, start=9400, stop=9500)[0]
    state = out_dir / "oauth_events.json"
    mock = _start_mock(port, state, tool_names)
    url = f"http://127.0.0.1:{port}/mcp"

    try:
        # A home is reused across runs, so an earlier run's row would still be in the
        # dialog, pointing at a port nothing listens on any more. Both sides must open
        # the dialog on exactly one row, and it has to be this run's.
        for existing in api_get(session, "/api/mcp/servers/"):
            _delete(session, f"/api/mcp/servers/{existing['id']}")

        created = api_post(session, "/api/mcp/servers/", {
            "display_name": DISPLAY_NAME,
            "url": url,
            "is_enabled": True,
            "use_oauth": True,
        })

        auth_script = seed_init_script(
            type("A", (), {"access_token": session.access_token,
                           "refresh_token": session.refresh_token})(),
            [],
        )
        # The MCP pill is only rendered on an expanded composer, and MCP-for-chat is
        # what expands it. Off by default, so seed it before the first navigation or
        # the composer has no MCP control at all.
        mcp_script = 'localStorage.setItem("unsloth_chat_mcp_enabled", "true");'
        async with open_chat(session.base_url, init_scripts=[auth_script, mcp_script],
                             viewport=(1280, 760), headless=True) as sp:
            page = sp.page
            await page.get_by_role("button", name="MCP servers").first.click()
            manage = page.get_by_text("Manage MCP servers", exact=True).first
            await manage.wait_for(state="visible", timeout=30_000)
            await manage.click()

            dialog = page.get_by_role("dialog").filter(has_text="MCP Servers").first
            await dialog.wait_for(state="visible", timeout=30_000)
            # The row must actually be the server this scene added; a dialog that
            # opened on an empty list would photograph as a clean "no change".
            row = dialog.locator("li").filter(has_text=DISPLAY_NAME).first
            await row.wait_for(state="visible", timeout=30_000)
            assert url in await row.inner_text(), "dialog row is not the mock server"

            await row.get_by_role("button", name="Refresh tools").click()
            # OAuth runs inside the backend: discovery, registration, the browser
            # hand-off, then the token exchange. The toast is the first moment either
            # outcome is on screen.
            toast = page.locator("[data-sonner-toast]").first
            await toast.wait_for(state="visible", timeout=300_000)
            await page.wait_for_timeout(1_500)
            toast_text = " ".join((await toast.inner_text()).split())

            shot = out_dir / f"{label.lower()}_mcp_oauth_dual_client_auth.png"
            await page.screenshot(path=str(shot))

        # Read the outcome again through the API, so the numbers in the facts come from
        # the same Studio that was photographed rather than from the toast's wording.
        probe = api_post(session, f"/api/mcp/servers/{created['id']}/refresh", {})
        events = _events(port)
        token_requests = [e for e in events if e["event"] == "token_request"]
        token_responses = [e for e in events if e["event"] == "token_response"]
        last_request = token_requests[-1] if token_requests else {}
        last_response = token_responses[-1] if token_responses else {}

        facts = {
            "server_url": url,
            "use_oauth": created["use_oauth"],
            "toast_text": toast_text,
            "toast_mentions_multiple_auth": "multiple authentication methods" in toast_text,
            "refresh_ok": probe["ok"],
            "tools_listed": probe["tool_count"],
            "refresh_error": probe.get("error"),
            "registered_auth_method": next(
                (e["assigned_auth_method"] for e in events if e["event"] == "register"), None),
            "token_requests": len(token_requests),
            "token_request_had_authorization_header": last_request.get(
                "had_authorization_header"),
            "token_request_had_client_id": last_request.get("had_body_client_id"),
            "token_request_form_keys": last_request.get("form_keys"),
            "token_status": last_response.get("status"),
            "token_error": (last_response.get("body") or {}).get("error_description"),
            "mcp_authenticated_requests": sum(
                1 for e in events if e["event"] == "mcp_authenticated"),
            "servers_configured": len(api_get(session, "/api/mcp/servers/")),
        }
        return [shot], facts
    finally:
        mock.kill()
