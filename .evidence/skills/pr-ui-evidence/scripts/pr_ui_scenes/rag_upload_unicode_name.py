"""Scene: the project Sources tab, after four documents are uploaded by name.

The change under review is the one line that decides what an uploaded document is
CALLED. Nothing about it is frontend: the name the panel prints is the one the upload
route wrote into the document row, so the surface is the Sources list and the
measurement is the set of names the server hands back.

Four names, chosen so the shot reads without a caption: two Chinese ones that the
old allowlist collapses onto the SAME string, one with an ordinary space, and one
with accents. The two Chinese rows are the point -- a list with two rows that say the
same thing is a user who can no longer tell their own documents apart.

No model is loaded. The panel fetches /api/rag/projects/<id>/documents and prints
doc.filename, so the picture and the numbers come from the same server response.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

RUN = uuid.uuid4().hex[:8]
PROJECT = f"pr10667p{RUN}"
CREATED_AT = 1_755_000_000_000

# The two Chinese names differ in every character. Under the old allowlist both come
# out as the same string, which is what makes the collision visible rather than merely
# ugly.
DOCS = {
    "报告.txt": "Quarterly report. Revenue rose in every region except one.",
    "会议记录.txt": "Meeting minutes. The board approved the hiring plan for the autumn.",
    "My Report.txt": "A report with an ordinary space in its name and nothing else unusual.",
    "Café Résumé.txt": "A document whose name carries accents that are not decoration.",
}


def _upload(session: Session, project_id: str, name: str, text: str) -> dict:
    """The browser's own request shape: RFC 2388 multipart with a UTF-8 filename."""
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: text/plain\r\n\r\n{text}\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    req = urllib.request.Request(
        f"{session.base_url}/api/rag/projects/{project_id}/documents",
        data = body,
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {session.access_token}",
        },
        method = "POST",
    )
    with urllib.request.urlopen(req, timeout = 900) as r:
        return json.loads(r.read())


def _seed(session: Session) -> tuple[list[dict], dict[str, str]]:
    api_post(session, "/api/chat/projects", {
        "id": PROJECT, "name": "PR 10667 uploads", "instructions": "",
        "archived": False, "createdAt": CREATED_AT, "updatedAt": CREATED_AT,
    })
    # The upload response names the document it just created, so each row can be tied
    # back to the name it was uploaded under even when two rows come out identical.
    returned = {name: _upload(session, PROJECT, name, text)["filename"]
                for name, text in DOCS.items()}
    # Settled, not necessarily ingested: the name is written by the upload route, so a
    # failed embedder still prints the row. Waiting keeps the two shots comparable.
    deadline = time.time() + 900
    while time.time() < deadline:
        docs = api_get(session, f"/api/rag/projects/{PROJECT}/documents")["documents"]
        if len(docs) == len(DOCS) and all(
            d.get("status") in ("completed", "failed") for d in docs
        ):
            return docs, returned
        time.sleep(3)
    return api_get(session, f"/api/rag/projects/{PROJECT}/documents")["documents"], returned


async def drive(
    session: Session,
    out_dir: Path,
    label: str,
    **_: object,
) -> tuple[list[Path], dict]:
    documents, returned = _seed(session)
    server_names = [d.get("filename") for d in documents]

    auth = type("A", (), {"access_token": session.access_token,
                          "refresh_token": session.refresh_token})()
    async with open_chat(
        session.base_url, init_scripts = [seed_init_script(auth, [])],
        viewport = (1280, 900), headless = True,
    ) as sp:
        page = sp.page
        await page.goto(f"{session.base_url}/chat?project={PROJECT}",
                        wait_until = "domcontentloaded", timeout = 60_000)
        sources_tab = page.get_by_role("button", name = "Sources", exact = True)
        await sources_tab.wait_for(state = "visible", timeout = 60_000)
        await sources_tab.click()
        # The rows arrive with the panel's own fetch, not with the tab: without this the
        # first shot is the "Loading sources…" placeholder on both sides.
        for _attempt in range(60):
            body = await page.locator("main").inner_text()
            if sum(body.count(n) for n in server_names if n) >= len(documents):
                break
            await page.wait_for_timeout(1000)
        await page.wait_for_timeout(1000)
        panel_text = await page.locator("main").inner_text()
        shot = out_dir / f"{label.lower()}_project_sources_names.png"
        await page.screenshot(path = str(shot))

    ui_rows = [line.strip() for line in panel_text.splitlines() if line.strip()]
    uploaded = list(DOCS)
    return [shot], {
        "uploaded_names": uploaded,
        "server_names": sorted(n for n in server_names if n),
        "distinct_server_names": len({n for n in server_names if n}),
        "documents_uploaded": len(uploaded),
        "names_survived_verbatim": all(returned.get(n) == n for n in DOCS),
        "two_chinese_names_collide": (
            returned.get("报告.txt") == returned.get("会议记录.txt")
        ),
        "name_for_报告.txt": returned.get("报告.txt"),
        "name_for_会议记录.txt": returned.get("会议记录.txt"),
        "name_for_My Report.txt": returned.get("My Report.txt"),
        "name_for_Café Résumé.txt": returned.get("Café Résumé.txt"),
        "underscored_names": sorted(n for n in server_names if n and "_" in n),
        "ui_row_lines": [ln for ln in ui_rows if ".txt" in ln],
        "document_statuses": sorted({d.get("status") for d in documents}),
    }
