import argparse
import base64
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
WIN = sys.platform == "win32"

T_STUDIO = "This is Unsloth's own address, so it can't open in the browser panel."
T_BLOCKED_LOCAL = "This local address can't open here. Type it in the address bar to open it."
ADDRESS = 'input[aria-label="Search or enter a URL"]'


def log(*parts):
    print("[e2e]", *parts, flush = True)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Driver:
    def __init__(self, base):
        self.base = base
        self.sid = None

    def req(self, method, path, payload = None, timeout = 30):
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.base + path, data = body, method = method,
                                         headers = {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout = timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise RuntimeError(f"WebDriver {method} {path} -> {error.code}: {detail[:800]}") from error

    def s(self, method, path, payload = None, timeout = 30):
        return self.req(method, f"/session/{self.sid}{path}", payload, timeout)

    def js(self, script, *args):
        return self.s("POST", "/execute/sync", {"script": script, "args": list(args)}).get("value")

    def wait(self, script, what, timeout = 60, interval = 0.25):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                last = self.js(script)
                if last:
                    return last
            except Exception as error:
                last = f"error: {error}"
            time.sleep(interval)
        raise AssertionError(f"timed out waiting for {what}; last={last!r}")

    def find(self, css):
        value = self.s("POST", "/element", {"using": "css selector", "value": css})["value"]
        return next(iter(value.values()))

    def screenshot(self, path):
        try:
            data = self.s("GET", "/screenshot", timeout = 20)["value"]
            Path(path).write_bytes(base64.b64decode(data))
        except Exception as error:
            log("screenshot failed", path, error)


def os_screenshot(path):
    try:
        if WIN:
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
                "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
                "$g=[System.Drawing.Graphics]::FromImage($bmp);"
                "$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size);"
                f"$bmp.Save('{path}',[System.Drawing.Imaging.ImageFormat]::Png)"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout = 30, check = False)
        elif shutil.which("import"):
            subprocess.run(["import", "-window", "root", str(path)], timeout = 30, check = False)
    except Exception as error:
        log("os screenshot failed", error)


WIN_CLICK = r"""
Add-Type @"
using System; using System.Runtime.InteropServices;
public class E2EUser32 {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, IntPtr e);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
}
"@
$p = Get-Process -Name unsloth-studio -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { Write-Output "NOWINDOW"; exit 0 }
$h = $p.MainWindowHandle
$pt = New-Object E2EUser32+POINT
[void][E2EUser32]::ClientToScreen($h, [ref]$pt)
if ($env:E2E_MODE -eq "origin") { Write-Output ("ORIGIN {0} {1}" -f $pt.X, $pt.Y); exit 0 }
[void][E2EUser32]::SetForegroundWindow($h)
Start-Sleep -Milliseconds 200
$x = $pt.X + [int]$env:E2E_X; $y = $pt.Y + [int]$env:E2E_Y
[void][E2EUser32]::SetCursorPos($x, $y)
Start-Sleep -Milliseconds 100
[E2EUser32]::mouse_event(2, 0, 0, 0, [IntPtr]::Zero)
Start-Sleep -Milliseconds 60
[E2EUser32]::mouse_event(4, 0, 0, 0, [IntPtr]::Zero)
Write-Output ("CLICKED {0} {1}" -f $x, $y)
"""


def window_origin():
    if WIN:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", WIN_CLICK], capture_output = True, text = True,
                             timeout = 60, env = {**os.environ, "E2E_MODE": "origin"}).stdout
        log("win origin:", out.strip())
        for line in out.splitlines():
            if line.startswith("ORIGIN"):
                _, x, y = line.split()
                return int(x), int(y)
        return None
    if not shutil.which("xdotool"):
        return None
    ids = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", "Unsloth"], capture_output = True,
                         text = True, timeout = 20).stdout.split()
    log("x windows:", ids)
    best = None
    for wid in ids:
        geo = subprocess.run(["xdotool", "getwindowgeometry", "--shell", wid], capture_output = True, text = True,
                             timeout = 20).stdout
        vals = dict(line.split("=", 1) for line in geo.splitlines() if "=" in line)
        area = int(vals.get("WIDTH", 0)) * int(vals.get("HEIGHT", 0))
        log("x geometry", wid, vals)
        if best is None or area > best[0]:
            best = (area, int(vals.get("X", 0)), int(vals.get("Y", 0)), wid)
    return (best[1], best[2]) if best else None


