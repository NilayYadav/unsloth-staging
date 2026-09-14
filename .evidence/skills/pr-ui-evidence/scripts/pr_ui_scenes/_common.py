# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/_common.py
"""Shared plumbing for PR before/after Studio scenes.

Everything here exists because it bit me during the PR 8222 pilot. Each helper
guards one failure mode that produced a screenshot pair which looked perfectly
fine and proved nothing:

  pick_free_ports      a port already held by an unrelated Studio -> launch_studio
                       silently fails to bind, /healthz answers from the WRONG
                       install, and both sides photograph someone else's build
  studio_session       the bootstrap password is written to auth/.bootstrap_password,
                       NOT inlined in the log (so lifecycle's regex finds nothing),
                       and every API 403s with "Password change required" until it
                       is rotated
  assert_showing       a detail panel keeps its previous selection while the click
                       lands on the search box, so the shot is of another model
  open_list            the toolbar format filter also reads "GGUF"; clicking it opens
                       a menu identical on both sides, which photographs as NO CHANGE

The last one is the dangerous class: a wrong selector that still yields a clean
image on both sides is indistinguishable from "the PR changed nothing" unless you
look at the picture. Always look at the picture.
"""

from __future__ import annotations

import json
import re
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def pick_free_ports(count: int, start: int = 8990, stop: int = 9200) -> list[int]:
    """`count` ports nothing is listening on, proven by actually binding them.

    Not a guess from a range: this workspace routinely has a dozen Studios alive
    from earlier sessions, and a collision does not raise -- it serves someone
    else's UI on the port you asked for.

    Binding proves the port is free NOW, which is not a reservation. A second
    session running this same driver walks the same default band and can take the
    port between the probe and the launch, so its Studio answers the login and the
    run dies on someone else's rotated password. Set PR_UI_PORT_BASE to move this
    run onto its own band when another session is already using the default one.
    """
    base = os.environ.get("PR_UI_PORT_BASE")
    if base:
        start = int(base)
        stop = start + 200
    free: list[int] = []
    for port in range(start, stop):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
            free.append(port)
        except OSError:
            pass
        finally:
            s.close()
        if len(free) == count:
            return free
    raise RuntimeError(f"could not find {count} free ports in [{start}, {stop})")


@dataclass
class Session:
    """An authenticated Studio, proven to be the install we meant."""

    base_url: str
    home: Path
    access_token: str
    refresh_token: str
    password: str


def _post(url: str, payload: dict, token: Optional[str] = None, timeout: int = 120) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def studio_session(base_url: str, home: Path, new_password: str) -> Session:
    """Log in to `base_url` with THIS home's own credential, rotating if needed.

    Logging in with the home's own credential is what proves the server answering
    is the install we built. A stale Studio on the same port has a different
    password and fails here instead of quietly serving the wrong screenshots.

    Two credential states, because a home is reused across runs:
      first run   auth/.bootstrap_password exists; log in with it and rotate. The
                  rotation is not optional -- until it happens every authenticated
                  route answers 403 "Password change required".
      later runs  Studio DELETES the bootstrap file once rotated, so the only
                  credential left is the one we set. Use it directly.
    """
    home = Path(home)
    boot_file = home / "auth" / ".bootstrap_password"
    if boot_file.exists():
        bootstrap = boot_file.read_text().strip()
        rotate = True
    else:
        bootstrap = new_password
        rotate = False
    try:
        tok = _post(f"{base_url}/api/auth/login",
                    {"username": "unsloth", "password": bootstrap})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()[:300]
        hint = ("ANOTHER Studio may hold this port and the one we launched never bound"
                if rotate else
                "this home was rotated by an earlier run under a DIFFERENT password; "
                "pass the same --password, or delete the home and reinstall")
        raise RuntimeError(
            f"login to {base_url} with {home}'s own credential failed "
            f"({exc.code}): {body}\n{hint}"
        ) from None
    if rotate:
        tok = _post(f"{base_url}/api/auth/change-password",
                    {"current_password": bootstrap, "new_password": new_password},
                    token=tok["access_token"])
    return Session(base_url=base_url, home=home, password=new_password,
                   access_token=tok["access_token"],
                   refresh_token=tok.get("refresh_token", ""))


def api_get(session: Session, path: str, timeout: int = 600) -> dict:
    """Authenticated GET, for the numeric half of the evidence.

    A screenshot shows a change; an API reading proves what the number IS. Scenes
    should report both, because a picture of a list cannot be diffed and a reviewer
    cannot count 63 rows by eye.
    """
    req = urllib.request.Request(
        f"{session.base_url}{path}",
        headers={"Authorization": f"Bearer {session.access_token}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def api_post(session: Session, path: str, payload: dict, timeout: int = 600) -> dict:
    """Authenticated POST, for endpoints that answer a query rather than mutate.

    Same purpose as :func:`api_get`: read the number the photographed server would put
    on screen. Deliberately NOT wrapped in a try -- a scene decides for itself whether a
    404 is the finding (an endpoint the PR adds) or a failure.
    """
    return _post(f"{session.base_url}{path}", payload, token=session.access_token,
                 timeout=timeout)


async def assert_showing(page, name: str, timeout_ms: int = 60_000) -> None:
    """Fail unless the page is actually displaying `name`.

    Guards the quietest scene bug there is: the click misses, the panel keeps its
    previous selection, and the screenshot is of a different model entirely. It
    looks like a valid screenshot, so nothing downstream catches it.
    """
    await page.get_by_role("heading", name=re.compile(re.escape(name))).first.wait_for(
        state="visible", timeout=timeout_ms
    )


async def open_menu(page, trigger, item, attempts: int = 6,
                    item_timeout_ms: int = 4_000) -> None:
    """Click `trigger` until `item` is actually on screen.

    Radix menus lose races with Studio's background refresh: the inventory reloads on a
    timer, and a reload landing between the click and the menu paint remounts the row and
    takes the open dropdown with it. A single attempt failed roughly half the time during
    the 8223 run.

    Worth a helper because of how the failure PRESENTS: a bare `click` timeout on the menu
    ITEM, which reads exactly like a bad selector and sends you rewriting a locator that
    was correct all along.
    """
    for attempt in range(attempts):
        await trigger.scroll_into_view_if_needed()
        await trigger.hover()
        await page.wait_for_timeout(400)
        await trigger.click()
        try:
            await item.wait_for(state="visible", timeout=item_timeout_ms)
            return
        except Exception:  # noqa: BLE001 -- a lost race, not a bad selector
            if attempt == attempts - 1:
                raise
            # Escape first: a half-open menu swallows the next click on the trigger.
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(1_000)


async def open_list(page, item_pattern: str, timeout_ms: int = 15_000) -> None:
    """Open the collapsed control whose CURRENT value matches `item_pattern`.

    Match on a value the list contains (a quant token, a resolution, a precision),
    never on a category word like "GGUF": category words also appear on toolbar
    filters, and opening the wrong menu yields a dropdown with identical contents
    on both sides -- evidence that the PR did nothing.
    """
    combo = page.locator("button").filter(has_text=re.compile(item_pattern)).first
    try:
        await combo.click(timeout=timeout_ms)
    except Exception:  # noqa: BLE001 -- already-open is not a failure
        pass
