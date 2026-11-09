import base64, hashlib, json, os, subprocess, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port = int(sys.argv[1])
out_dir = sys.argv[2]
upstream = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else ""
model_id = os.environ.get("LOGGER_MODEL_ID", "e2e-video-logger")
os.makedirs(out_dir, exist_ok = True)
log_path = os.path.join(out_dir, "requests.jsonl")
lock = threading.Lock()
counter = [0]


def ffprobe(path):
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_name,width,height:format=format_name,duration,size",
                "-of",
                "compact",
                path,
            ],
            capture_output = True,
            text = True,
            timeout = 30,
        )
        return (r.stdout or r.stderr).strip()
    except Exception as e:
        return f"ffprobe unavailable: {e}"


def summarize(body, n):
    msgs = body.get("messages") or []
    summary = {
        "n": n,
        "model": body.get("model"),
        "stream": body.get("stream"),
        "top_level_keys": sorted(body.keys()),
        "messages": [],
    }
    for mi, m in enumerate(msgs):
        c = m.get("content")
        entry = {"role": m.get("role")}
        if isinstance(c, str):
            entry["content"] = "str:" + c[:200]
        elif isinstance(c, list):
            parts = []
            for pi, p in enumerate(c):
                t = p.get("type") if isinstance(p, dict) else type(p).__name__
                d = {"type": t}
                if t == "text":
                    d["text"] = p.get("text", "")[:200]
                elif t in ("video_url", "image_url", "input_video"):
                    v = p.get(t)
                    url = v.get("url") if isinstance(v, dict) else v
                    url = url or ""
                    head, _, b64 = url.partition(",")
                    d["prefix"] = head[:80]
                    d["b64_chars"] = len(b64)
                    try:
                        raw = base64.b64decode(b64)
                        d["bytes"] = len(raw)
                        d["sha256"] = hashlib.sha256(raw).hexdigest()[:16]
                        ext = ".mkv" if "matroska" in head else ".mp4" if "mp4" in head else ".bin"
                        fp = os.path.join(out_dir, f"req{n}_m{mi}_p{pi}{ext}")
                        open(fp, "wb").write(raw)
                        d["saved"] = os.path.basename(fp)
                        if t != "image_url":
                            d["ffprobe"] = ffprobe(fp)
                    except Exception as e:
                        d["decode_error"] = str(e)
                parts.append(d)
            entry["parts"] = parts
        summary["messages"].append(entry)
    return summary


def video_line(s):
    vids = [
        p
        for m in s["messages"]
        for p in m.get("parts", [])
        if p["type"] in ("video_url", "input_video")
    ]
    if not vids:
        return "no video part in the request"
    v = vids[-1]
    return f"video_url part: {v.get('prefix')} {v.get('bytes')} bytes"


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        sys.stdout.write("[logger] " + (fmt % a) + "\n")
        sys.stdout.flush()

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            if upstream:
                return self._proxy(b"", "GET")
            return self._json(
                200,
                {
                    "object": "list",
                    "data": [{"id": model_id, "object": "model", "owned_by": "e2e"}],
                },
            )
        self._json(200, {"ok": True})

    def _proxy(self, raw, method):
        url = (
            upstream.rstrip("/") + self.path[len("/v1") :]
            if self.path.startswith("/v1")
            else upstream.rstrip("/") + self.path
        )
        req = urllib.request.Request(
            url,
            data = raw if method == "POST" else None,
            method = method,
            headers = {"Content-Type": "application/json"},
        )
        try:
            resp = urllib.request.urlopen(req, timeout = 900)
            code, headers = resp.status, resp.headers
        except urllib.error.HTTPError as e:
            resp, code, headers = e, e.code, e.headers
        self.send_response(code)
        ctype = headers.get("Content-Type", "application/json")
        self.send_header("Content-Type", ctype)
        self.send_header("Connection", "close")
        self.end_headers()
        collected = bytearray()
        while True:
            chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
            if not chunk:
                break
            collected += chunk
            self.wfile.write(chunk)
            self.wfile.flush()
        return code, bytes(collected)

    def do_POST(self):
        n_raw = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n_raw)
        with lock:
            counter[0] += 1
            n = counter[0]
        try:
            body = json.loads(raw)
        except Exception:
            body = {}
        s = summarize(body, n)
        s["path"] = self.path
        s["request_bytes"] = n_raw
        print("[logger] REQUEST " + json.dumps(s), flush = True)
        if upstream:
            code, resp_body = self._proxy(raw, "POST")
            s["upstream_status"] = code
            s["upstream_response_tail"] = resp_body[-3000:].decode("utf-8", "replace")
            print(f"[logger] UPSTREAM {code} {s['upstream_response_tail'][-1500:]}", flush = True)
        else:
            text = f"LOGGER RECEIVED: {video_line(s)}. This is the e2e request logger, not a model."
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                base = {
                    "id": f"e2e-{n}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": body.get("model"),
                }
                for delta in ({"role": "assistant", "content": ""}, {"content": text}):
                    self.wfile.write(
                        b"data: "
                        + json.dumps(
                            {
                                **base,
                                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                            }
                        ).encode()
                        + b"\n\n"
                    )
                self.wfile.write(
                    b"data: "
                    + json.dumps(
                        {
                            **base,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                            "usage": {
                                "prompt_tokens": 1,
                                "completion_tokens": 1,
                                "total_tokens": 2,
                            },
                        }
                    ).encode()
                    + b"\n\n"
                )
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            else:
                self._json(
                    200,
                    {
                        "id": f"e2e-{n}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": body.get("model"),
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": text},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    },
                )
        with lock:
            with open(log_path, "a") as f:
                f.write(json.dumps(s) + "\n")


ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