def os_click(x, y):
    if WIN:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", WIN_CLICK], capture_output = True, text = True,
                             timeout = 60, env = {**os.environ, "E2E_MODE": "click", "E2E_X": str(x), "E2E_Y": str(y)})
        log("win click:", out.stdout.strip(), out.stderr.strip()[:400])
        return "CLICKED" in out.stdout
    if not shutil.which("xdotool"):
        return False
    origin = window_origin()
    if not origin:
        return False
    ax, ay = origin[0] + x, origin[1] + y
    out = subprocess.run(["xdotool", "mousemove", str(ax), str(ay), "click", "1"], capture_output = True, text = True,
                         timeout = 20)
    log("xdotool click", ax, ay, out.returncode, out.stderr.strip()[:200])
    return out.returncode == 0


class Site:
    def __init__(self, path):
        self.path = Path(path)

    def entries(self):
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding = "utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        return out

    def events(self, since = 0.0):
        return [e["data"] for e in self.entries() if e.get("kind") == "event" and e.get("t", 0) >= since]

    def requests(self, since = 0.0):
        return [e for e in self.entries() if e.get("kind") == "request" and e.get("t", 0) >= since]

    def wait_event(self, pred, timeout, since):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.events(since):
                if pred(event):
                    return event
            time.sleep(0.25)
        return None


TOAST_RECORDER = r"""
if (!window.__e2eToasts) {
  window.__e2eToasts = [];
  const seen = new WeakMap();
  const scan = () => {
    for (const el of document.querySelectorAll('[data-sonner-toast]')) {
      const text = (el.innerText || '').trim();
      if (text && seen.get(el) !== text) {
        seen.set(el, text); window.__e2eToasts.push({t: Date.now(), text});
        // Hold sonner's dismiss timer (it pauses while the list is hovered) so Open can still be pressed.
        el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
      }
    }
  };
  new MutationObserver(scan).observe(document.body, {subtree: true, childList: true, characterData: true});
  scan();
}
return true;
"""

TABS = r"""
const tabs = [...document.querySelectorAll('[role="tab"][data-tab-id]')];
return tabs.map((el) => ({id: el.getAttribute('data-tab-id'), selected: el.getAttribute('aria-selected') === 'true',
  text: (el.innerText || '').trim()}));
"""


