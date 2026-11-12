import json
import os
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.request

STUDIO_PORT = 18888


def sh(
    cmd,
    timeout = 300,
    env = None,
    input = None,
):
    p = subprocess.run(
        cmd,
        shell = isinstance(cmd, str),
        text = True,
        capture_output = True,
        timeout = timeout,
        env = env,
        input = input,
    )
    return p.returncode, (p.stdout + p.stderr).strip()


class Studio:
    def __init__(self, env, tag):
        self.log = f"/tmp/studio-{tag}.log"
        home = self.home = env.get("HOME", os.path.expanduser("~"))
        sh(f"rm -rf {home}/.unsloth/studio/auth")
        self.proc = subprocess.Popen(
            ["unsloth", "studio", "-H", "127.0.0.1", "-p", str(STUDIO_PORT)],
            env = dict(env, UNSLOTH_API_ONLY = "1"),
            stdout = open(self.log, "w"),
            stderr = subprocess.STDOUT,
        )
        self.base = f"http://127.0.0.1:{STUDIO_PORT}"
        end = time.time() + 300
        while time.time() < end:
            try:
                urllib.request.urlopen(self.base + "/api/health", timeout = 3).read()
                break
            except Exception:
                if self.proc.poll() is not None:
                    raise RuntimeError("studio exited: " + self.tail())
                time.sleep(1)
        else:
            raise RuntimeError("studio health timeout: " + self.tail())
        old = open(f"{home}/.unsloth/studio/auth/.bootstrap_password").read().strip()
        new = "Mt-" + secrets.token_urlsafe(12)
        tok = self.call("POST", "/api/auth/login", {"username": "unsloth", "password": old})[1][
            "access_token"
        ]
        self.token = tok
        self.call(
            "POST", "/api/auth/change-password", {"current_password": old, "new_password": new}
        )
        self.token = self.call("POST", "/api/auth/login", {"username": "unsloth", "password": new})[
            1
        ]["access_token"]

    def tail(self, n = 4000):
        return open(self.log, errors = "replace").read()[-n:]

    def grep(
        self,
        pat,
        n = 40,
    ):
        rx = re.compile(pat, re.I)
        return [
            l[:400] for l in open(self.log, errors = "replace").read().splitlines() if rx.search(l)
        ][-n:]

    def call(
        self,
        method,
        path,
        body = None,
        timeout = 1800,
    ):
        req = urllib.request.Request(
            self.base + path, json.dumps(body).encode() if body is not None else None, method = method
        )
        req.add_header("Content-Type", "application/json")
        if getattr(self, "token", None):
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout = timeout) as r:
                raw = r.read().decode()
                code = r.status
        except urllib.error.HTTPError as e:
            raw, code = e.read().decode(errors = "replace"), e.code
        except Exception as e:
            return 0, {"error": repr(e)[:400]}
        try:
            return code, json.loads(raw)
        except Exception:
            return code, {"raw": raw[:1500]}

    def load(self, path, **kw):
        t0 = time.time()
        code, d = self.call(
            "POST",
            "/api/inference/load",
            {
                "model_path": path,
                "is_lora": False,
                "max_seq_length": 4096,
                "speculative_type": "off",
                "n_parallel": 1,
                **kw,
            },
        )
        return {"http": code, "seconds": round(time.time() - t0, 1), "resp": json.dumps(d)[:900]}

    def chat(
        self,
        prompt = "Write three sentences about the ocean. /no_think",
        max_tokens = 96,
        timeout = 600,
    ):
        t0 = time.time()
        code, d = self.call(
            "POST",
            "/v1/chat/completions",
            {
                "model": "default",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "seed": 1,
                "max_tokens": max_tokens,
                "stream": False,
            },
            timeout = timeout,
        )
        dt = time.time() - t0
        out = {"http": code, "seconds": round(dt, 2)}
        try:
            out["text"] = d["choices"][0]["message"]["content"]
            n = d.get("usage", {}).get("completion_tokens")
            out["tokens"] = n
            tm = d.get("timings") or {}
            out["tok_s"] = round(tm.get("predicted_per_second") or (n / dt if n else 0), 2)
        except Exception:
            out["resp"] = json.dumps(d)[:800]
        return out

    def status(self):
        return {
            "spark": json.dumps(self.call("GET", "/api/inference/spark/status")[1])[:1800],
            "inference": json.dumps(self.call("GET", "/api/inference/status")[1])[:600],
        }

    def stop(self):
        self.call("POST", "/api/inference/unload", {})
        self.proc.terminate()
        try:
            self.proc.wait(60)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        sh(f"pkill -9 -f '{self.home}/.unsloth/llama.cpp/.*llama-server'; true")
