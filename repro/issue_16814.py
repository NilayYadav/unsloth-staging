import asyncio, os, subprocess, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "studio", "backend"))
from core.inference import mcp_client as m

mode, d = sys.argv[1], sys.argv[2]
open(os.path.join(d, "a.txt"), "w").write("hello")
pkg = ["-y", "@modelcontextprotocol/server-filesystem@2026.8.31", d]
parts = ["cmd", "/c", "npx", *pkg] if mode == "cmd" else ["npx", *pkg]
url = m.join_stdio_command(parts)
print("address:", url, flush=True)

def nodes():
    if sys.platform != "win32":
        return ""
    out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout
    return f"node={out.lower().count('node.exe')} cmd={out.lower().count('cmd.exe')}"

t = time.monotonic()
tools = asyncio.run(m.list_tools_async(url, None, timeout=120))
print(f"test-connection {time.monotonic()-t:.1f}s tools={len(tools)} {nodes()}", flush=True)

plan = [("list_allowed_directories", {}, 0)] + [("read_file", {"path": os.path.join(d, "a.txt")}, 0)] * 4
plan += [("read_file", {"path": os.path.join(d, "a.txt")}, 70)] + [("read_file", {"path": os.path.join(d, "a.txt")}, 0)] * 3
plan += [("read_file", {"path": os.path.join(d, "a.txt")}, 200)] + [("read_file", {"path": os.path.join(d, "a.txt")}, 0)] * 3
fails = 0
for i, (name, args, wait) in enumerate(plan):
    if wait:
        print(f"-- idle {wait}s", flush=True)
        time.sleep(wait)
    t = time.monotonic()
    out = m.call_tool_sync(url, None, name, args, timeout=300, scope="chat-1")
    bad = out.startswith("Error")
    fails += bad
    print(f"{i:2d} {name} {time.monotonic()-t:6.1f}s {'FAIL' if bad else 'ok  '} {out[:90]!r} {nodes()}", flush=True)
print(f"RESULT mode={mode} failures={fails}/{len(plan)}", flush=True)
sys.exit(1 if fails else 0)
