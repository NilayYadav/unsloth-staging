"""PR 11046: Notion MCP tool-call accuracy, full listing (BEFORE) vs compact listing (AFTER).

    modal run modal_mcp_accuracy.py --models 4b --tasks search_created_range,query_rows_checkbox
    modal run modal_mcp_accuracy.py --models 4b,30b --tasks all

Both sides run the same install, GPU, model, prompts and recording MCP server; only the
checked-out backend tree differs. Results land in ./out/run_<side>_<model>.json.
"""

import json
import os
import time
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
INSTALL_REF = "38d4a4869ad3173ba7cad99682f5c8726e72f14d"
BEFORE_REF = os.environ.get("BEFORE_REF", "38d4a4869ad3173ba7cad99682f5c8726e72f14d")
AFTER_REF = os.environ.get("AFTER_REF", "be846ee102051eb833ae3770a0d40a3e8b3e05e9")
PR = 11046
GPU = os.environ.get("EVAL_GPU", "A100-80GB")
MODELS = {
    "4b": "unsloth/Qwen3-4B-Instruct-2507-GGUF",
    "30b": "unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF",
}

image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python = "3.12")
    .apt_install(
        "git", "curl", "wget", "rsync", "ca-certificates", "build-essential",
        "cmake", "libcurl4-openssl-dev", "xz-utils", "unzip",
        "ffmpeg", "libgl1", "libglib2.0-0",
    )
    .run_commands(
        "git init /root/unsloth",
        "cd /root/unsloth && git remote add origin https://github.com/unslothai/unsloth.git",
        f"cd /root/unsloth && git fetch --depth 1 origin {INSTALL_REF} && git checkout --detach FETCH_HEAD",
        "cd /root/unsloth && bash install.sh --local",
        gpu = GPU,
    )
    .run_commands("python -m pip install httpx jsonschema")
    .env({"UNSLOTH_STUDIO_DISABLE_PUBLIC_CHECK": "1", "TOKENIZERS_PARALLELISM": "false"})
    .add_local_file(HERE / "recording_server.py", "/eval/recording_server.py")
    .add_local_file(HERE / "notion_tools.json", "/eval/notion_tools.json")
    .add_local_file(HERE / "tasks.json", "/eval/tasks.json")
)
app = modal.App(f"pr{PR}-mcp-accuracy")
hf_cache = modal.Volume.from_name("unsloth-studio-hf-cache", create_if_missing = True)


@app.function(image = image, gpu = GPU, timeout = 4 * 60 * 60, max_containers = 2,
              volumes = {"/root/.cache/huggingface": hf_cache})
