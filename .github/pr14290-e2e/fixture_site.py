import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

LOG = sys.argv[1]
LOCK = threading.Lock()

REPORT = """
<script>
function report(ev, extra) {
  const body = JSON.stringify(Object.assign({ev, href: location.href, t: Date.now()}, extra || {}));
  try { if (navigator.sendBeacon('/log', body)) return; } catch (e) {}
  fetch('/log', {method: 'POST', body, keepalive: true}).catch(() => {});
}
function rects() {
  const out = {};
  for (const el of document.querySelectorAll('[data-target]')) {
    const r = el.getBoundingClientRect();
    out[el.id] = {x: r.left, y: r.top, w: r.width, h: r.height};
  }
  return out;
}
</script>
"""

MAIN = """<!doctype html><html><head><meta charset="utf-8"><title>E2E local page</title>
<style>body{margin:0;font:16px sans-serif;background:#fffbe6}
.t{position:absolute;left:16px;width:280px;height:56px;display:flex;align-items:center;justify-content:center;
background:#0b5;color:#fff;border:0;font:bold 16px sans-serif;text-decoration:none}
#h{position:absolute;left:16px;top:0;margin:0;font-size:14px;height:24px;line-height:24px}</style></head>
<body><p id="h">E2E-LOCAL-7860 main</p>
<a id="pub" data-target class="t" style="top:30px" href="https://example.com/">public link</a>
<a id="loc" data-target class="t" style="top:100px" href="http://127.0.0.1:7861/">other local link</a>
<button id="pop" data-target class="t" style="top:170px" onclick="window.open('/x')">window.open /x</button>
""" + REPORT + """
<script>
for (const id of ['pub', 'loc', 'pop']) {
  document.getElementById(id).addEventListener('click', (e) => report('click', {id, trusted: e.isTrusted}), true);
}
addEventListener('load', () => {
  report('loaded', {page: 'main', rects: rects(), dpr: devicePixelRatio, iw: innerWidth, ih: innerHeight,
    sx: screenX, sy: screenY});
  const auto = new URLSearchParams(location.search).get('auto');
  if (auto) setTimeout(() => document.getElementById(auto).click(), 1500);
});
</script></body></html>"""

FRAME = """<!doctype html><html><head><meta charset="utf-8"><title>E2E frame page</title></head>
<body style="margin:0;font:16px sans-serif"><p>E2E-LOCAL-7860 frame</p>
<iframe id="f" src="https://example.com/" width="600" height="300"></iframe>""" + REPORT + """
<script>addEventListener('load', () => report('loaded', {page: 'frame'}));
document.getElementById('f').addEventListener('load', () => report('iframe-load', {page: 'frame'}));</script>
</body></html>"""

X = """<!doctype html><html><head><title>E2E popup target</title></head><body><p>E2E-LOCAL-7860 popup x</p>
""" + REPORT + """<script>addEventListener('load', () => report('loaded', {page: 'x'}));</script></body></html>"""

OTHER = """<!doctype html><html><head><title>E2E other local</title></head><body><p>E2E-LOCAL-7861 other</p>
""" + REPORT + """<script>addEventListener('load', () => report('loaded', {page: 'other'}));</script></body></html>"""


def write(entry):
    with LOCK, open(LOG, "a", encoding = "utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def handler_for(port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def page(self, html):
            raw = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            parsed = urlparse(self.path)
            write({"t": time.time(), "port": port, "kind": "request", "method": "GET", "path": self.path,
                   "ua": self.headers.get("User-Agent", "")})
            if parsed.path == "/favicon.ico":
                self.send_response(404)
                self.end_headers()
                return
            if port == 7861:
                return self.page(OTHER)
            if parsed.path == "/frame":
                return self.page(FRAME)
            if parsed.path == "/x":
                return self.page(X)
            if parsed.path == "/log":
                q = parse_qs(parsed.query)
                write({"t": time.time(), "port": port, "kind": "event", "data": {k: v[0] for k, v in q.items()}})
                self.send_response(204)
                self.end_headers()
                return
            return self.page(MAIN)

        def do_POST(self):
            length = int(self.headers.get("content-length", "0") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                data = {"raw": raw.decode("utf-8", "replace")}
            write({"t": time.time(), "port": port, "kind": "event", "data": data})
            self.send_response(204)
            self.end_headers()

    return Handler


def main():
    servers = [ThreadingHTTPServer(("127.0.0.1", port), handler_for(port)) for port in (7860, 7861)]
    for server in servers[1:]:
        threading.Thread(target = server.serve_forever, daemon = True).start()
    print("fixture site on 7860, 7861", flush = True)
    servers[0].serve_forever()


if __name__ == "__main__":
    main()
