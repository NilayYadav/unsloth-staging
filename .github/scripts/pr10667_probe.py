# PR 10667 A/B probe: what an uploaded document ends up being called.
#
# Identical on both branches. Only studio/backend/routes/rag.py differs, so a
# difference in this output is a difference in the shipped implementation.

import io
import os
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[2] / "studio" / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from routes import rag as rag_routes  # noqa: E402
from routes.rag import _sanitize_filename  # noqa: E402

failures = []
lines = []


def check(label, got, want, op = "=="):
    ok = (got == want) if op == "==" else (got != want)
    lines.append(
        f"{'PASS' if ok else 'FAIL'}  {label}\n"
        f"        got    {got!r}\n"
        f"        want   {op} {want!r}"
    )
    if not ok:
        failures.append(label)


print("=" * 78)
print("PR 10667 repro -- uploaded document names")
print(f"python {sys.version.split()[0]}  platform {sys.platform}")
print("=" * 78)

# --- The defect: a name the user gave, coming back out of the sanitizer. ------
check("Chinese name survives", _sanitize_filename("报告.pdf"), "报告.pdf")
check("Japanese name survives", _sanitize_filename("日本語ドキュメント.txt"), "日本語ドキュメント.txt")
check("Cyrillic + digits survive", _sanitize_filename("Отчёт 2026.pdf"), "Отчёт 2026.pdf")
check("Accents survive", _sanitize_filename("résumé.docx"), "résumé.docx")
check("A plain space is not an underscore", _sanitize_filename("My Report.pdf"), "My Report.pdf")
check("Punctuation is not an underscore", _sanitize_filename("Q3: Revenue.pdf"), "Q3: Revenue.pdf")

# --- The consequence: two different documents becoming one name. -------------
check(
    "Two different Chinese names stay different",
    _sanitize_filename("报告.pdf"),
    _sanitize_filename("会议记录.pdf"),
    op = "!=",
)

# --- The guarantees that must NOT regress. -----------------------------------
for raw in ["../../etc/passwd.txt", "..\\..\\windows\\evil.txt", "/absolute/notes.txt", "C:\\Users\\me\\notes.txt"]:
    out = _sanitize_filename(raw)
    ok = "/" not in out and "\\" not in out
    lines.append(f"{'PASS' if ok else 'FAIL'}  no path separator survives {raw!r} -> {out!r}")
    if not ok:
        failures.append(f"separator in {raw!r}")

for raw, want in [("a\x00b.pdf", "ab.pdf"), ("a\u202eb.pdf", "ab.pdf"), ("a\u200bb.pdf", "ab.pdf"), ("a\nb.pdf", "a b.pdf")]:
    check(f"control/bidi stripped from {raw!r}", _sanitize_filename(raw), want)

long_out = _sanitize_filename("x" * 300 + ".txt")
ok = len(long_out) <= 200 and long_out.endswith(".txt")
lines.append(f"{'PASS' if ok else 'FAIL'}  long name trimmed to {len(long_out)} chars keeping '.txt'")
if not ok:
    failures.append("length/extension")

empty_out = _sanitize_filename("\u3000\u3000")
ok = empty_out == "document"
lines.append(f"{'PASS' if ok else 'FAIL'}  all-whitespace name falls back -> {empty_out!r}")
if not ok:
    failures.append("empty fallback")

# --- End to end through the real upload entry point. -------------------------
from fastapi import UploadFile  # noqa: E402

uploads = Path(os.environ["PR10667_UPLOADS"])
uploads.mkdir(parents = True, exist_ok = True)
rag_routes.rag_uploads_root = lambda: uploads

stored_path, filename, _digest = rag_routes._save_upload(
    UploadFile(file = io.BytesIO(b"alpha bravo charlie " * 50), filename = "报告 2026.txt")
)
check("_save_upload keeps the display name", filename, "报告 2026.txt")

stem = Path(stored_path).stem
try:
    uuid.UUID(stem)
    stored_is_uuid = True
except ValueError:
    stored_is_uuid = False
lines.append(
    f"{'PASS' if stored_is_uuid else 'FAIL'}  bytes land at a uuid path, the name is never a path"
    f"\n        stored {Path(stored_path).name!r}"
)
if not stored_is_uuid:
    failures.append("stored path is not a uuid")

print("\n".join(lines))
print("=" * 78)
if failures:
    print(f"RESULT: FAIL -- {len(failures)} check(s) failed: {failures}")
    sys.exit(1)
print("RESULT: PASS -- every check held")
