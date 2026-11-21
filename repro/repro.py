import ast, os, signal, subprocess, sys, time, logging
import psutil

src = open(sys.argv[1]).read()
tree = ast.parse(src)
want = {"_darwin_footprint_bytes", "_tree_memory_bytes", "_memory_watchdog", "_memory_exceeded_note"}
nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in want]
assert {n.name for n in nodes} == want
ns = {
    "sys": sys, "time": time, "logger": logging.getLogger("x"),
    "_darwin_libproc": None, "_MEMORY_WATCHDOG_POLL_S": 1.0,
    "_killpg_captured": lambda pgid: os.killpg(pgid, signal.SIGKILL),
    "_kill_process_tree": lambda p: p.kill(),
}
exec(compile(ast.Module(body=nodes, type_ignores=[]), "pr_tools", "exec"), ns)

GIB = 1024**3
LIMIT = 8 * GIB  # PR default on this runner: half of RAM, floored at 8 GB
print(f"runner RAM {psutil.virtual_memory().total/GIB:.1f} GiB, limit {LIMIT/GIB:.0f} GiB")

TOOL = """
import multiprocessing as mp, sys, time
def work(_):
    import torch
    return 1
if __name__ == '__main__':
    mp.set_start_method('spawn')
    pool = mp.Pool(int(sys.argv[1]))
    pool.map(work, range(int(sys.argv[1])), chunksize=1)
    print('READY', flush=True)
    time.sleep(30)
"""

TOOL_PATH = os.path.abspath("tool_call.py")
open(TOOL_PATH, "w").write(TOOL)

def tree(pid):
    r = psutil.Process(pid)
    return [r, *r.children(recursive=True)]

for n in [int(x) for x in sys.argv[2:]]:
    proc = subprocess.Popen([sys.executable, TOOL_PATH, str(n)], stdout=subprocess.PIPE, text=True, start_new_session=True)
    assert proc.stdout.readline().strip() == "READY"
    time.sleep(2)
    procs = tree(proc.pid)
    rss = sum(p.memory_info().rss for p in procs)
    fp = sum(ns["_darwin_footprint_bytes"](p.pid) for p in procs)
    pr = ns["_tree_memory_bytes"](proc.pid)
    used_before = psutil.virtual_memory()
    print(f"\n== {n} workers each importing torch ({len(procs)} processes)")
    print(f"  PR watchdog count (sum of max(rss, footprint)): {pr/GIB:.2f} GiB")
    print(f"  sum of RSS:                                    {rss/GIB:.2f} GiB")
    print(f"  sum of phys_footprint (Activity Monitor):      {fp/GIB:.2f} GiB")
    print(f"  system RAM in use right now:                   {(used_before.total-used_before.available)/GIB:.2f} GiB")
    t = time.time()
    ns["_memory_watchdog"](proc, LIMIT, os.getpgid(proc.pid), 0.5)
    killed = getattr(proc, "_unsloth_memory_exceeded", None)
    try: os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError: pass
    proc.wait()
    print(f"  watchdog verdict at {LIMIT/GIB:.0f} GiB limit: {'KILLED' if killed else 'left alone'}"
          f" ({time.time()-t:.1f}s) note={ns['_memory_exceeded_note'](proc).strip()!r}")
    if not killed:
        pass
