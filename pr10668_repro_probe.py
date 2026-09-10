"""PR 10668 repro probe: does a tool result that only *quotes* __IMAGES__ /
__RAG_SOURCES__ survive the trip to the model?

Run from studio/backend. Exit 0 = the anchored+validated behaviour is in place.
Exit 1 = the result the model receives is truncated (the defect).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "studio", "backend"))
from core.inference.tool_loop_controller import strip_result_for_model  # noqa: E402

failures = []
checks = []


def check(label, got, want, must_hold_on_main=False):
    ok = got == want
    checks.append((label, ok, got, want, must_hold_on_main))
    if not ok:
        failures.append(label)
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    print(f"       model receives: {got!r}")
    if not ok:
        print(f"       expected      : {want!r}")
    return ok


print("=" * 78)
print("A. real session: user asks Studio to grep its own source for the marker")
print("=" * 78)
# What `terminal` returns for: grep -n '__IMAGES__' studio/backend/core/inference/tools.py
grep_out = (
    'tools.py:16130:    # __IMAGES__ stays LAST: older clients slice from it to the end of the\n'
    'tools.py:16134:        out += f"\\n__IMAGES__:{_json.dumps(images)}"\n'
    'tools.py:15948:    for marker in ("\\n__FILES__:", "\\n__IMAGES__:"):'
)
check("A1 grep hits survive whole (terminal)", strip_result_for_model(grep_out, "terminal"), grep_out)
check("A2 grep hits survive whole (no tool name)", strip_result_for_model(grep_out), grep_out)

print()
print("=" * 78)
print("B. real session: a retrieved document chunk quotes the RAG marker")
print("=" * 78)
doc = (
    "Studio splits the UI source map from the result with the literal\n"
    '__RAG_SOURCES__: prefix, and every loop strips it before the model reads it.\n'
    "The answer to the question is 42."
)
check("B1 retrieved chunk survives whole (search_knowledge_base)", strip_result_for_model(doc, "search_knowledge_base"), doc)

print()
print("=" * 78)
print("C. regression guard: a genuine envelope must STILL be stripped")
print("=" * 78)
# exactly what tools._created_file_sentinels appends after a matplotlib call
sandbox = "figure written\n__FILES__:" + json.dumps([{"name": "plot.png", "size": 12345}]) + \
    "\n__IMAGES__:" + json.dumps(["plot.png"])
check("C1 sandbox __FILES__+__IMAGES__ envelope stripped", strip_result_for_model(sandbox, "python"), "figure written")

# exactly what tools.py appends with RAG_SOURCES_SENTINEL
rag = "Chunk text.\n__RAG_SOURCES__:" + json.dumps([{"filename": "a.pdf", "page": 2}], ensure_ascii=False)
check("C2 search_knowledge_base source map stripped", strip_result_for_model(rag, "search_knowledge_base"), "Chunk text.")

# gemini code_execution attaches a data URI list the same way
gem = "print(fig)\n__IMAGES__:" + json.dumps(["data:image/png;base64,iVBORw0KGgo="])
check("C3 hosted code_execution image envelope stripped", strip_result_for_model(gem, None), "print(fig)")

print()
print("=" * 78)
print("D. regression guard: a Gemini code_execution turn that drew TWO figures")
print("   stacks one envelope per inlineData part - none of it may reach the model")
print("=" * 78)
two_plots = (
    "Figures saved."
    + "\n__IMAGES__:" + json.dumps(["data:image/png;base64," + "A" * 64])
    + "\n__IMAGES__:" + json.dumps(["data:image/png;base64," + "B" * 64])
)
check("D1 both stacked image envelopes stripped", strip_result_for_model(two_plots, "code_execution"), "Figures saved.")

print()
print("=" * 78)
print("E. a document an MCP tool read, ending in a well-formed envelope, is content")
print("=" * 78)
manifest = 'icons/README\n__IMAGES__:["icon.png"]'
check("E1 MCP document keeps its last line", strip_result_for_model(manifest, "mcp__fs__read_file"), manifest)
check("E2 the sandbox tool that emits it still loses it", strip_result_for_model(manifest, "python"), "icons/README")

print()
print("=" * 78)
lost = sum(1 for lbl, ok, got, want, _ in checks if not ok)
for lbl, ok, got, want, _ in checks:
    if not ok:
        print(f"TRUNCATION: {lbl}: model lost {len(want) - len(got)} of {len(want)} chars")
print(f"RESULT: {len(checks) - lost} passed, {lost} failed")
print("=" * 78)
sys.exit(1 if failures else 0)
