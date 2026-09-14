# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/test_pr_ui_diff_guards.py
"""The two checks that turn a silent screenshot failure into a non-zero exit.

Both failures they cover produce output indistinguishable from a successful run: the
driver installs twice, drives the scene twice, composes a clean labelled pair, and
prints it. Until these existed the only thing standing between that and a PR comment
was a human remembering to open the PNG, and the most common cause is not "the PR
changed nothing" but a missed click, the wrong dropdown, or a stale home.

Run: python3 scripts/pr_ui_scenes/test_pr_ui_diff_guards.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from pr_ui_diff import _facts_diff, _same_bytes  # noqa: E402

_FAILS = 0


def _check(name: str, got, want) -> None:
    global _FAILS
    if got == want:
        print(f"ok    {name}")
    else:
        print(f"FAIL  {name}: got {got!r}, want {want!r}", file=sys.stderr)
        _FAILS += 1


def test_same_bytes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        a, b, c = d / "a.png", d / "b.png", d / "c.png"
        a.write_bytes(b"\x89PNG" + b"x" * 100)
        b.write_bytes(b"\x89PNG" + b"x" * 100)   # same content, different file
        c.write_bytes(b"\x89PNG" + b"y" * 100)   # same SIZE, different content
        _check("identical content is caught", _same_bytes(a, b), True)
        # Size equality alone must not be enough: a scene that photographs the same
        # panel with one label changed produces same-size, different-content files.
        _check("same size but different content is not identical", _same_bytes(a, c), False)
        _check("a missing file is not identical", _same_bytes(a, d / "nope.png"), False)


def test_facts_diff() -> None:
    _check("no change reads as empty", _facts_diff({"rows": 4}, {"rows": 4}), {})
    _check("a moved value is reported",
           _facts_diff({"rows": 4}, {"rows": 18}), {"rows": (4, 18)})
    # A key only one side could read is itself the finding, not something to skip.
    _check("a key missing on one side counts",
           _facts_diff({"quant": "Q4"}, {}), {"quant": ("Q4", None)})
    _check("a key added by the head counts",
           _facts_diff({}, {"quant": "Q4"}), {"quant": (None, "Q4")})
    # Order within a list is part of the value: a picker that reorders rows changed.
    _check("list order is part of the value",
           _facts_diff({"opts": ["a", "b"]}, {"opts": ["b", "a"]}),
           {"opts": (["a", "b"], ["b", "a"])})


def main() -> int:
    test_same_bytes()
    test_facts_diff()
    print()
    print("FAILED" if _FAILS else "all guard checks passed")
    return 1 if _FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
