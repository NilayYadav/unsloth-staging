"""PR 9940 A/B repro: does a Claude Code session stay inside the served window?

Runs a real `claude` session against a stub endpoint that enforces the model's
real window (like the local llama.cpp server does) and reports usage the same way.
The launch environment comes from the checked-out tree's own _claude_local_env, so
the only variable between arms is the PR implementation.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATE = {
    "server_window": 65536,
    "requests": 0,
    "compactions": 0,
    "max_prompt_tokens": 0,
    "overflows": [],
    "prompt_tokens": [],
}
LOCK = threading.Lock()
COMPACT_MARKER = "Your task is to create a detailed summary"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        self._json({"data": []})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace") if n else "{}"
        try:
            body = json.loads(raw)
        except Exception:
            body = {}
        tokens = max(1, len(raw) // 4)
        is_compact = COMPACT_MARKER in raw
        with LOCK:
            STATE["requests"] += 1
            STATE["prompt_tokens"].append(tokens)
            STATE["max_prompt_tokens"] = max(STATE["max_prompt_tokens"], tokens)
            if is_compact:
                STATE["compactions"] += 1
            over = tokens > STATE["server_window"]
            if over:
                STATE["overflows"].append(tokens)
        if self.path.rstrip("/").endswith("count_tokens"):
            return self._json({"input_tokens": tokens})
        if over:
            return self._error(tokens)
        text = "Summary of the conversation so far: the user sent filler text." if is_compact else "ok"
        if body.get("stream"):
            return self._stream(body, tokens, text)
        return self._json(self._message(body, tokens, text))

    def _error(self, tokens):
        payload = {"type": "error", "error": {
            "type": "invalid_request_error",
            "message": f"the request exceeds the available context size: {tokens} > {STATE['server_window']}"}}
        raw = json.dumps(payload).encode()
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _message(self, body, tokens, text):
        return {"id": "msg_stub", "type": "message", "role": "assistant",
                "model": body.get("model", "stub"),
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": tokens, "output_tokens": 4,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}

    def _json(self, payload):
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _stream(self, body, tokens, text):
        events = [
            ("message_start", {"type": "message_start", "message": {
                "id": "msg_stub", "type": "message", "role": "assistant",
                "model": body.get("model", "stub"), "content": [],
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": tokens, "output_tokens": 1,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": text}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta",
                               "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": 4}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for name, data in events:
            chunk = f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()
            self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--turns", type=int, default=12)
    ap.add_argument("--server-window", type=int, default=65536)
    ap.add_argument("--filler-chars", type=int, default=24000)
    ap.add_argument("--model", default="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:Q4_K_XL")
    ap.add_argument("--claude", default="claude")
    ap.add_argument("--out", required=True)
    ap.add_argument("--extra-env", action="append", default=[])
    ap.add_argument("--drop-env", action="append", default=[])
    ap.add_argument("--plain-env", action="store_true")
    args = ap.parse_args()

    STATE["server_window"] = args.server_window
    sys.path.insert(0, str(Path(args.repo).resolve()))
    import unsloth_cli.commands.start as start

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    base = f"http://127.0.0.1:{port}"
    entry = {"id": args.model, "context_length": args.server_window}
    if args.plain_env:
        launch_env = {"ANTHROPIC_BASE_URL": base, "ANTHROPIC_AUTH_TOKEN": "sk-unsloth-stub",
                      "ANTHROPIC_MODEL": args.model}
    else:
        launch_env = start._claude_local_env(base, "sk-unsloth-stub", entry)

    home = Path(tempfile.mkdtemp(prefix=f"claude-{args.arm}-"))
    work = home / "work"
    work.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}
    env.update(launch_env)
    env.update({"HOME": str(home), "USERPROFILE": str(home),
                "CLAUDE_CONFIG_DIR": str(home / ".claude"), "ANTHROPIC_API_KEY": ""})
    for pair in args.extra_env:
        k, _, v = pair.partition("=")
        env[k] = v
    for name in args.drop_env:
        env.pop(name, None)

    filler = ("lorem ipsum dolor sit amet consectetur " * (args.filler_chars // 39 + 1))[:args.filler_chars]
    turns = []
    first_failure = None
    for turn in range(1, args.turns + 1):
        cmd = [args.claude, "--print", "--output-format", "json", "--model", args.model]
        if turn > 1:
            cmd.append("--continue")
        cmd.append(f"turn {turn}. Reply with ok. Reference material: {filler}")
        proc = subprocess.run(cmd, env=env, cwd=str(work), capture_output=True,
                              text=True, timeout=600)
        record = {"turn": turn, "returncode": proc.returncode}
        try:
            payload = json.loads(proc.stdout)
            record["is_error"] = payload.get("is_error")
            usage = payload.get("modelUsage") or {}
            for model_id, entry_usage in usage.items():
                record["context_window"] = entry_usage.get("contextWindow")
                record["input_tokens"] = entry_usage.get("inputTokens")
            if payload.get("is_error"):
                record["result"] = str(payload.get("result"))[:400]
        except Exception:
            record["stdout_tail"] = proc.stdout[-400:]
            record["stderr_tail"] = proc.stderr[-400:]
        with LOCK:
            record["compactions_so_far"] = STATE["compactions"]
            record["requests_so_far"] = STATE["requests"]
            record["prompt_tokens_seen"] = list(STATE["prompt_tokens"])
            record["max_prompt_tokens"] = STATE["max_prompt_tokens"]
        failed = proc.returncode != 0 or record.get("is_error")
        record["ok"] = not failed
        turns.append(record)
        if failed and first_failure is None:
            first_failure = turn
            break

    shutil.rmtree(home, ignore_errors=True)
    report = {
        "arm": args.arm,
        "repo": str(Path(args.repo).resolve()),
        "model": args.model,
        "server_window": args.server_window,
        "env_max_context_tokens": launch_env.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS"),
        "env_auto_compact_window": launch_env.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"),
        "env_autocompact_pct": launch_env.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"),
        "extra_env": args.extra_env,
        "drop_env": args.drop_env,
        "turns_requested": args.turns,
        "turns_completed": sum(1 for t in turns if t["ok"]),
        "first_failed_turn": first_failure,
        "compactions": STATE["compactions"],
        "max_prompt_tokens": STATE["max_prompt_tokens"],
        "overflow_prompt_tokens": STATE["overflows"],
        "resolved_context_window": next((t.get("context_window") for t in turns
                                         if t.get("context_window")), None),
        "turn_details": turns,
    }
    Path(args.out).write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "turn_details"}, indent=2))


if __name__ == "__main__":
    main()
