import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

SIDE = os.environ["PROBE_SIDE"]
HOME = Path(os.environ["UNSLOTH_STUDIO_HOME"]).resolve()
ART = Path(os.environ["STUDIO_ARTIFACT_DIR"]).resolve()
ART.mkdir(parents = True, exist_ok = True)
MODEL = "unsloth/SmolLM2-135M-Instruct-GGUF"
PROMPT = "The capital of France is"
BASE = {"prompt": PROMPT, "max_tokens": 12, "temperature": 0, "seed": 7}

CASES = [
    ("baseline", {}, None),
    ("echo_true", {"echo": True}, "echo"),
    ("suffix_text", {"suffix": " and that is the end."}, "suffix"),
    ("best_of_3", {"best_of": 3}, "best_of"),
    ("best_of_3_n_2", {"best_of": 3, "n": 2}, "best_of"),
    ("best_of_2_stream", {"best_of": 2, "stream": True}, "best_of"),
    ("benign_defaults", {"echo": False, "suffix": "", "best_of": 1}, None),
    ("benign_nulls", {"echo": None, "suffix": None, "best_of": None}, None),
    ("benign_best_of_eq_n", {"best_of": 2, "n": 2}, None),
]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(cmd, log):
    fh = open(log, "w")
    print("Launching:", " ".join(cmd), flush = True)
    return subprocess.Popen(cmd, stdout = fh, stderr = subprocess.STDOUT, start_new_session = True)


def stop(p):
    if p and p.poll() is None:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            p.wait(15)
        except Exception:
            p.kill()


def wait_health(url, timeout = 900):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url, timeout = 3).status_code < 500:
                return
        except Exception:
            pass
        time.sleep(2)
    sys.exit(f"FAIL: {url} not healthy")


def post(url, body, headers):
    if body.get("stream"):
        with httpx.stream("POST", url, json = body, headers = headers, timeout = 180) as r:
            raw = r.read().decode("utf-8", "ignore")
            status = r.status_code
        if status != 200:
            try:
                return {"status": status, "body": json.loads(raw)}
            except Exception:
                return {"status": status, "body": raw[:500]}
        idx = set()
        text = ""
        for line in raw.splitlines():
            if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                ev = json.loads(line[6:])
                for c in ev.get("choices") or []:
                    idx.add(c.get("index"))
                    text += c.get("text") or ""
        return {"status": status, "choices": len(idx), "text": text}
    r = httpx.post(url, json = body, headers = headers, timeout = 180)
    try:
        j = r.json()
    except Exception:
        return {"status": r.status_code, "body": r.text[:500]}
    if r.status_code != 200:
        return {"status": r.status_code, "body": j}
    ch = j.get("choices") or []
    return {"status": 200, "choices": len(ch), "text": ch[0].get("text") if ch else None}


def run_cases(url, headers):
    out = {}
    for name, extra, _ in CASES:
        out[name] = post(url, {**BASE, **extra}, headers)
        print(f"[{SIDE}] {url.split('/v1')[0]} {name}: {json.dumps(out[name])[:300]}", flush = True)
    return out


def err_param(res):
    b = res.get("body")
    if isinstance(b, dict):
        e = b.get("error") or (b.get("detail") or {}).get("error") or {}
        return e.get("code"), e.get("param")
    return None, None


studio = llama = None
failures = []
try:
    port = free_port()
    unsloth = next(p for p in [HOME / "bin" / "unsloth", *HOME.glob(".venv*/bin/unsloth"), *HOME.glob("unsloth_studio/bin/unsloth")] if p.is_file())
    studio_log = ART / "studio-run.log"
    studio = start([str(unsloth), "studio", "run", "--model", MODEL, "--gguf-variant", "Q8_0", "--port", str(port),
                    "--host", "127.0.0.1", "--api-key-name", "ci", "--max-seq-length", "2048", "--disable-tools"], studio_log)
    key = None
    end = time.time() + 1200
    while time.time() < end and not key:
        m = re.search(r"API Key:\s+(sk-unsloth-[a-f0-9]+)", studio_log.read_text(errors = "ignore"))
        key = m.group(1) if m else None
        time.sleep(2)
    if not key:
        sys.exit("FAIL: no Studio API key")
    wait_health(f"http://127.0.0.1:{port}/api/health")
    studio_res = run_cases(f"http://127.0.0.1:{port}/v1/completions", {"Authorization": f"Bearer {key}"})

    server = next(p for p in HOME.rglob("llama-server") if p.is_file() and os.access(p, os.X_OK))
    gguf = next(p for root in (HOME, Path.home() / ".cache") for p in root.rglob("SmolLM2-135M-Instruct-Q8_0.gguf"))
    lport = free_port()
    llama = start([str(server), "-m", str(gguf), "--port", str(lport), "--host", "127.0.0.1", "-c", "2048"], ART / "llama-direct.log")
    wait_health(f"http://127.0.0.1:{lport}/health", 300)
    direct_res = run_cases(f"http://127.0.0.1:{lport}/v1/completions", {})

    (ART / f"results-{SIDE}.json").write_text(json.dumps({"studio": studio_res, "llama_direct": direct_res}, indent = 2))

    base_text = direct_res["baseline"]["text"]
    d = direct_res
    premise = {
        "direct echo_true 200 w/o prompt": d["echo_true"]["status"] == 200 and not (d["echo_true"]["text"] or "").startswith(PROMPT),
        "direct suffix same as baseline": d["suffix_text"]["status"] == 200 and d["suffix_text"]["text"] == base_text,
        "direct best_of_3 one choice": d["best_of_3"]["status"] == 200 and d["best_of_3"]["choices"] == 1,
    }
    for k, v in premise.items():
        print(("PASS " if v else "FAIL ") + "premise: " + k, flush = True)
        if not v:
            failures.append("premise: " + k)

    for name, _, param in CASES:
        r = studio_res[name]
        if param:
            ok = r["status"] == 400 and err_param(r) == ("unsupported_parameter", param)
            label = f"studio {name} -> 400 unsupported_parameter param={param}"
        else:
            ok = r["status"] == 200 and r.get("choices", 0) >= 1
            label = f"studio {name} -> 200 proxied"
        print(("PASS " if ok else "FAIL ") + label + f" (got {r['status']} {err_param(r)})", flush = True)
        if not ok:
            failures.append(label)
finally:
    stop(studio)
    stop(llama)

if failures:
    sys.exit(f"FAIL [{SIDE}]: " + "; ".join(failures))
print(f"PASS [{SIDE}] all assertions", flush = True)
