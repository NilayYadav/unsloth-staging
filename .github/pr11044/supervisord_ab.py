import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = "93b3dedd11a371e04383dd4253624f0a4843afd1"
SAVE_S = 25
ROOT = Path("/tmp/sv-ab")

STUB = r'''
import pathlib, signal, sys, time
marker = pathlib.Path(sys.argv[1])
def on_term(signum, frame):
    print("stub: SIGTERM, saving for %ss" % sys.argv[2], flush=True)
    time.sleep(float(sys.argv[2]))
    marker.write_text("saved")
    print("stub: saved", flush=True)
    sys.exit(0)
signal.signal(signal.SIGTERM, on_term)
print("stub: running", flush=True)
while True:
    time.sleep(1)
'''


def program_section(text):
    match = re.search(r"^\[program:studio\]\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
    keep = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith(";"):
            continue
        key, _, value = line.partition("=")
        keep[key.strip()] = value.strip()
    return keep


def run_side(label, conf_text, env_extra):
    d = ROOT / label
    d.mkdir(parents=True, exist_ok=True)
    (d / "stub.py").write_text(STUB)
    prog = program_section(conf_text)
    directives = {k: prog.get(k) for k in ("stopwaitsecs", "killasgroup", "stopasgroup", "stopsignal")}
    prog.update({
        "command": f"{sys.executable} {d}/stub.py {d}/saved {SAVE_S}",
        "directory": str(d),
        "stdout_logfile": str(d / "studio.out"),
        "stderr_logfile": str(d / "studio.err"),
    })
    prog.pop("environment", None)
    conf = "\n".join([
        "[unix_http_server]", f"file={d}/sv.sock",
        "[supervisorctl]", f"serverurl=unix://{d}/sv.sock",
        "[rpcinterface:supervisor]",
        "supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface",
        "[supervisord]", "nodaemon=true", f"pidfile={d}/sv.pid",
        f"logfile={d}/supervisord.log", "loglevel=info",
        "[program:studio]", *[f"{k}={v}" for k, v in prog.items()],
    ]) + "\n"
    (d / "supervisord.conf").write_text(conf)
    env = {k: v for k, v in os.environ.items() if not k.startswith("UNSLOTH_")}
    env.update(env_extra)
    facts = {"label": label, "directives": directives, "env": env_extra}
    sv = subprocess.Popen(["supervisord", "-c", str(d / "supervisord.conf")], env=env,
                          stdout=open(d / "sv.stdout", "w"), stderr=subprocess.STDOUT)
    ctl = ["supervisorctl", "-c", str(d / "supervisord.conf")]
    deadline = time.time() + 60
    while time.time() < deadline:
        if sv.poll() is not None:
            facts["supervisord_start_failed"] = (d / "sv.stdout").read_text()[-600:]
            return facts
        status = subprocess.run(ctl + ["status", "studio"], capture_output=True, text=True).stdout
        if "RUNNING" in status:
            break
        time.sleep(1)
    else:
        facts["never_running"] = True
        sv.kill()
        return facts
    t0 = time.monotonic()
    stop = subprocess.run(ctl + ["stop", "studio"], capture_output=True, text=True, timeout=400)
    facts["supervisorctl_stop_s"] = round(time.monotonic() - t0, 1)
    facts["supervisorctl_stop_output"] = stop.stdout.strip()
    subprocess.run(ctl + ["shutdown"], capture_output=True, text=True)
    sv.wait(timeout=60)
    log = (d / "supervisord.log").read_text()
    facts["sigkill_sent"] = "SIGKILL" in log
    facts["exit_status_0"] = "stopped: studio (exit status 0)" in log
    facts["checkpoint_marker_written"] = (d / "saved").exists()
    facts["supervisord_log"] = [l for l in log.splitlines() if "studio" in l][-6:]
    return facts


def main():
    with urllib.request.urlopen(
        f"https://raw.githubusercontent.com/unslothai/unsloth/{BASE}/docker/supervisord.conf"
    ) as r:
        base_conf = r.read().decode()
    head_conf = Path("docker/supervisord.conf").read_text()
    docker_env = {"UNSLOTH_STUDIO_SHUTDOWN_STOP_TIMEOUT_S": "120", "UNSLOTH_STUDIO_STOP_WAIT_S": "150"}
    report = {
        "save_takes_s": SAVE_S,
        "supervisord_version": subprocess.run(["supervisord", "--version"], capture_output=True,
                                              text=True).stdout.strip(),
        "base_conf_sha256": hashlib.sha256(base_conf.encode()).hexdigest(),
        "head_conf_sha256": hashlib.sha256(head_conf.encode()).hexdigest(),
        "before": run_side("before", base_conf, docker_env),
        "after": run_side("after", head_conf, docker_env),
        "after_without_env": run_side("after_without_env", head_conf, {}),
    }
    b, a = report["before"], report["after"]
    report["before_reproduces_kill"] = bool(b.get("sigkill_sent") and not b.get("checkpoint_marker_written"))
    report["after_waits_for_save"] = bool(
        a.get("checkpoint_marker_written") and a.get("exit_status_0") and not a.get("sigkill_sent")
        and a.get("supervisorctl_stop_s", 0) >= SAVE_S - 1
    )
    text = json.dumps(report, indent=2)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("## PR 11044 supervisord stop A/B\n\n| side | stopwaitsecs | stop took | SIGKILL | save marker |\n|---|---|---|---|---|\n")
            for side in ("before", "after", "after_without_env"):
                s = report[side]
                f.write(f"| {side} | {s['directives'].get('stopwaitsecs')} | {s.get('supervisorctl_stop_s')}s | "
                        f"{s.get('sigkill_sent')} | {s.get('checkpoint_marker_written')} |\n")
            f.write(f"\n```json\n{text}\n```\n")
    if not (report["before_reproduces_kill"] and report["after_waits_for_save"]):
        print("FAIL: A/B expectation not met")
        sys.exit(1)
    print("PASS: merge base SIGKILLs the save at 10s; PR head waits for it")


if __name__ == "__main__":
    main()
