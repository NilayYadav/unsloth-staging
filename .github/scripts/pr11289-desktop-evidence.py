# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved.
"""Exercise the unmodified desktop binary against a delayed loopback fixture."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.options import ArgOptions


label, binary, source_sha = sys.argv[1:]
out = Path("evidence") / label
out.mkdir(parents=True, exist_ok=True)
home = (Path("homes") / label).resolve()
root_id = "a" * 64
studio = home / ".unsloth/studio"
(studio / "auth").mkdir(parents=True)
(studio / "share").mkdir()
(studio / "auth/.desktop_secret").write_text("fixture-only-desktop-secret")
(studio / "share/studio_install_id").write_text(root_id)
events = []
started = time.monotonic()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/health":
            self.reply(200, {"status": "healthy", "service": "Unsloth UI Backend",
                "desktop_protocol_version": 1, "desktop_manageability_version": 1,
                "supports_desktop_auth": True, "supports_desktop_backend_ownership": True,
                "native_path_leases_supported": True, "studio_root_id": root_id,
                "version": "2026.9.18"})
        elif self.path == "/api/auth/status":
            self.reply(200, {"initialized": True, "requires_password_change": False,
                "login_mode": "multi", "full_access": False})
        else:
            self.reply(404, {"detail": "fixture endpoint not implemented"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}")
        if self.path != "/api/auth/desktop-login":
            self.reply(404, {})
            return
        if body.get("secret") == "desktop-preflight-invalid-secret":
            self.reply(401, {})
            return
        assert body.get("secret") == "fixture-only-desktop-secret"
        event = {"request_seconds": round(time.monotonic() - started, 3), "port": 8888}
        events.append(event)
        time.sleep(7)
        self.reply(200, {"login_required": True, "login_mode": "multi"})
        event["response_seconds"] = round(time.monotonic() - started, 3)


server = ThreadingHTTPServer(("127.0.0.1", 8888), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
           XDG_CACHE_HOME=str(home / ".cache"), XDG_DATA_HOME=str(home / ".local/share"),
           WEBKIT_DISABLE_DMABUF_RENDERER="1")
env.pop("UNSLOTH_STUDIO_HOME", None)
driver_log = (out / "driver.log").open("w")
proc = subprocess.Popen(["tauri-driver"], env=env, stdout=driver_log, stderr=subprocess.STDOUT)
driver = None
facts = {"label": label, "source_sha": source_sha, "delay_seconds": 7,
         "backend": "controlled loopback fixture; real desktop binary and frontend",
         "expect": "before startup error; after visible account login without manual retry"}
try:
    import urllib.request
    for _ in range(100):
        try:
            urllib.request.urlopen("http://127.0.0.1:4444/status", timeout=1)
            break
        except Exception:
            time.sleep(.1)
    options = ArgOptions()
    options.set_capability("browserName", "wry")
    options.set_capability("tauri:options", {"application": str(Path(binary).resolve())})
    driver = webdriver.Remote("http://127.0.0.1:4444", options=options)
    driver.set_window_size(1280, 900)
    deadline = time.monotonic() + 65
    while time.monotonic() < deadline:
        text = driver.find_element(By.TAG_NAME, "body").text
        login = any(e.is_displayed() for e in driver.find_elements(By.CSS_SELECTOR, 'input[type="password"]'))
        error = "Something went wrong" in text
        if login or error:
            break
        time.sleep(.25)
    else:
        raise AssertionError("Desktop never reached either expected terminal UI state")
    time.sleep(1)
    driver.save_screenshot(str(out / "desktop.png"))
    facts.update(login_form_visible=login, startup_error_visible=error,
                 elapsed_seconds=round(time.monotonic() - started, 3),
                 auth_requests=len(events), events=events, visible_text=text,
                 viewport=driver.execute_script("return {width:innerWidth,height:innerHeight}"))
    (out / "meta.json").write_text(json.dumps(facts, indent=2))
    print(json.dumps(facts, indent=2), flush=True)
    assert events, "No real desktop auth request reached the delay fixture"
    assert login and not error, "REGRESSION: delayed desktop auth leaves startup error instead of login"
finally:
    if driver:
        if not (out / "desktop.png").exists():
            driver.save_screenshot(str(out / "desktop.png"))
        driver.quit()
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    server.shutdown()
    driver_log.close()
