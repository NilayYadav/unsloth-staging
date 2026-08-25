#!/usr/bin/env python3
"""Remove the external-provider guard, and nothing else, so the no-op can be reproduced."""

from pathlib import Path

GUARD = (
    "    # Armed research appends Studio's own deep_research past every filter below, and no\n"
    "    # provider can run it, so such a selection is never purely hosted. Without this the\n"
    "    # request proxies through, the tool is never offered and arming research does nothing.\n"
    '    if getattr(payload, "deep_research_armed", False):\n'
    "        return False\n"
)

path = Path("studio/backend/routes/inference.py")
source = path.read_text()
if GUARD not in source:
    raise SystemExit("the guard this repro removes is not where it was")
path.write_text(source.replace(GUARD, "", 1))
print("guard removed; everything else is untouched")