class Run:
    def __init__(self, d, site, art, arm):
        self.d = d
        self.site = site
        self.art = Path(art)
        self.arm = arm
        self.results = {}
        self.notes = []
        self.handles_seen = []
        self.page_rects = None

    def shot(self, name):
        self.d.screenshot(self.art / f"{name}-main.png")
        os_screenshot(self.art / f"{name}-screen.png")

    def body(self):
        return self.d.js("return document.body ? document.body.innerText : ''") or ""

    def tabs(self):
        return self.d.js(TABS) or []

    def toasts(self, since_ms = 0):
        return [t for t in (self.d.js("return window.__e2eToasts || []") or []) if t["t"] >= since_ms]

    def now_ms(self):
        return self.d.js("return Date.now()")

    def address(self):
        return self.d.js(f"const i=document.querySelector('{ADDRESS}'); return i ? i.value : null")

    def type_address(self, text):
        for attempt in range(2):
            try:
                el = self.d.find(ADDRESS)
                self.d.s("POST", f"/element/{el}/click", {})
                time.sleep(0.2)
                self.d.s("POST", f"/element/{el}/clear", {})
                self.d.s("POST", f"/element/{el}/value", {"text": text + ""})
                return "webdriver-keys"
            except Exception as error:
                log("type via webdriver failed", attempt, error)
                time.sleep(1)
        self.d.js(
            "const i=document.querySelector(arguments[0]); i.focus();"
            "const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
            "set.call(i, arguments[1]); i.dispatchEvent(new Event('input',{bubbles:true}));"
            "i.form.requestSubmit(); return true;", ADDRESS, text)
        return "react-setter"

    def select_tab(self, tab_id):
        self.d.js("const e=document.querySelector(`[role=\"tab\"][data-tab-id=\"${arguments[0]}\"]`); if(e){e.click(); return true;} return false;", tab_id)
        self.d.wait(f"return document.querySelector('[role=\"tab\"][data-tab-id=\"{tab_id}\"]')?.getAttribute('aria-selected')==='true'",
                    f"tab {tab_id} selected", timeout = 10)
        time.sleep(1.5)

    def close_other_tabs(self, keep):
        for tab in self.tabs():
            if tab["id"] != keep:
                self.d.js(
                    "const e=document.querySelector(`[role=\"tab\"][data-tab-id=\"${arguments[0]}\"]`);"
                    "const b=e && [...e.querySelectorAll('button')].find((x)=>(x.getAttribute('aria-label')||'')==='Close tab');"
                    "if(b){b.click(); return true;} return false;", tab["id"])
                time.sleep(0.4)
        self.select_tab(keep)

    def placeholder_rect(self):
        return self.d.js(
            "const els=[...document.querySelectorAll('[data-native-page]')];"
            "const vis=els.map((e)=>({id:e.getAttribute('data-native-page'), r:e.getBoundingClientRect()})).filter((x)=>x.r.width>0&&x.r.height>0);"
            "const x=vis[0]; return x ? {id:x.id, x:x.r.left, y:x.r.top, w:x.r.width, h:x.r.height, dpr:devicePixelRatio, n:els.length} : null;")

    def probe_handles(self, label):
        info = {"label": label}
        try:
            current = self.d.s("GET", "/window")["value"]
            handles = self.d.s("GET", "/window/handles")["value"]
            info["current"] = current
            info["handles"] = handles
            pages = {}
            for handle in handles:
                if handle == current:
                    continue
                try:
                    self.d.s("POST", "/window", {"handle": handle})
                    pages[handle] = self.d.js("return location.href")
                except Exception as error:
                    pages[handle] = f"error: {error}"
            self.d.s("POST", "/window", {"handle": current})
            info["other_pages"] = pages
        except Exception as error:
            info["error"] = str(error)
        log("window handles", json.dumps(info))
        self.handles_seen.append(info)
        return info

    def native_handle(self):
        info = self.probe_handles("find-native")
        for handle, href in (info.get("other_pages") or {}).items():
            if isinstance(href, str) and href.startswith("http://localhost:7860"):
                return info["current"], handle
        return None

    def click_in_page(self, target, scenario):
        since = time.time()
        attempts = []
        native = self.native_handle()
        if native:
            main, handle = native
            try:
                self.d.s("POST", "/window", {"handle": handle})
                el = self.d.find(f"#{target}")
                self.d.s("POST", f"/element/{el}/click", {})
                attempts.append("webdriver-native-handle")
            except Exception as error:
                attempts.append(f"webdriver-native-handle failed: {error}")
            finally:
                self.d.s("POST", "/window", {"handle": main})
            ev = self.site.wait_event(lambda e: e.get("ev") == "click" and e.get("id") == target, 5, since)
            if ev:
                return {"method": "webdriver-native-handle", "trusted": ev.get("trusted"), "attempts": attempts}
        rect = self.placeholder_rect()
        page = self.page_rects or {}
        if rect and target in page:
            dpr = rect.get("dpr") or 1
            tx = page[target]["x"] + page[target]["w"] / 2
            ty = page[target]["y"] + page[target]["h"] / 2
            x = int(round((rect["x"] + tx) * dpr))
            y = int(round((rect["y"] + ty) * dpr))
            ok = os_click(x, y)
            attempts.append(f"os-click {x},{y} rect={rect} ok={ok}")
            ev = self.site.wait_event(lambda e: e.get("ev") == "click" and e.get("id") == target, 5, since)
            if ev:
                return {"method": "os-trusted-click", "trusted": ev.get("trusted"), "attempts": attempts}
        else:
            attempts.append(f"os-click skipped rect={rect} page={bool(page)}")
        since = time.time()
        how = self.type_address(f"localhost:7860/?auto={target}")
        attempts.append(f"scripted auto-click via address bar ({how})")
        ev = self.site.wait_event(lambda e: e.get("ev") == "click" and e.get("id") == target, 15, since)
        if ev:
            return {"method": "page-scripted-click", "trusted": ev.get("trusted"), "attempts": attempts}
        return {"method": None, "attempts": attempts}

    def record(self, key, ok, detail, expected = "pass"):
        self.results[key] = {"ok": bool(ok), "expected": expected, **detail}
        log(f"{key}: {'OK' if ok else 'FAIL'} (expected {expected}) {json.dumps(detail)[:1500]}")

    def wait_toast(self, needle, since_ms, timeout = 6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for toast in self.toasts(since_ms):
                if needle in toast["text"]:
                    return toast
            time.sleep(0.25)
        return None

    def click_toast_open(self):
        return self.d.js(
            "for (const el of document.querySelectorAll('[data-sonner-toast]')) {"
            " const b=[...el.querySelectorAll('button')].find((x)=>(x.innerText||'').trim()==='Open');"
            " if (b) { b.click(); return (el.innerText||'').trim(); } } return false;")

    def dismiss_toasts(self):
        self.d.js("for (const el of document.querySelectorAll('[data-sonner-toast] [data-close-button]')) el.click(); return true;")
        time.sleep(1.2)


def cdp_ready(port, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout = 2) as response:
                return json.load(response)
        except Exception:
            time.sleep(0.5)
    return None


def cdp_targets(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout = 5) as response:
            return json.load(response)
    except Exception as error:
        return f"error: {error}"


def win_diag(path):
    ps = (
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'msedgewebview2|unsloth-studio' } | "
        "ForEach-Object { '{0} {1} parent={2}: {3}' -f $_.ProcessId, $_.Name, $_.ParentProcessId, $_.CommandLine } ; "
        "'--- listening'; Get-NetTCPConnection -State Listen | Sort-Object LocalPort | ForEach-Object { '{0}:{1} pid={2}' -f $_.LocalAddress, $_.LocalPort, $_.OwningProcess } ; "
        "'--- env'; [Environment]::GetEnvironmentVariable('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', 'Machine'); [Environment]::GetEnvironmentVariable('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', 'User'); "
        "'--- policy'; Get-ItemProperty 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge\\WebView2' -ErrorAction SilentlyContinue | Out-String; "
        "Get-ItemProperty 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge' -ErrorAction SilentlyContinue | Out-String; "
        "'--- data'; Get-ChildItem -Recurse -Filter DevToolsActivePort $env:LOCALAPPDATA -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output = True, text = True, timeout = 120)
        Path(path).write_text(out.stdout + "\n" + out.stderr, encoding = "utf-8")
        log("win diag:\n" + out.stdout[:6000])
    except Exception as error:
        log("win diag failed", error)


def win_resize(width, height):
    ps = (
        'Add-Type @"\nusing System; using System.Runtime.InteropServices;\n'
        'public class E2EMove { [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int hh, bool r); }\n"@\n'
        "$p = Get-Process -Name unsloth-studio -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1;"
        f"if ($p) {{ [E2EMove]::MoveWindow($p.MainWindowHandle, 0, 0, {width}, {height}, $true) }} else {{ 'NOWINDOW' }}"
    )
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output = True, text = True, timeout = 60)
    log("win resize:", out.stdout.strip(), out.stderr.strip()[:300])


