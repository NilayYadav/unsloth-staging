#!/usr/bin/env python3
import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ID = "overlap-repro-project"
PROJECT_NAME = "Overlap Repro Project"
URL = "https://example.com/"


class WD:
    def __init__(self, base):
        self.base, self.sid = base, None

    def req(self, method, path, body = None, timeout = 120):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data = data, method = method,
                                   headers = {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout = timeout) as resp:
                return json.loads(resp.read() or b"{}").get("value")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path}: {e.code} {e.read()[:400]!r}") from None

    def s(self, p):
        return f"/session/{self.sid}{p}"

    def js(self, script, *args):
        return self.req("POST", self.s("/execute/sync"), {"script": script, "args": list(args)})

    def js_async(self, script, *args):
        return self.req("POST", self.s("/execute/async"), {"script": script, "args": list(args)})

    def find(self, css):
        return self.req("POST", self.s("/elements"), {"using": "css selector", "value": css}) or []

    def click(self, el):
        self.req("POST", self.s(f"/element/{next(iter(el.values()))}/click"), {})

    def type(self, el, text):
        self.req("POST", self.s(f"/element/{next(iter(el.values()))}/value"), {"text": text})

    def shot(self, path):
        Path(path).write_bytes(base64.b64decode(self.req("GET", self.s("/screenshot"))))


class CDP:
    def __init__(self, port):
        import websocket

        targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout = 10))
        print("targets:", [(t.get("type"), t.get("url")) for t in targets])
        page = next(t for t in targets if t.get("type") == "page" and "tauri.localhost" in t.get("url", ""))
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout = 120)
        self.n, self.sid = 0, "cdp"

    def call(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def _eval(self, expr):
        r = self.call("Runtime.evaluate", expression = expr, returnByValue = True, awaitPromise = True)
        if "exceptionDetails" in r:
            raise RuntimeError(str(r["exceptionDetails"])[:400])
        return r.get("result", {}).get("value")

    def js(self, script, *args):
        return self._eval(f"(function(){{{script}}}).apply(null, {json.dumps(list(args))})")

    def js_async(self, script, *args):
        return self._eval(
            f"new Promise(done => (function(){{{script}}}).apply(null, [...{json.dumps(list(args))}, done]))")

    def find(self, css):
        n = self._eval(f"document.querySelectorAll({json.dumps(css)}).length") or 0
        return [{"css": css, "i": i} for i in range(n)]

    def click(self, el):
        self._eval(f"document.querySelectorAll({json.dumps(el['css'])})[{el['i']}].click()")

    def type(self, el, text):
        self._eval(f"document.querySelectorAll({json.dumps(el['css'])})[{el['i']}].focus()")
        enter = text.endswith("")
        self.call("Input.insertText", text = text.rstrip(""))
        if enter:
            for kind in ("keyDown", "keyUp"):
                self.call("Input.dispatchKeyEvent", type = kind, key = "Enter", code = "Enter",
                          windowsVirtualKeyCode = 13, nativeVirtualKeyCode = 13,
                          **({"text": "\r"} if kind == "keyDown" else {}))

    def shot(self, path):
        Path(path).write_bytes(base64.b64decode(self.call("Page.captureScreenshot", format = "png")["data"]))

    def req(self, *a, **k):
        return None

    def s(self, p):
        return p


def screen_shot(path):
    try:
        if sys.platform == "win32":
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
                "$i=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
                "$g=[System.Drawing.Graphics]::FromImage($i);"
                "$g.CopyFromScreen($b.Left,$b.Top,0,0,$i.Size);"
                f"$i.Save('{path}',[System.Drawing.Imaging.ImageFormat]::Png)"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], check = True, timeout = 60)
        else:
            subprocess.run(["import", "-window", "root", str(path)], check = True, timeout = 60)
    except Exception as e:
        print(f"screen capture failed for {path}: {e}")


