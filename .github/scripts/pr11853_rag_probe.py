"""PR 11853 A/B probe: which document does Studio retrieve with Qwen3-Embedding on llama-server?

Same probe on both branches; only the implementation under test differs. Drives a real
Studio: sets the embedding model through the Settings API, uploads six project documents,
reads dense /api/rag/search rankings for six paraphrased questions, then answers one real
chat turn in the project and photographs the Document Sources row.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

PORT = os.environ["STUDIO_PORT"]
BASE = f"http://127.0.0.1:{PORT}"
SIDE = os.environ["SIDE"]
OUT = Path(os.environ.get("OUT_DIR", "pr11853-out")).resolve()
OUT.mkdir(parents = True, exist_ok = True)
STUDIO_HOME = Path(os.environ.get("UNSLOTH_STUDIO_HOME", Path.home() / ".unsloth" / "studio"))

EMBED_MODEL = "Qwen/Qwen3-Embedding-0.6B"
CHAT_MODEL = "unsloth/Qwen3-0.6B-GGUF"
CHAT_VARIANT = "Q4_K_M"

DOCS = {
    "photosynthesis.txt": "Chlorophyll inside leaves captures sunlight and turns water and carbon dioxide into sugar, releasing oxygen as a by-product.",
    "volcanoes.txt": "Magma rising through cracks in the crust erupts as lava, ash and gas once pressure beneath a mountain builds up.",
    "bread.txt": "Yeast feeds on sugars in dough and gives off carbon dioxide, which makes a loaf swell before it goes into the oven.",
    "tides.txt": "Gravity from the moon tugs on the oceans, so coastlines see sea level climb and fall roughly twice between sunrises.",
    "vaccines.txt": "Immunisation shows the body a harmless fragment of a germ so white blood cells remember it and defeat a genuine infection later.",
    "bitcoin.txt": "Miners compete to append blocks to a shared ledger, and the network rewards whoever wins with newly minted coins.",
}
QUESTIONS = [
    ("How do plants make food from light?", "photosynthesis.txt"),
    ("What causes molten rock to burst out of a peak?", "volcanoes.txt"),
    ("Why does dough get bigger when it rests?", "bread.txt"),
    ("Why does the beach flood and drain each day?", "tides.txt"),
    ("How does a jab protect people from disease?", "vaccines.txt"),
    ("How are new cryptocurrency tokens created?", "bitcoin.txt"),
]
# No word (or porter stem) in common with any document, so the chat turn isolates the embedder.
UI_QUESTION = QUESTIONS[4]

RUN = uuid.uuid4().hex[:8]
PROJECT = f"pr11853p{RUN}"
THREAD = f"pr11853t{RUN}"
NOW_MS = 1_755_000_000_000

LOCAL_STORAGE = {
    "unsloth_chat_rag_top_k": "1",
    "unsloth_chat_rag_autoinject_min_score": "0",
    "unsloth_chat_tools_enabled": "false",
    "unsloth_chat_reasoning_enabled": "false",
}

token = ""


def call(method: str, path: str, payload = None, *, timeout = 600, body = None, ctype = None):
    headers = {}
    data = body
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if ctype:
        headers["Content-Type"] = ctype
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", data = data, headers = headers, method = method)
    try:
        with urllib.request.urlopen(req, timeout = timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path} -> {exc.code}: {exc.read().decode()[:500]}") from None
    return json.loads(raw) if raw else {}


def login() -> str:
    global token
    bootstrap = (STUDIO_HOME / "auth" / ".bootstrap_password").read_text().strip()
    tok = call("POST", "/api/auth/login", {"username": "unsloth", "password": bootstrap})
    token = tok["access_token"]
    tok = call(
        "POST",
        "/api/auth/change-password",
        {"current_password": bootstrap, "new_password": "Pr11853-" + secrets.token_urlsafe(12)},
    )
    token = tok["access_token"]
    return tok.get("refresh_token", "")


def upload(name: str, text: str) -> None:
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
    ).encode() + text.encode() + f"\r\n--{boundary}--\r\n".encode()
    call(
        "POST",
        f"/api/rag/projects/{PROJECT}/documents",
        body = body,
        ctype = f"multipart/form-data; boundary={boundary}",
        timeout = 1800,
    )


def await_ingest() -> list[dict]:
    deadline = time.time() + 1800
    docs: list[dict] = []
    while time.time() < deadline:
        docs = call("GET", f"/api/rag/projects/{PROJECT}/documents")["documents"]
        if len(docs) >= len(DOCS) and all(d.get("status") in ("completed", "failed") for d in docs):
            return docs
        time.sleep(3)
    raise RuntimeError(f"ingest never settled: {[(d.get('filename'), d.get('status')) for d in docs]}")


def _cmdlines() -> list[list[str]]:
    out = []
    for proc in Path("/proc").iterdir():
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        argv = [a.decode(errors = "replace") for a in argv if a]
        if "--embedding" in argv or "--embeddings" in argv or "--pooling" in argv:
            out.append(argv)
    return out


SEEN_EMBED_SERVERS: dict[str, str] = {}


def _watch_embed_servers() -> None:
    # The embedder is spawned and stopped by Studio on demand, so sample for the whole run.
    while True:
        for argv in _cmdlines():
            if "--embedding" in argv or "--embeddings" in argv:
                pooling = argv[argv.index("--pooling") + 1] if "--pooling" in argv else "<none>"
                SEEN_EMBED_SERVERS.setdefault(" ".join(argv), pooling)
        time.sleep(0.5)


def search(query: str, mode: str) -> list[tuple[str, float]]:
    res = call(
        "POST",
        "/api/rag/search",
        {"project_id": PROJECT, "query": query, "top_k": len(DOCS), "mode": mode},
    )["results"]
    return [(r["filename"], round(float(r["score"]), 4)) for r in res]


def load_chat_model() -> dict:
    call(
        "POST",
        "/api/inference/load",
        {"model_path": CHAT_MODEL, "gguf_variant": CHAT_VARIANT, "max_seq_length": 4096},
        timeout = 1800,
    )
    deadline = time.time() + 1800
    while time.time() < deadline:
        st = call("GET", "/api/inference/status")
        if st.get("active_model") == CHAT_MODEL and not st.get("loading"):
            return st
        time.sleep(3)
    raise RuntimeError("chat model never became resident")


def chat_turn(refresh: str) -> dict:
    from playwright.sync_api import sync_playwright

    seed = {"unsloth_auth_token": token, "unsloth_refresh_token": refresh, **LOCAL_STORAGE}
    init = (
        "(() => { const s = " + json.dumps(seed) + ";"
        " for (const k of Object.keys(s)) { try { localStorage.setItem(k, s[k]); } catch (e) {} } })();"
    )
    facts: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport = {"width": 1280, "height": 860})
        ctx.add_init_script(init)
        page = ctx.new_page()
        page.goto(f"{BASE}/chat?thread={THREAD}", wait_until = "domcontentloaded", timeout = 90_000)
        box = page.locator("form:has(textarea) textarea").first
        box.wait_for(state = "visible", timeout = 90_000)
        page.wait_for_timeout(3_000)
        box.click()
        box.fill(UI_QUESTION[0])
        box.press("Enter")
        stop = page.locator('button[aria-label="Stop generating"]').first
        try:
            stop.wait_for(state = "visible", timeout = 60_000)
        except Exception:  # noqa: BLE001
            pass
        stop.wait_for(state = "hidden", timeout = 600_000)
        page.wait_for_timeout(3_000)
        row = page.locator('div:has(> div:text-is("Document Sources"))').last
        try:
            row.wait_for(state = "visible", timeout = 60_000)
            row.scroll_into_view_if_needed()
            row_text = row.inner_text()
        except Exception:  # noqa: BLE001
            row_text = ""
        page.wait_for_timeout(1_000)
        page.screenshot(path = str(OUT / f"{SIDE}_chat_sources.png"))
        facts["sources_row_text"] = " ".join(row_text.split())
        facts["cited_files"] = sorted({w for w in re.findall(r"[\w-]+\.txt", row_text)})
        ctx.close()
        browser.close()
    return facts


def main() -> int:
    facts: dict = {"side": SIDE, "sha": os.environ.get("GITHUB_SHA", "")}
    import threading

    threading.Thread(target = _watch_embed_servers, daemon = True).start()
    refresh = login()
    resolve_path = "/api/settings/embedding-model/resolve?" + urllib.parse.urlencode({"model": EMBED_MODEL})
    plan = call("GET", resolve_path)
    if not plan.get("cached") and plan.get("download_repo"):
        # What Settings' Download button does: fetch the planned files into Studio's own hub cache first.
        from huggingface_hub import hf_hub_download

        hub_cache = call("GET", "/api/settings/hugging-face-cache")["hub_cache"]
        for name in plan.get("files") or []:
            hf_hub_download(plan["download_repo"], name, cache_dir = hub_cache)
        plan = call("GET", resolve_path)
    facts["resolve"] = plan
    saved = call(
        "PUT",
        "/api/settings/embedding-model",
        {"embedding_model": EMBED_MODEL, "gguf_repo": plan.get("download_repo"), "backend": plan.get("backend")},
        timeout = 900,
    )
    facts["embedding_setting"] = {k: saved.get(k) for k in ("embedding_model", "embedding_gguf_repo")}

    call("POST", "/api/chat/projects", {
        "id": PROJECT, "name": "PR 11853 retrieval", "instructions": "",
        "archived": False, "createdAt": NOW_MS, "updatedAt": NOW_MS,
    })
    call("POST", "/api/chat/threads", {
        "id": THREAD, "title": "Science notes", "modelType": "base", "modelId": "",
        "projectId": PROJECT, "archived": False, "createdAt": NOW_MS, "updatedAt": NOW_MS,
    })
    for name, text in DOCS.items():
        upload(name, text)
    docs = await_ingest()
    facts["documents"] = sorted(f"{d['filename']}:{d.get('status')}" for d in docs)

    rows = []
    for q, want in QUESTIONS:
        dense = search(q, "dense")
        rows.append({
            "question": q,
            "expected": want,
            "dense_top1": dense[0][0] if dense else None,
            "dense_ranking": dense,
            "dense_score_spread": round(dense[0][1] - dense[-1][1], 4) if dense else None,
            "hybrid_top1": (search(q, "hybrid") or [(None, 0)])[0][0],
        })
    facts["questions"] = rows
    facts["llama_server_pooling"] = sorted(set(SEEN_EMBED_SERVERS.values()))
    facts["embed_server_cmdlines"] = sorted(SEEN_EMBED_SERVERS)
    facts["dense_top1_correct"] = sum(r["dense_top1"] == r["expected"] for r in rows)
    facts["hybrid_top1_correct"] = sum(r["hybrid_top1"] == r["expected"] for r in rows)

    status = load_chat_model()
    facts["chat_model"] = status.get("active_model")
    facts["chat"] = chat_turn(refresh)
    facts["chat"]["question"] = UI_QUESTION[0]
    facts["chat"]["expected"] = UI_QUESTION[1]
    facts["chat"]["cited_expected"] = UI_QUESTION[1] in facts["chat"]["cited_files"]

    (OUT / f"{SIDE}_facts.json").write_text(json.dumps(facts, indent = 2))
    print(json.dumps(facts, indent = 2))
    print(f"RESULT side={SIDE} pooling={facts['llama_server_pooling']} "
          f"dense_top1_correct={facts['dense_top1_correct']}/{len(QUESTIONS)} "
          f"hybrid_top1_correct={facts['hybrid_top1_correct']}/{len(QUESTIONS)} "
          f"chat_cited={facts['chat']['cited_files']}")
    if (
        facts["dense_top1_correct"] != len(QUESTIONS)
        or not facts["chat"]["cited_expected"]
        or facts["llama_server_pooling"] != ["last"]
    ):
        print("FAIL: Qwen3-Embedding was not served with last pooling, or a question or the chat missed its own document")
        return 1
    print("PASS: every question retrieved its own document, and the chat cited it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
