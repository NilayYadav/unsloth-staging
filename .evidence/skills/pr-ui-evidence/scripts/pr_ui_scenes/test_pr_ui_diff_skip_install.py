# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/test_pr_ui_diff_skip_install.py
"""Exercise pr_ui_diff's per-side --skip-install decision with everything expensive stubbed.

The change under test is a three-line branch, but it sits in front of a 20 minute
install and a live Studio, so the only way to check it before spending that is to run
`main()` with the install, the launch, the login and the scene replaced by recorders.

Asserted here, one case per row:
  stamp matches + --skip-install   -> reuse, and side.install is still constructed
                                      (the earlier version of this branch left it None
                                       and launch_studio got None)
  stamp stale    + --skip-install   -> REBUILD that side only, not a raise
  stamp missing  + --skip-install   -> rebuild that side only
  no --skip-install                 -> rebuild both regardless of stamps
"""

from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
_SHARED = Path(__file__).resolve().parents[3] / "pr-repro-ci" / "scripts"
if not (_SHARED / "studio_test_kit").is_dir():
    raise RuntimeError("install the sibling pr-repro-ci skill")
sys.path.insert(0, str(_SHARED))

import pr_ui_diff  # noqa: E402
from studio_test_kit.lifecycle import StudioInstall  # noqa: E402

BASE = "a" * 40
HEAD = "b" * 40

fake_scene = types.ModuleType("pr_ui_scenes.fake")


async def _drive(session, out_dir, label, **kw):
    return [], {"label": label}


fake_scene.drive = _drive
sys.modules["pr_ui_scenes.fake"] = fake_scene


def run(tmp: Path, stamps: dict[str, str | None], skip: bool) -> list[str]:
    installed: list[str] = []
    launched: list[tuple[str, object]] = []

    for label, sha in (("before", stamps.get("before")), ("after", stamps.get("after"))):
        home = tmp / f"home_{label}"
        home.mkdir(parents=True, exist_ok=True)
        stamp = home / ".uidiff_sha"
        if sha:
            stamp.write_text(sha)
        elif stamp.exists():
            stamp.unlink()

    pr_ui_diff.resolve_shas = lambda repo, pr, gh_repo: (BASE, HEAD, "some-branch")
    pr_ui_diff.make_worktree = lambda repo, sha, dest: dest

    def _install(side, log_dir):
        installed.append(side.label)
        return StudioInstall(home=side.home, repo=side.worktree, branch=side.sha)

    pr_ui_diff.install_side = _install
    pr_ui_diff.launch_studio = lambda inst, port, log, extra_env=None: launched.append(
        (str(port), inst)
    )
    pr_ui_diff.studio_session = lambda url, home, pw: object()
    pr_ui_diff.pick_free_ports = lambda n: list(range(9500, 9500 + n))

    argv = ["pr_ui_diff.py", "--pr", "8222", "--scene", "fake", "--root", str(tmp)]
    if skip:
        argv.append("--skip-install")
    sys.argv = argv
    pr_ui_diff.main()

    # A side that is reused must STILL carry a StudioInstall: launch_studio takes one,
    # and None only fails later, inside a helper, with an unrelated-looking message.
    for port, inst in launched:
        assert isinstance(inst, StudioInstall), f"launch_studio got {inst!r} on :{port}"
    assert len(launched) == 2, launched
    return installed


def main() -> int:
    # A scratch dir of its own, so the test needs no particular
    # workspace layout and leaves nothing behind on any exit path.
    with tempfile.TemporaryDirectory(prefix="uidiff_skipinstall_") as td:
        return _run_cases(Path(td))


def _run_cases(tmp: Path) -> int:
    cases = [
        ("both stamped, --skip-install", {"before": BASE, "after": HEAD}, True, []),
        ("after stale, --skip-install", {"before": BASE, "after": "c" * 40}, True, ["AFTER"]),
        ("after unstamped, --skip-install", {"before": BASE, "after": None}, True, ["AFTER"]),
        ("before stale, --skip-install", {"before": "c" * 40, "after": HEAD}, True, ["BEFORE"]),
        ("both stamped, no skip", {"before": BASE, "after": HEAD}, False, ["BEFORE", "AFTER"]),
    ]
    failed = 0
    for name, stamps, skip, expect in cases:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
        got = run(tmp, stamps, skip)
        ok = got == expect
        failed += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  {name}: installed={got} expected={expect}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