def wait(fn, timeout, every = 2.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception as e:
            print(f"  wait: {e}")
        time.sleep(every)
    return None


def capture(wd, out, name, facts):
    time.sleep(8)
    try:
        wd.shot(out / f"{name}.webdriver.png")
    except Exception as e:
        print(f"webdriver screenshot failed: {e}")
    screen_shot(out / f"{name}.screen.png")
    facts[name] = wd.js("""
      const d = document.querySelector('[role=dialog][aria-modal=true]');
      const r = d && d.getBoundingClientRect();
      return {path: location.pathname + location.search,
              dialog: d ? {x: r.x, y: r.y, w: r.width, h: r.height} : null,
              projectTitleVisible: document.body.innerText.includes(arguments[0]),
              text: document.body.innerText.slice(0, 600)};""", PROJECT_NAME)
    print(name, json.dumps(facts[name], indent = 1))


def open_browser_and_load(wd):
    buttons = wd.find('button[aria-label="New tab"]')
    if not buttons:
        raise RuntimeError("no 'New tab' globe button")
    wd.click(buttons[0])
    field = wait(lambda: wd.find('input[aria-label="Search or enter a URL"]'), 30, 1)
    if not field:
        raise RuntimeError("browser address field never appeared")
    wd.type(field[0], URL + "")


def nav(wd, route):
    wd.js("history.pushState({}, '', arguments[0]); dispatchEvent(new PopStateEvent('popstate'));", route)
    time.sleep(4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required = True)
    ap.add_argument("--out", required = True)
    ap.add_argument("--cdp", action = "store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents = True, exist_ok = True)

    port = 4444 if not a.cdp else 9222
    log = (out / "app.log").open("wb")
    if a.cdp:
        env = dict(os.environ, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f"--remote-debugging-port={port}")
        driver = subprocess.Popen([str(Path(a.bin).resolve())], env = env, stdout = log, stderr = subprocess.STDOUT)
    else:
        driver = subprocess.Popen(["tauri-driver", "--port", str(port)], stdout = log, stderr = subprocess.STDOUT)
    facts = {"platform": sys.platform}
    wd = WD(f"http://127.0.0.1:{port}")
    try:
        if not wait(lambda: socket.create_connection(("127.0.0.1", port), 1).close() or True, 180, 2):
            raise RuntimeError(f"nothing listening on {port}")
        if a.cdp:
            wd = wait(lambda: CDP(port), 120, 3)
            if not wd:
                raise RuntimeError("no main-window DevTools target")
        else:
            caps = {"capabilities": {"alwaysMatch": {"tauri:options": {"application": str(Path(a.bin).resolve())}}}}
            wd.sid = wd.req("POST", "/session", caps, timeout = 300)["sessionId"]

        def ready():
            p = wd.js("return location.pathname")
            print(f"  at {p}")
            return p.startswith("/chat") or p.startswith("/login") or p.startswith("/change-password")

        if not wait(ready, 1500, 10):
            raise RuntimeError("app never reached the Studio UI")
        path = wd.js("return location.pathname")
        if not path.startswith("/chat"):
            wd.shot(out / "stuck.webdriver.png")
            raise RuntimeError(f"app landed on {path}, not /chat")
        time.sleep(5)
        for _ in range(3):
            skip = [b for b in wd.find("button") if (wd.js("return arguments[0].innerText", b) if not isinstance(wd, CDP) else wd._eval(f"document.querySelectorAll('button')[{b['i']}].innerText")) .strip() == "Skip setup"]
            if not skip:
                break
            wd.click(skip[0])
            time.sleep(3)
        native = wd.js_async("""const done = arguments[arguments.length - 1];
          window.__TAURI_INTERNALS__.invoke('browser_view_supported').then(v => done({ok: v}), e => done({err: String(e)}));""")
        facts["native_supported_probe"] = native

        api = wd.js(r"""const m = performance.getEntriesByType('resource').map(e => e.name)
            .map(n => /^(https?:\/\/127\.0\.0\.1:[1-9]\d*)\//.exec(n)).find(Boolean);
            return m ? m[1] : null;""")
        if not api:
            api = "http://127.0.0.1:8888"
        facts["api"] = api
        now = int(time.time() * 1000)
        created = wd.js_async("""const [api, body, done] = arguments;
          fetch(api + '/api/chat/projects', {method: 'POST', headers: {'Content-Type': 'application/json',
            Authorization: 'Bearer ' + localStorage.getItem('unsloth_auth_token')}, body: JSON.stringify(body)})
          .then(r => r.text().then(t => done({status: r.status, body: t.slice(0, 300)})), e => done({err: String(e)}));""",
            api, {"id": PROJECT_ID, "name": PROJECT_NAME, "createdAt": now, "updatedAt": now})
        facts["project_create"] = created
        print("project create:", created)

        nav(wd, f"/chat?project={PROJECT_ID}")
        if not wait(lambda: PROJECT_NAME in wd.js("return document.body.innerText"), 60):
            wd.js("location.reload()")
            time.sleep(10)
            nav(wd, f"/chat?project={PROJECT_ID}")
        capture(wd, out, "1_project_page", facts)

        open_browser_and_load(wd)
        capture(wd, out, "2_project_page_browser_example", facts)
        time.sleep(10)
        capture(wd, out, "3_project_page_browser_example_later", facts)

        for btn in wd.find('button[aria-label="Close"]'):
            try:
                wd.click(btn)
                break
            except Exception:
                pass
        nav(wd, "/chat?new=overlap-control")
        try:
            open_browser_and_load(wd)
        except RuntimeError as e:
            facts["control_note"] = str(e)
        capture(wd, out, "4_control_new_chat_browser_example", facts)
        rc = 0
    except Exception as e:
        print(f"FAILED: {e}")
        facts["error"] = str(e)
        try:
            wd.shot(out / "failure.webdriver.png")
        except Exception:
            pass
        screen_shot(out / "failure.screen.png")
        rc = 1
    finally:
        (out / "facts.json").write_text(json.dumps(facts, indent = 1))
        try:
            if wd.sid:
                wd.req("DELETE", wd.s(""), timeout = 20)
        except Exception:
            pass
        driver.terminate()
    sys.exit(rc)


if __name__ == "__main__":
    main()
