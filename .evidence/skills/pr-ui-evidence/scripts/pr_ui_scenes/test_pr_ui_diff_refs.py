# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/test_pr_ui_diff_refs.py
"""The two ways a run can point at the wrong thing without ever looking wrong.

Both are covered by driving ``main()`` with the install, the launch, the login and the
scene replaced by recorders, as ``test_pr_ui_diff_skip_install.py`` does.

  --base-ref / --head-ref  A PR whose visible effect is gated behind ANOTHER open PR
                           shows nothing against its own merge base. The overrides make
                           the honest pair reachable, and they have to reach the sides:
                           an override that is parsed and dropped produces a clean pair
                           of the wrong two commits.
  --root resolution        install.sh runs with cwd inside the worktree and is handed
                           only UNSLOTH_STUDIO_HOME, so a RELATIVE --root installs a
                           whole Studio under <worktree>/<root>/home_before. The install
                           succeeds; the launcher then cannot find the CLI. Observed.

Run: python3 scripts/pr_ui_scenes/test_pr_ui_diff_refs.py
"""

from __future__ import annotations

import contextlib
import io
import os
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
OVERRIDE = {"someother-pr": "c" * 40, "someother-plus-this": "d" * 40}

fake_scene = types.ModuleType("pr_ui_scenes.fake_refs")


async def _drive(session, out_dir, label, **kw):
    return [], {"label": label}


fake_scene.drive = _drive
sys.modules["pr_ui_scenes.fake_refs"] = fake_scene

_FAILS = 0


def _check(name: str, got, want) -> None:
    global _FAILS
    if got == want:
        print(f"ok    {name}")
    else:
        print(f"FAIL  {name}: got {got!r}, want {want!r}", file=sys.stderr)
        _FAILS += 1


def run(root_arg: str, extra: list[str]) -> tuple[list[str], list[Path]]:
    """``main()`` with everything expensive stubbed. Returns the SHAs each side was
    built from, in order, and the homes those installs were pointed at."""
    shas: list[str] = []
    homes: list[Path] = []

    pr_ui_diff.resolve_shas = lambda repo, pr, gh_repo: (BASE, HEAD, "some-branch")
    pr_ui_diff.make_worktree = lambda repo, sha, dest: dest
    # Stands in for `git rev-parse --verify <ref>^{commit}`, the one real command the
    # override path runs. An unknown ref must not silently become a valid side.
    pr_ui_diff._sh = lambda cmd, cwd=None, env=None, check=True, timeout=None: (
        OVERRIDE[cmd[-1].split("^")[0]] + "\n"
    )

    def _install(side, log_dir):
        shas.append(side.sha)
        homes.append(side.home)
        side.home.mkdir(parents=True, exist_ok=True)  # the real install_side does this
        return StudioInstall(home=side.home, repo=side.worktree, branch=side.sha)

    pr_ui_diff.install_side = _install
    pr_ui_diff.launch_studio = lambda inst, port, log, extra_env=None: None
    pr_ui_diff.studio_session = lambda url, home, pw: object()
    pr_ui_diff.pick_free_ports = lambda n: list(range(9600, 9600 + n))

    sys.argv = ["pr_ui_diff.py", "--pr", "8244", "--scene", "fake_refs",
                "--root", root_arg, *extra]
    # main() prints the whole registry `expect` twice, which buries the check lines.
    with contextlib.redirect_stdout(io.StringIO()):
        pr_ui_diff.main()
    return shas, homes


def test_refs_reach_the_sides(tmp_path: Path) -> None:
    shas, _ = run(str(tmp_path), ["--base-ref", "someother-pr", "--head-ref", "someother-plus-this"])
    _check("both overrides reach the installs", shas,
           [OVERRIDE["someother-pr"], OVERRIDE["someother-plus-this"]])


def test_one_sided_override_keeps_the_other_side(tmp_path: Path) -> None:
    shas, _ = run(str(tmp_path), ["--base-ref", "someother-pr"])
    _check("an unset --head-ref leaves the PR's head alone", shas,
           [OVERRIDE["someother-pr"], HEAD])


def test_no_override_uses_the_merge_base(tmp_path: Path) -> None:
    shas, _ = run(str(tmp_path), [])
    _check("no override means the PR's own base and head", shas, [BASE, HEAD])


def test_a_relative_root_is_resolved(tmp_path: Path) -> None:
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        _, homes = run("outputs/rel_root", [])
    finally:
        os.chdir(cwd)
    _check("both homes are absolute", [h.is_absolute() for h in homes], [True, True])
    _check("and they are under the cwd the caller meant",
           [str(h).startswith(str(tmp_path.resolve())) for h in homes], [True, True])


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="uidiff_refs_") as td:
        for fn in (test_refs_reach_the_sides, test_one_sided_override_keeps_the_other_side,
                   test_no_override_uses_the_merge_base, test_a_relative_root_is_resolved):
            import shutil

            shutil.rmtree(td, ignore_errors=True)
            Path(td).mkdir(parents=True, exist_ok=True)
            fn(Path(td))
    print()
    print("FAILED" if _FAILS else "all ref checks passed")
    return 1 if _FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