def evaluate(model_keys: list, task_ids: list, before_ref: str, after_ref: str, max_seq_length: int,
             sides: list = ("before", "after")):
    import secrets
    import signal
    import socket
    import subprocess
    import sys
    import urllib.error
    import urllib.request

    import httpx

    root = Path("/root/unsloth")
    venv_py = Path.home() / ".unsloth/studio/unsloth_studio/bin/python"
    tasks = json.load(open("/eval/tasks.json"))
    if task_ids:
        tasks = [t for t in tasks if t["id"] in task_ids]

    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], check = True,
                              capture_output = True, text = True).stdout.strip()

    def port_open(port):
        with socket.socket() as s:
            return s.connect_ex(("127.0.0.1", port)) == 0

    def post(url, payload, token = None, timeout = 120):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data = json.dumps(payload).encode(), headers = headers, method = "POST")
        with urllib.request.urlopen(req, timeout = timeout) as r:
            return json.loads(r.read())

    def login(base_url, home, password):
        boot = home / "auth" / ".bootstrap_password"
        if boot.exists():
            first = boot.read_text().strip()
            tok = post(f"{base_url}/api/auth/login", {"username": "unsloth", "password": first})
            tok = post(f"{base_url}/api/auth/change-password",
                       {"current_password": first, "new_password": password}, token = tok["access_token"])
        else:
            tok = post(f"{base_url}/api/auth/login", {"username": "unsloth", "password": password})
            if tok.get("must_change_password"):
                raise RuntimeError("login requires a password change with no bootstrap file")
        return tok["access_token"]

    t0 = time.time()
    git("fetch", "--depth", "50", "origin", before_ref, after_ref, f"refs/pull/{PR}/head")
    print(f"fetched refs in {time.time() - t0:.0f}s", flush = True)
    result = {"pr": PR, "gpu": GPU, "install_ref": INSTALL_REF, "runs": []}
    llama_bin = None
    for cand in (Path.home() / ".unsloth/llama.cpp/llama-server",
                 Path.home() / ".unsloth/llama.cpp/build/bin/llama-server",
                 Path.home() / ".unsloth/studio/llama.cpp/llama-server",
                 Path.home() / ".unsloth/studio/llama.cpp/build/bin/llama-server"):
        if cand.is_file() and os.access(cand, os.X_OK):
            llama_bin = cand
            break
    if llama_bin is None:
        found = subprocess.run("find / -xdev -name llama-server -type f -perm -u+x 2>/dev/null | head -1",
                               shell = True, capture_output = True, text = True).stdout.strip()
        llama_bin = Path(found) if found else None
    if llama_bin is None:
        raise RuntimeError("no llama-server binary in the image")
    result["llama_server"] = str(llama_bin)
    print(f"llama-server: {llama_bin}", flush = True)

    for side, ref, port in (("before", before_ref, 9201), ("after", after_ref, 9202)):
        if side not in sides:
            continue
        git("checkout", "-q", "--detach", ref)
        subprocess.run(["git", "-C", str(root), "diff", "--quiet", ref], check = True)
        subprocess.run(f"find {root}/studio/backend -name __pycache__ -type d -prune -exec rm -rf {{}} +", shell = True)
        tools_py = (root / "studio/backend/core/inference/tools.py").read_text()
        side_meta = {"side": side, "ref": ref, "checked_out": git("rev-parse", "HEAD"),
                     "pr_code_present": "mcp_tool_schema" in tools_py}
        assert side_meta["checked_out"] == ref, side_meta
        assert not port_open(port), f"port {port} busy"
        home = Path(f"/root/home_{side}")
        home.mkdir(parents = True, exist_ok = True)
        password = secrets.token_urlsafe(16)
        env = dict(os.environ, UNSLOTH_STUDIO_HOME = str(home), HF_HOME = "/root/.cache/huggingface")
        env["LLAMA_SERVER_PATH"] = str(llama_bin)
        log_path = Path(f"/tmp/studio_{side}.log")
        log = open(log_path, "w")
        proc = subprocess.Popen(
            [str(venv_py), str(root / "studio/backend/run.py"), "--host", "127.0.0.1",
             "--port", str(port), f"--password={password}"],
            cwd = str(root), env = env, stdout = log, stderr = subprocess.STDOUT, start_new_session = True,
        )
        base = f"http://127.0.0.1:{port}"
        calls_log = Path(f"/tmp/calls_{side}.jsonl")
        marker = Path(f"/tmp/marker_{side}.txt")
        calls_log.write_text("")
        try:
            t_launch = time.time()
            deadline = time.time() + 1200
            while time.time() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError(f"studio exited rc={proc.returncode}")
                try:
                    with urllib.request.urlopen(f"{base}/api/health", timeout = 5) as r:
                        if r.status == 200:
                            break
                except Exception:
                    time.sleep(3)
            else:
                raise RuntimeError("studio never became healthy")
            side_meta["startup_s"] = round(time.time() - t_launch, 1)
            token = login(base, home, password)
            headers = {"Authorization": f"Bearer {token}"}
            server = post(f"{base}/api/mcp/servers/", {
                "display_name": "Notion",
                "url": f"{sys.executable} /eval/recording_server.py /eval/notion_tools.json {calls_log} {marker}",
                "is_enabled": True,
            }, token = token)
            side_meta["mcp_server_id"] = server.get("id")

            for key in model_keys:
                model = MODELS[key]
                run = dict(side_meta, model = model, max_seq_length = max_seq_length, tasks = [])
                t_load = time.time()
                loaded = post(f"{base}/api/inference/load", {
                    "model_path": model, "gguf_variant": "Q4_K_M", "max_seq_length": max_seq_length,
                }, token = token, timeout = 3600)
                run["load_s"] = round(time.time() - t_load, 1)
                run["context_length"] = loaded.get("context_length")
                print(f"[{side}] loaded {model} ctx={run['context_length']} in {run['load_s']}s", flush = True)

                for i, task in enumerate(tasks):
                    marker.write_text(task["id"])
                    before_lines = len(calls_log.read_text().splitlines())
                    body = {
                        "model": model,
                        "messages": [{"role": "user", "content": task["prompt"]}],
                        "stream": True,
                        "stream_options": {"include_usage": True},
                        "temperature": 0.0,
                        "seed": 3407,
                        "max_tokens": 2048,
                        "enable_tools": True,
                        "enabled_tools": [],
                        "mcp_enabled": True,
                        "permission_mode": "off",
                        "auto_heal_tool_calls": True,
                        "max_tool_calls_per_message": 6,
                    }
                    rec = {"id": task["id"], "tool_events": [], "tool_errors": [], "final_text": "", "usage": None}
                    t_req = time.time()
                    try:
                        with httpx.Client(timeout = httpx.Timeout(1800, connect = 30)) as client:
                            with client.stream("POST", f"{base}/api/inference/chat/completions", json = body,
                                               headers = dict(headers, **{"X-Unsloth-Events": "1"})) as resp:
                                if resp.status_code >= 400:
                                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.read().decode()[:500]}")
                                for line in resp.iter_lines():
                                    if not line.startswith("data: ") or line.strip() == "data: [DONE]":
                                        continue
                                    try:
                                        evt = json.loads(line[6:])
                                    except ValueError:
                                        continue
                                    if evt.get("type") in ("tool_start", "tool_end"):
                                        slim = {k: evt.get(k) for k in ("type", "tool_name", "tool_call_id", "arguments")}
                                        if evt["type"] == "tool_end":
                                            res = evt.get("result")
                                            slim["result"] = res[:400] if isinstance(res, str) else res
                                            if isinstance(res, str) and res.startswith("Error"):
                                                rec["tool_errors"].append({"tool": evt.get("tool_name"), "result": res[:400]})
                                        rec["tool_events"].append(slim)
                                    elif evt.get("usage"):
                                        rec["usage"] = evt["usage"]
                                    for choice in evt.get("choices") or []:
                                        rec["final_text"] += (choice.get("delta") or {}).get("content") or ""
                    except Exception as exc:
                        rec["error"] = f"{type(exc).__name__}: {exc}"[:600]
                    rec["wall_s"] = round(time.time() - t_req, 2)
                    time.sleep(0.5)
                    new = [json.loads(l) for l in calls_log.read_text().splitlines()[before_lines:] if l.strip()]
                    rec["calls"] = [{"name": c["name"], "arguments": c["arguments"]} for c in new]
                    rec["foreign_task_calls"] = sum(1 for c in new if c.get("task_id") != task["id"])
                    ends = {e["tool_call_id"]: e for e in rec["tool_events"] if e["type"] == "tool_end"}
                    rec["schema_tool_calls"] = sum(1 for e in ends.values() if e.get("tool_name") == "mcp_tool_schema")
                    rec["tool_sequence"] = [e.get("tool_name") for e in ends.values()]
                    rec["final_text"] = rec["final_text"][-600:]
                    run["tasks"].append(rec)
                    print(f"[{side}/{key}] {task['id']}: calls={[c['name'] for c in rec['calls']]} "
                          f"schema_tool={rec['schema_tool_calls']} errors={len(rec['tool_errors'])} "
                          f"wall={rec['wall_s']}s usage={rec['usage']} err={rec.get('error')}", flush = True)
                    if i == 0:
                        try:
                            count = post(f"{base}/api/inference/chat/count_tokens", {
                                "model": model, "messages": body["messages"], "enable_tools": True,
                                "enabled_tools": [], "mcp_enabled": True, "permission_mode": "off",
                                "max_tool_calls_per_message": 6,
                            }, token = token, timeout = 600)
                        except urllib.error.HTTPError as exc:
                            count = {"error": f"HTTP {exc.code}: {exc.read().decode()[:300]}"}
                        run["count_tokens"] = count
                        print(f"[{side}/{key}] count_tokens={count}", flush = True)
                result["runs"].append(run)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            result.setdefault("errors", []).append(f"{side}: {type(exc).__name__}: {exc}")
        finally:
            for sig, wait in ((signal.SIGTERM, 60), (signal.SIGKILL, 10)):
                try:
                    os.killpg(proc.pid, sig)
                    proc.wait(timeout = wait)
                    break
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    if proc.poll() is not None:
                        break
            log.close()
            tail = [l for l in log_path.read_text(errors = "replace").splitlines()[-400:]
                    if not any(s in l.lower() for s in ("password", "token", "bearer", "sk-unsloth"))]
            result.setdefault("log_tails", {})[side] = "\n".join(tail[-120:])
            for _ in range(60):
                if not port_open(port):
                    break
                time.sleep(1)
    return result