def boot(d, app, attach = None):
    if attach:
        caps = {"browserName": "webview2", "ms:edgeOptions": {"debuggerAddress": f"127.0.0.1:{attach}"}}
    else:
        caps = {"browserName": "wry", "tauri:options": {"application": str(app)}}
    created = d.req("POST", "/session", {"capabilities": {"alwaysMatch": caps}}, timeout = 120)
    d.sid = str(created["value"]["sessionId"])
    log("session", d.sid, json.dumps(created["value"].get("capabilities", {}))[:600])
    d.wait("return document.readyState === 'complete' && !!document.body", "main document", timeout = 60)
    d.wait("const t=(document.body&&document.body.innerText)||''; return t.length > 20 && !['Starting Unsloth', 'Loading projects', 'Loading models', 'Nearly done', 'Finishing update'].some((m)=>t.includes(m))",
           "the backend handoff", timeout = 150)
    if attach:
        win_resize(1400, 900)
        time.sleep(1.5)
        log("inner size", d.js("return [innerWidth, innerHeight, devicePixelRatio]"))
        return
    for payload in ({"width": 1400, "height": 900, "x": 0, "y": 0},):
        try:
            d.s("POST", "/window/rect", payload)
        except Exception as error:
            log("window rect failed", error)
            try:
                d.s("POST", "/window/maximize", {})
            except Exception as error2:
                log("maximize failed", error2)


