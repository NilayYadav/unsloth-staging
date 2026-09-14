import json
import os
import subprocess
import sys

import modal

HERE = os.path.dirname(os.path.abspath(__file__))
BEFORE = os.path.join(HERE, "main_now/studio/backend")
AFTER = os.path.join(HERE, "after_on_main/studio/backend")
IGNORE = ["**/__pycache__", "**/.pytest_cache", "**/tests/**"]

image = (
    modal.Image.debian_slim(python_version = "3.12")
    .pip_install_from_requirements(os.path.join(BEFORE, "requirements/studio.txt"))
    .add_local_dir(BEFORE, "/src/before", ignore = IGNORE)
    .add_local_dir(AFTER, "/src/after", ignore = IGNORE)
    .add_local_file(os.path.join(HERE, "byteid.py"), "/src/byteid.py")
    .add_local_file(os.path.join(HERE, "plain_server.py"), "/src/plain_server.py")
)
app = modal.App("pr10940-byte-identity")


@app.function(image = image, timeout = 1800)
def compare():
    for side in ("before", "after"):
        p = subprocess.run([sys.executable, "/src/byteid.py", f"/src/{side}", "/src/plain_server.py", f"/tmp/{side}.json"],
                           cwd = f"/src/{side}", capture_output = True, text = True)
        print(f"[{side}] exit={p.returncode}\n{p.stdout[-1500:]}\n{p.stderr[-2500:]}", flush = True)
        if p.returncode:
            return
    before = json.load(open("/tmp/before.json"))
    after = json.load(open("/tmp/after.json"))
    assert before.keys() == after.keys()
    groups = {}
    for key in before:
        group = key.split(":", 1)[0]
        same = before[key]["sha256"] == after[key]["sha256"]
        g = groups.setdefault(group, {"total": 0, "identical": 0, "differ": []})
        g["total"] += 1
        g["identical"] += same
        if not same:
            g["differ"].append(key)
    for group, g in groups.items():
        print(f"GROUP {group}: {g['identical']}/{g['total']} byte-identical", flush = True)
        for key in g["differ"][:5]:
            print(f"  DIFF {key}\n    before={before[key]['head']!r}\n    after ={after[key]['head']!r}", flush = True)
    for key in sorted(k for k in before if k.startswith("stdio:")):
        print(f"STDIO {key[6:]}: len={before[key]['len']} sha256={before[key]['sha256'][:16]} "
              f"{'IDENTICAL' if before[key]['sha256'] == after[key]['sha256'] else 'DIFFERENT'} head={before[key]['head'][:90]!r}", flush = True)


@app.local_entrypoint()
def main():
    compare.remote()