@app.local_entrypoint()
def main(models: str = "4b", tasks: str = "search_created_range,query_rows_checkbox",
         before_ref: str = BEFORE_REF, after_ref: str = AFTER_REF, max_seq_length: int = 100000,
         tag: str = "latest", sides: str = "before,after"):
    task_ids = [] if tasks == "all" else [t for t in tasks.split(",") if t]
    t0 = time.time()
    keys = models.split(",")
    calls = [evaluate.spawn([key], task_ids, before_ref, after_ref, max_seq_length, sides.split(",")) for key in keys]
    out = HERE / "out" / tag
    out.mkdir(parents = True, exist_ok = True)
    for key, call in zip(keys, calls):
        result = call.get()
        for run in result["runs"]:
            name = f"run_{run['side']}_{run['model'].split('/')[-1]}.json"
            (out / name).write_text(json.dumps(run, indent = 1))
        for side, tail in (result.pop("log_tails", None) or {}).items():
            (out / f"studio_{side}_{key}_log_tail.txt").write_text(tail)
        meta = {k: v for k, v in result.items() if k != "runs"}
        meta["wall_s_total"] = round(time.time() - t0, 1)
        (out / f"meta_{key}.json").write_text(json.dumps(meta, indent = 1))
        print(json.dumps(meta, indent = 1))
