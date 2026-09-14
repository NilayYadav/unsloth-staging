# Disposable PR 10936 probe: what Studio in Docker lists, loads and answers.
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
SIDE = os.environ["SIDE"]
PW = os.environ["STUDIO_PW"]
OUT = sys.argv[1]
SOURCES = ("lmstudio", "ollama", "hermes", "models_dir")
facts = {"side": SIDE}


def call(method, path, body = None, token = None, timeout = 60):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data = data, method = method, headers = headers)
    with urllib.request.urlopen(request, timeout = timeout) as response:
        return json.loads(response.read().decode() or "null")


def wait_healthy(limit = 1500):
    start = time.time()
    while time.time() - start < limit:
        try:
            if call("GET", "/api/health", timeout = 10).get("status") == "healthy":
                return round(time.time() - start)
        except Exception:
            pass
        time.sleep(5)
    raise SystemExit("FAIL: Studio never became healthy")


def inventory(token):
    body = call("GET", "/api/hub/local", token = token, timeout = 300)
    facts["dirs"] = {k: body.get(k) for k in ("models_dir", "lmstudio_dirs", "ollama_dirs", "hermes_dirs")}
    rows = {s: [] for s in SOURCES}
    for row in body.get("models", []):
        if row.get("source") in rows:
            rows[row["source"]].append(
                {k: row.get(k) for k in ("display_name", "model_id", "path", "load_id", "model_format", "size_bytes")}
            )
    facts["rows"] = rows
    facts["counts"] = {s: len(v) for s, v in rows.items()}
    return rows


def load_and_chat(token, row):
    started = time.time()
    request = urllib.request.Request(
        BASE + "/api/inference/load",
        data = json.dumps({"model_path": row["load_id"], "max_seq_length": 2048}).encode(),
        method = "POST",
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout = 1200) as response:
        load_body = response.read().decode().strip()
    status = call("GET", "/api/inference/status", token = token)
    reply = call(
        "POST",
        "/v1/chat/completions",
        {"messages": [{"role": "user", "content": "Say hello in five words."}], "max_tokens": 24, "temperature": 0},
        token = token,
        timeout = 600,
    )
    return {
        "load_id": row["load_id"],
        "load_response_tail": load_body[-300:],
        "loaded": status.get("loaded"),
        "active_model": status.get("active_model"),
        "reply": reply["choices"][0]["message"]["content"],
        "completion_tokens": (reply.get("usage") or {}).get("completion_tokens"),
        "seconds": round(time.time() - started, 1),
    }


def screenshots(tokens):
    from playwright.sync_api import sync_playwright

    shots = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport = {"width": 1366, "height": 900})
        page.add_init_script(
            "localStorage.setItem('unsloth_auth_token', %s);"
            "localStorage.setItem('unsloth_auth_refresh_token', %s);"
            "localStorage.setItem('unsloth_model_selector_section', 'downloaded');"
            % (json.dumps(tokens["access_token"]), json.dumps(tokens["refresh_token"]))
        )
        page.goto(BASE + "/chat", wait_until = "domcontentloaded")
        trigger = page.locator('button[data-tour="chat-model-selector"]')
        trigger.wait_for(timeout = 120000)
        page.wait_for_timeout(3000)
        trigger.click()
        popover = page.locator('[data-tour="chat-model-selector-popover"]')
        popover.wait_for(timeout = 30000)
        on_device = popover.get_by_text("On Device", exact = True)
        if on_device.count():
            on_device.first.click()
        page.wait_for_timeout(4000)
        search = popover.locator("input").first
        if search.count():
            search.fill("smol")
            page.wait_for_timeout(2500)
        shots["picker_text"] = popover.inner_text()
        popover.screenshot(path = f"evidence/{SIDE}_picker.png")
        page.screenshot(path = f"evidence/{SIDE}_picker_page.png")

        if SIDE == "after":
            row = popover.get_by_text("Q4_K_M").first
            row.click()
            page.wait_for_timeout(2000)
            composer = page.locator('textarea[aria-label="Message input"]')
            composer.wait_for(timeout = 300000)
            deadline = time.time() + 600
            while time.time() < deadline and "Select model" in trigger.inner_text():
                page.wait_for_timeout(2000)
            shots["selected_model"] = trigger.inner_text()
            before_len = len(page.inner_text("body"))
            composer.fill("Say hello in five words.")
            composer.press("Enter")
            stable, last = 0, before_len
            deadline = time.time() + 300
            while time.time() < deadline and stable < 4:
                page.wait_for_timeout(1500)
                now = len(page.inner_text("body"))
                stable = stable + 1 if now == last and now > before_len + 40 else 0
                last = now
            page.screenshot(path = f"evidence/{SIDE}_chat.png")
        browser.close()
    return shots


facts["healthy_after_s"] = wait_healthy()
tokens = call("POST", "/api/auth/login", {"username": "unsloth", "password": PW})
facts["must_change_password"] = tokens.get("must_change_password")
rows = inventory(tokens["access_token"])
print(json.dumps({"counts": facts["counts"], "dirs": facts["dirs"]}, indent = 2))

failures = []
if SIDE == "before":
    for source in SOURCES:
        if rows[source]:
            failures.append(f"before lists {source} rows, so the bug did not reproduce")
else:
    facts["chat"] = {}
    for source in SOURCES:
        if not rows[source]:
            failures.append(f"after lists no {source} rows")
            continue
        try:
            facts["chat"][source] = load_and_chat(tokens["access_token"], rows[source][0])
            if not facts["chat"][source]["reply"].strip():
                failures.append(f"{source} model loaded but replied with empty content")
        except (urllib.error.URLError, KeyError, ValueError) as exc:
            failures.append(f"{source} load/chat failed: {exc}")

try:
    facts["ui"] = screenshots(tokens)
except Exception as exc:
    facts["ui_error"] = repr(exc)

facts["failures"] = failures
with open(OUT, "w") as f:
    json.dump(facts, f, indent = 2)
print(json.dumps({k: v for k, v in facts.items() if k != "rows"}, indent = 2))
print("RESULT", "FAIL" if failures else "PASS", SIDE, failures)
sys.exit(1 if failures else 0)