def open_browser(r):
    d = r.d
    path = d.js("return location.pathname")
    log("landed on", path)
    if not str(path).startswith("/chat"):
        d.js("history.pushState({}, '', '/chat'); dispatchEvent(new PopStateEvent('popstate')); return true;")
        d.wait("return location.pathname.startsWith('/chat')", "/chat route", timeout = 30)
    d.wait("return !!document.querySelector('button[aria-label=\"New tab\"]')", "the chat header New tab button", timeout = 60)
    d.js("document.querySelector('button[aria-label=\"New tab\"]').click(); return true;")
    d.wait(f"return !!document.querySelector('{ADDRESS}')", "the browser panel address bar", timeout = 30)
    d.js(TOAST_RECORDER)
    native = d.js("return !!window.__TAURI_INTERNALS__")
    log("tauri internals present:", native)
    time.sleep(1)


def scenarios(r):
    d, site = r.d, r.site
    tabs = r.tabs()
    log("tabs after open", tabs)
    tab_a = next((t["id"] for t in tabs if t["selected"]), tabs[0]["id"] if tabs else None)

    # 2: Studio's own port
    t0 = r.now_ms()
    how = r.type_address("localhost:8888")
    try:
        d.wait(f"return (document.body.innerText||'').includes({json.dumps(T_STUDIO)})", "studioAddress text", timeout = 20)
        ok = True
    except AssertionError:
        ok = False
    r.shot("s2-studio-port")
    r.record("s2_studio_port_message", ok, {"typed": "localhost:8888", "input": how,
                                            "body_excerpt": r.body()[:600] if not ok else T_STUDIO})

    # 1: typed localhost:7860 opens with Local badge
    since = time.time()
    how = r.type_address("localhost:7860")
    loaded = site.wait_event(lambda e: e.get("ev") == "loaded" and e.get("page") == "main", 30, since)
    badge = None
    try:
        badge = d.wait("const t=document.querySelector('[role=\"tab\"][aria-selected=\"true\"]');"
                       "return t && [...t.querySelectorAll('span')].some((s)=>(s.innerText||'').trim()==='Local') ? (t.innerText||'').trim() : false;",
                       "Local badge on the active tab", timeout = 20)
    except AssertionError as error:
        badge = None
        r.notes.append(str(error))
    if loaded:
        r.page_rects = loaded.get("rects")
    time.sleep(1.5)
    r.shot("s1-localhost-7860")
    reqs = [q["path"] for q in site.requests(since)]
    r.record("s1_typed_localhost_opens", bool(loaded) and bool(badge), {
        "input": how, "page_loaded_event": loaded, "active_tab": badge, "address": r.address(),
        "site_requests": reqs[:10]})
    if not loaded:
        r.notes.append("main page never loaded in the native view; in-page scenarios cannot run")
        return
    tab_a = next((t["id"] for t in r.tabs() if t["selected"]), tab_a)
    r.probe_handles("after-s1")
    r.dismiss_toasts()

    expect_feature = r.arm == "head"

    # 3 and 4: links a local page cannot follow
    for key, target, url, expect_text in (
        ("s3_public_link_toast", "pub", "https://example.com/", None),
        ("s4_other_local_link_toast", "loc", "http://127.0.0.1:7861/", T_BLOCKED_LOCAL),
    ):
        r.close_other_tabs(tab_a)
        if not (r.address() or "").startswith("http://localhost:7860"):
            r.type_address("localhost:7860")
            site.wait_event(lambda e: e.get("ev") == "loaded" and e.get("page") == "main", 20, time.time() - 1)
            time.sleep(1)
        before_tabs = r.tabs()
        t0 = r.now_ms()
        since = time.time()
        click = r.click_in_page(target, key)
        toast = r.wait_toast(f"wants to open {url}", t0, timeout = 6)
        if not toast:
            time.sleep(1)
        after_tabs = r.tabs()
        addr = r.address()
        stayed = (addr or "").startswith("http://localhost:7860") and len(after_tabs) == len(before_tabs)
        navigated_away = [q for q in site.requests(since) if q["port"] == 7861] if target == "loc" else []
        r.shot(f"{key}-toast")
        detail = {"click": click, "toast": toast, "address_after": addr, "tabs_before": len(before_tabs),
                  "tabs_after": len(after_tabs), "all_toasts": r.toasts(t0), "requests_to_7861": len(navigated_away)}
        opened = None
        if toast:
            open_text = False
            for _ in range(12):
                open_text = r.click_toast_open()
                if open_text:
                    break
                time.sleep(0.25)
            time.sleep(4)
            opened_tabs = r.tabs()
            new = [t for t in opened_tabs if t["id"] not in {b["id"] for b in after_tabs}]
            body = r.body()
            opened = {"clicked_open": open_text, "new_tabs": new, "address": r.address()}
            if expect_text:
                opened["blockedLocal_shown"] = expect_text in body
            r.shot(f"{key}-after-open")
            detail["after_open"] = opened
        if expect_feature:
            ok = (click.get("method") is not None and toast is not None and stayed and opened is not None
                  and len(opened["new_tabs"]) == 1
                  and (url.split("//")[1].split("/")[0] in (opened["address"] or ""))
                  and (expect_text is None or opened.get("blockedLocal_shown")))
            r.record(key, ok, detail)
        else:
            ok = click.get("method") is not None and toast is None and len(after_tabs) == len(before_tabs)
            r.record(key, ok, detail, expected = "no toast, no tab (baseline)")
        r.dismiss_toasts()

    # 6: window.open('/x')
    r.close_other_tabs(tab_a)
    if not (r.address() or "").startswith("http://localhost:7860"):
        r.type_address("localhost:7860")
        site.wait_event(lambda e: e.get("ev") == "loaded" and e.get("page") == "main", 20, time.time() - 1)
        time.sleep(1)
    before_tabs = r.tabs()
    t0 = r.now_ms()
    since = time.time()
    click = r.click_in_page("pop", "s6")
    new = []
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        new = [t for t in r.tabs() if t["id"] not in {b["id"] for b in before_tabs}]
        if new:
            break
        time.sleep(0.25)
    time.sleep(3)
    body = r.body()
    x_loaded = site.wait_event(lambda e: e.get("ev") == "loaded" and e.get("page") == "x", 1, since)
    r.shot("s6-window-open")
    detail = {"click": click, "new_tabs": new, "address": r.address(), "blockedLocal_shown": T_BLOCKED_LOCAL in body,
              "x_page_loaded_in_a_view": bool(x_loaded), "toasts": r.toasts(t0)}
    if expect_feature:
        ok = click.get("method") is not None and len(new) == 1 and T_BLOCKED_LOCAL in body and "localhost:7860/x" in (r.address() or "")
        r.record("s6_window_open_new_tab_blocked_local", ok, detail)
    else:
        ok = click.get("method") is not None and len(new) == 0
        r.record("s6_window_open_new_tab_blocked_local", ok, detail, expected = "no tab (baseline)")
    r.dismiss_toasts()

    # 5: public iframe
    r.close_other_tabs(tab_a)
    before_tabs = r.tabs()
    t0 = r.now_ms()
    since = time.time()
    r.type_address("localhost:7860/frame")
    loaded = site.wait_event(lambda e: e.get("ev") == "loaded" and e.get("page") == "frame", 30, since)
    time.sleep(8)
    toasts = [t for t in r.toasts(t0) if "wants to open" in t["text"]]
    after_tabs = r.tabs()
    r.shot("s5-iframe")
    detail = {"frame_page_loaded": bool(loaded), "prompt_toasts": toasts, "tabs_before": len(before_tabs),
              "tabs_after": len(after_tabs), "address": r.address(),
              "iframe_load_event": site.wait_event(lambda e: e.get("ev") == "iframe-load", 0.5, since)}
    ok = bool(loaded) and len(toasts) <= 1 and len(after_tabs) == len(before_tabs)
    r.record("s5_public_iframe_at_most_one_toast", ok, detail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required = True)
    ap.add_argument("--native-driver", required = True)
    ap.add_argument("--art", required = True)
    ap.add_argument("--arm", default = "head")
    ap.add_argument("--windows-mode", choices = ["attach", "tauri-driver"], default = "attach")
    args = ap.parse_args()
    art = Path(args.art).resolve()
    art.mkdir(parents = True, exist_ok = True)
    site_log = art / "site-events.jsonl"
    site_proc = subprocess.Popen([sys.executable, str(HERE / "fixture_site.py"), str(site_log)],
                                 stdout = open(art / "fixture-site.log", "wb"), stderr = subprocess.STDOUT)
    time.sleep(1)
    tauri_driver = shutil.which("tauri-driver")
    port, native_port = free_port(), free_port()
    drv_log = open(art / "tauri-driver.log", "wb")
    kwargs = {"start_new_session": True} if not WIN else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    attach = None
    app_proc = None
    if WIN and args.windows_mode == "attach":
        # msedgedriver's own launch (what tauri-driver asks for) never finds DevToolsActivePort for
        # this app, so start the app with a fixed WebView2 debugging port and attach to it instead.
        attach = 9222
        env = {**os.environ, "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={attach}"}
        app_proc = subprocess.Popen([str(args.app)], env = env, stdout = open(art / "app-stdout.log", "wb"),
                                    stderr = subprocess.STDOUT, creationflags = subprocess.CREATE_NEW_PROCESS_GROUP)
        version = cdp_ready(attach, 45)
        log("cdp version", json.dumps(version)[:400])
        win_diag(art / "webview2-procs.txt")
        if not version:
            version = cdp_ready(attach, 60)
            log("cdp version (late)", json.dumps(version)[:400])
        (art / "cdp-targets-boot.json").write_text(json.dumps(cdp_targets(attach), indent = 2), encoding = "utf-8")
        drv = subprocess.Popen([args.native_driver, f"--port={port}", "--verbose", f"--log-path={art / 'msedgedriver.log'}"],
                               stdout = drv_log, stderr = subprocess.STDOUT, **kwargs)
    else:
        drv = subprocess.Popen([tauri_driver, "--port", str(port), "--native-port", str(native_port),
                                "--native-driver", args.native_driver], stdout = drv_log, stderr = subprocess.STDOUT, **kwargs)
    d = Driver(f"http://127.0.0.1:{port}")
    for _ in range(120):
        try:
            d.req("GET", "/status", timeout = 1)
            break
        except Exception:
            time.sleep(0.25)
    r = Run(d, Site(site_log), art, args.arm)
    fatal = None
    try:
        boot(d, args.app, attach)
        r.shot("00-booted")
        open_browser(r)
        r.shot("01-panel-open")
        scenarios(r)
    except Exception as error:
        fatal = "".join(traceback.format_exception(error))
        log("FATAL", fatal)
        if d.sid:
            r.shot("zz-failure")
            try:
                snap = d.js("return {url: location.href, body: (document.body && document.body.innerText) || '', html: (document.body && document.body.innerHTML.slice(0, 200000)) || ''}")
                (art / "failure-dom.json").write_text(json.dumps(snap, indent = 2), encoding = "utf-8")
            except Exception as error2:
                log("snapshot failed", error2)
    finally:
        try:
            if d.sid:
                (art / "final-body.txt").write_text(r.body(), encoding = "utf-8")
                (art / "toasts.json").write_text(json.dumps(r.toasts(0), indent = 2), encoding = "utf-8")
        except Exception:
            pass
        try:
            if d.sid:
                d.req("DELETE", f"/session/{d.sid}", timeout = 15)
        except Exception:
            pass
        if attach:
            (art / "cdp-targets-end.json").write_text(json.dumps(cdp_targets(attach), indent = 2), encoding = "utf-8")
        for proc in [p for p in (drv, site_proc, app_proc) if p]:
            try:
                if WIN:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output = True)
                else:
                    os.killpg(proc.pid, signal.SIGTERM) if proc is drv else proc.terminate()
            except Exception:
                pass
        drv_log.close()
    summary = {"arm": args.arm, "os": sys.platform, "fatal": fatal, "results": r.results, "notes": r.notes,
               "window_handles": r.handles_seen, "page_rects": r.page_rects}
    (art / "results.json").write_text(json.dumps(summary, indent = 2), encoding = "utf-8")
    lines = [f"### PR 14290 desktop e2e: {sys.platform} / {args.arm}", "", "| scenario | result | expected | method |", "|---|---|---|---|"]
    for key, value in r.results.items():
        method = (value.get("click") or {}).get("method", "") if isinstance(value.get("click"), dict) else ""
        lines.append(f"| {key} | {'PASS' if value['ok'] else 'FAIL'} | {value['expected']} | {method} |")
    if fatal:
        lines += ["", "```", fatal[-3000:], "```"]
    md = "\n".join(lines) + "\n"
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding = "utf-8") as handle:
            handle.write(md)
    expected_keys = {"s1_typed_localhost_opens", "s2_studio_port_message", "s3_public_link_toast",
                     "s4_other_local_link_toast", "s5_public_iframe_at_most_one_toast", "s6_window_open_new_tab_blocked_local"}
    ok = not fatal and expected_keys <= set(r.results) and all(v["ok"] for v in r.results.values())
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
