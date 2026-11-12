import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import studio_drv
from studio_drv import Studio, sh

ROOT = os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "cluster")
os.makedirs(ROOT, exist_ok = True)
LAN = sys.argv[1]
MODEL = os.environ["MODEL"]
studio_drv.STUDIO_PORT = 18899
PAIR_PORT = 50171
P = "Write three sentences about the ocean. /no_think"
ENV = dict(os.environ)
ports = {
    i: {"agent": 50170 + 10 * i, "rpc": 50152 + 10 * i, "native": 50153 + 10 * i} for i in (1, 2)
}
procs = {}
extra = []
R = {"lan_ip": LAN, "steps": []}


def step(name, **d):
    R["steps"].append({"step": name, **d})
    print("STEP", name, json.dumps(d)[:2000], flush = True)


def wenv(i):
    return dict(
        ENV,
        UNSLOTH_CLUSTER_STATE_DIR = f"{ROOT}/w{i}/state",
        UNSLOTH_CLUSTER_RUN_DIR = f"{ROOT}/w{i}/run",
    )


def cli(
    *a,
    env = None,
    timeout = 120,
):
    rc, out = sh(["unsloth", *a], env = env or ENV, timeout = timeout)
    return {"rc": rc, "out": out[-1200:]}


def start_worker(i, agent_port = None):
    stop_worker(i)
    p = ports[i]
    procs[i] = subprocess.Popen(
        [
            "unsloth",
            "worker",
            "--port",
            str(agent_port or p["agent"]),
            "--rpc-port",
            str(p["rpc"]),
            "--native-port",
            str(p["native"]),
            *extra,
        ],
        env = wenv(i),
        stdout = open(f"{ROOT}/worker{i}.log", "a"),
        stderr = subprocess.STDOUT,
    )


def stop_worker(i, hard = False):
    p = procs.pop(i, None)
    if p and p.poll() is None:
        (p.kill if hard else p.terminate)()
        try:
            p.wait(30)
        except subprocess.TimeoutExpired:
            p.kill()


def wlog(i, n = 500):
    try:
        return open(f"{ROOT}/worker{i}.log", errors = "replace").read()[-n:]
    except Exception:
        return ""


def rpc_pids(i):
    rc, out = sh(f"pgrep -f 'rpc-server.*-p {ports[i]['rpc']}' || true")
    return [x for x in out.split() if x.isdigit()]


def topo(st):
    s = st.status()["spark"]
    return {
        "topology": re.findall(r'"topology": "(\w+)"', s)[:1],
        "reason": re.findall(r'"reason": "([^"]+)"', s)[:1],
        "transports": sorted(set(re.findall(r'"transport": "(\w+)"', s))),
        "hosts": sorted(set(re.findall(r'"host": "([^"]+)"', s))),
    }


try:
    for i in (1, 2):
        p = subprocess.Popen(
            ["unsloth", "cluster", "pair", "--addr", LAN, "--port", str(PAIR_PORT), "--ttl", "3m"],
            env = ENV,
            stdout = subprocess.PIPE,
            stderr = subprocess.STDOUT,
            text = True,
        )
        token = None
        for line in p.stdout:
            m = re.search(r"(unsloth-pair-\S+)", line)
            if m and "`" not in line:
                token = m.group(1)
                break
        rc, out = sh(
            [
                "unsloth",
                "cluster",
                "join",
                "--token-stdin",
                "--name",
                f"mac-worker-{i}",
                "--port",
                str(ports[i]["agent"]),
            ],
            env = wenv(i),
            input = (token or "") + "\n",
            timeout = 120,
        )
        rest = p.stdout.read()
        p.wait(60)
        step(
            f"pair mac-worker-{i} over {LAN}",
            join_rc = rc,
            join = out.replace(token or "#", "<token>")[-200:],
            pair_rc = p.returncode,
            pair = rest.strip()[-200:],
        )

    moved = ports[2]["agent"] + 5
    start_worker(1)
    start_worker(2, agent_port = moved)
    time.sleep(15)
    lst = cli("cluster", "list")
    if lst["out"].count("ready") < 2:
        step(
            "workers not ready on this address; retrying with --allow-any-network",
            list = lst,
            worker1 = wlog(1),
        )
        extra = ["--allow-any-network"]
        start_worker(1)
        start_worker(2, agent_port = moved)
        time.sleep(15)
        lst = cli("cluster", "list")
    disc = cli("cluster", "discover", "--seconds", "6")
    step(
        "discovery: worker 2 serves on a port it did not pair with",
        moved_port = moved,
        discover = disc,
        list = lst,
        found = ("found by discovery" in lst["out"]),
        worker1 = wlog(1, 400),
        worker2 = wlog(2, 400),
    )

    st = Studio(ENV, "default")
    ld = st.load(MODEL)
    single = st.chat(prompt = P)
    step(
        "default planner",
        load_http = ld["http"],
        load_s = ld["seconds"],
        resp = ld["resp"][-400:] if ld["http"] != 200 else "",
        chat = single,
        **topo(st),
    )
    st.stop()

    os.environ["UNSLOTH_CLUSTER_TOPOLOGY"] = "layer_split"
    st = Studio(dict(ENV, UNSLOTH_CLUSTER_TOPOLOGY = "layer_split"), "split")
    ld = st.load(MODEL)
    c = st.chat(prompt = P)
    step(
        "forced split over the paired workers",
        load_http = ld["http"],
        load_s = ld["seconds"],
        resp = ld["resp"][-400:] if ld["http"] != 200 else "",
        chat = c,
        same_text_as_default = (c.get("text") == single.get("text")),
        **topo(st),
        args = st.grep(r"Appending user extra args", 1),
    )

    before = rpc_pids(2)
    stop_worker(2, hard = True)
    time.sleep(3)
    orphan = rpc_pids(2)
    c = st.chat(prompt = P, max_tokens = 16, timeout = 120)
    step(
        "worker 2 agent SIGKILLed mid-session",
        rpc_server_before = before,
        rpc_server_left_running = orphan,
        next_chat_http = c["http"],
        resp = c.get("resp", c.get("text")),
        list = cli("cluster", "list"),
    )
    start_worker(2, agent_port = moved)
    time.sleep(15)
    step(
        "worker 2 restarted after the kill",
        rpc_server_now = rpc_pids(2),
        orphan_cleaned = (not orphan or not set(orphan) & set(rpc_pids(2))),
        list = cli("cluster", "list"),
        worker2 = wlog(2, 500),
    )
    ld = st.load(MODEL)
    c = st.chat(prompt = P)
    step(
        "reload with both workers back",
        load_http = ld["http"],
        chat_http = c["http"],
        same_text_as_default = (c.get("text") == single.get("text")),
        **topo(st),
    )

    rm = cli("cluster", "remove", "mac-worker-1")
    time.sleep(6)
    c = st.chat(prompt = P, max_tokens = 16, timeout = 120)
    step(
        "`cluster remove mac-worker-1` during a live session",
        remove = rm,
        next_chat_http = c["http"],
        list = cli("cluster", "list"),
        worker1 = wlog(1, 400),
    )
    ld = st.load(MODEL)
    c = st.chat(prompt = P)
    step("reload after removal", load_http = ld["http"], chat_http = c["http"], **topo(st))
    st.stop()
    lv = cli("cluster", "leave", "--yes", env = wenv(2))
    step("worker 2 leaves", leave = lv, list = cli("cluster", "list"))
except Exception:
    import traceback
    step("HARNESS ERROR", error = traceback.format_exc()[-2500:])
finally:
    for i in list(procs):
        stop_worker(i)

json.dump(R, open(f"{ROOT}/result.json", "w"), indent = 1)
summary = os.environ.get("GITHUB_STEP_SUMMARY")
if summary:
    with open(summary, "a") as f:
        f.write("## Cluster scenarios\n\n")
        for s in R["steps"]:
            f.write(
                f"### {s['step']}\n```json\n{json.dumps({k: v for k, v in s.items() if k != 'step'}, indent = 1)[:3000]}\n```\n"
            )
