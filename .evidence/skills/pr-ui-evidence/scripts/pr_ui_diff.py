#!/usr/bin/env python3
# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_diff.py
"""Before/after Studio evidence for a PR, from two isolated installs.

    python scripts/pr_ui_diff.py --pr 8222
    python scripts/pr_ui_diff.py --pr 8222 --skip-install     # reuse the homes

Installs Studio twice under two distinct UNSLOTH_STUDIO_HOMEs -- one at the PR's
MERGE BASE, one at its head -- drives the same scene against both, and composes a
labelled BEFORE/AFTER image.

Two design points, both load-bearing:

* Two full installs, not one Studio with a `git checkout` between shots.
  `install.sh --local` builds the frontend from the checked-out tree, so a UI
  change only exists in a build made from that tree. Swapping branches under a
  running Studio serves the old bundle and yields two identical screenshots --
  which looks exactly like a successful comparison of a PR that changed nothing.

* BEFORE is the PR's merge base, not current main. `main` moves several times a
  day here; photographing against it shows the PR's change plus everything else
  that landed in between, and credits the lot to the PR.

The scene, its parameters and the difference it is expected to show all live in
`pr_ui_scenes/registry.py`. Read the plan's `expect` before believing any pair.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))
_SUITE_SKILLS = Path(__file__).resolve().parents[2]
_SHARED_SCRIPTS = _SUITE_SKILLS / "pr-repro-ci" / "scripts"
if not (_SHARED_SCRIPTS / "studio_test_kit").is_dir():
    raise RuntimeError(
        "Mimir skill suite is incomplete: install pr-repro-ci with scripts/studio_test_kit"
    )
sys.path.insert(0, str(_SHARED_SCRIPTS))

from pr_ui_scenes._common import pick_free_ports, studio_session  # noqa: E402
from pr_ui_scenes.registry import plan_for  # noqa: E402
from studio_test_kit.compose import hstack_images  # noqa: E402
from studio_test_kit.lifecycle import StudioInstall, launch_studio  # noqa: E402


def _sh(cmd: list[str], cwd: Optional[Path] = None, env: Optional[dict] = None,
        timeout: Optional[int] = None, check: bool = True) -> str:
    """Run a command, raising with BOTH streams on failure. A 40 minute install
    that dies printing neither is not debuggable after the fact."""
    proc = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})},
                          text=True, capture_output=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"--- stdout ---\n{proc.stdout[-4000:]}\n--- stderr ---\n{proc.stderr[-4000:]}"
        )
    return proc.stdout


def _gh_env() -> dict:
    """gh with GH_TOKEN removed: the ambient token lacks scopes and 403s."""
    return {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}


@dataclass
class Side:
    label: str
    sha: str
    worktree: Path
    home: Path
    port: int = 0
    install: Optional[StudioInstall] = None


def resolve_shas(repo: Path, pr: int, gh_repo: str) -> tuple[str, str, str]:
    meta = json.loads(subprocess.run(
        ["gh", "pr", "view", str(pr), "--repo", gh_repo,
         "--json", "headRefOid,headRefName,baseRefName"],
        text=True, capture_output=True, env=_gh_env(), check=True).stdout)
    head_sha, head_ref, base_ref = meta["headRefOid"], meta["headRefName"], meta["baseRefName"]
    _sh(["git", "fetch", "origin", base_ref, head_sha], cwd=repo, env=_gh_env())
    base_sha = _sh(["git", "merge-base", head_sha, f"origin/{base_ref}"],
                   cwd=repo, env=_gh_env()).strip()
    return base_sha, head_sha, head_ref


def make_worktree(repo: Path, sha: str, dest: Path) -> Path:
    """A DETACHED worktree: these are read-only stages for a screenshot, and a
    named branch would collide with whoever is driving the PR itself."""
    if dest.exists():
        _sh(["git", "worktree", "remove", "--force", str(dest)], cwd=repo, check=False)
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _sh(["git", "worktree", "add", "--detach", str(dest), sha], cwd=repo)
    return dest


def install_side(side: Side, log_dir: Path) -> StudioInstall:
    side.home.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    out = _sh(["bash", str(side.worktree / "install.sh"), "--local", "--no-torch"],
              cwd=side.worktree, env={"UNSLOTH_STUDIO_HOME": str(side.home)},
              timeout=60 * 45)
    (log_dir / f"install_{side.label.lower()}.log").write_text(out)
    return StudioInstall(home=side.home, repo=side.worktree, branch=side.sha)


def _same_bytes(a: Path, b: Path) -> bool:
    """Whether two shots are the same image.

    Byte equality rather than a perceptual diff on purpose. Both sides render the same
    scene at the same viewport on the same box, so a real UI difference changes bytes;
    anything subtler than that is not going to read in a GitHub comment anyway. A
    perceptual threshold would need tuning per scene and would be one more thing that can
    silently pass.
    """
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return hashlib.sha256(a.read_bytes()).digest() == hashlib.sha256(b.read_bytes()).digest()
    except OSError:
        return False


def _facts_diff(before: dict, after: dict) -> dict:
    """Keys whose value differs between the sides, as ``{key: (before, after)}``.

    A key present on one side only counts as changed, since a scene that could not read
    something on one build is itself the finding.
    """
    changed = {}
    for key in sorted(set(before) | set(after)):
        b_val, a_val = before.get(key), after.get(key)
        if b_val != a_val:
            changed[key] = (b_val, a_val)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument(
        "--allow-identical", action="store_true",
        help="report success even when the two sides are byte-identical and no scene fact "
             "moved. Only for a PR whose visible effect is genuinely absent on this host, "
             "and say so in the comment rather than posting the pair as evidence.",
    )
    ap.add_argument("--scene", default=None, help="override the registry's scene")
    ap.add_argument("--repo", type=Path, default=WORKSPACE / "unsloth")
    ap.add_argument("--gh-repo", default="unslothai/unsloth")
    ap.add_argument("--root", type=Path, default=None)
    ap.add_argument("--skip-install", action="store_true")
    ap.add_argument(
        "--base-ref", default=None,
        help="override the BEFORE side's commit. For a PR whose visible effect is gated "
             "behind another open PR, the merge base shows nothing and the honest pair is "
             "'that other PR' against 'that other PR plus this one'. Say which refs were "
             "used in the comment: this is no longer the PR's own merge base.",
    )
    ap.add_argument(
        "--head-ref", default=None,
        help="override the AFTER side's commit. See --base-ref.",
    )
    ap.add_argument(
        "--studio-env", action="append", default=[], metavar="KEY=VALUE",
        help="env applied to BOTH launched Studios (not to the installs). Use it to point "
             "a scene at an isolated HF cache: this box's XDG_CACHE_HOME holds a shared "
             "cache of dozens of repos, and Studio scans it IN ADDITION to HF_HOME, so a "
             "scene about cache contents otherwise photographs whatever else is on the box "
             "and its numbers move when another session downloads something.",
    )
    args = ap.parse_args()
    studio_env = dict(kv.split("=", 1) for kv in args.studio_env)

    plan = plan_for(args.pr)
    scene_name = args.scene or plan.scene
    # Resolved, always. install.sh is run with cwd inside the worktree and only passes
    # UNSLOTH_STUDIO_HOME through, so a relative --root installs a whole Studio under
    # <worktree>/<root>/home_before and the launcher then cannot find the CLI where this
    # script is looking for it. The install itself succeeds, which is what makes it slow
    # to spot.
    root = (args.root or (WORKSPACE / "outputs" / f"ui_diff_{args.pr}")).resolve()
    root.mkdir(parents=True, exist_ok=True)

    # Both refs pinned means every output of the PR lookup is discarded anyway, and
    # `gh pr view` fails outright when --pr names an issue rather than a pull request.
    if args.base_ref and args.head_ref:
        base_sha, head_sha, head_ref = "", "", args.head_ref
    else:
        base_sha, head_sha, head_ref = resolve_shas(args.repo, args.pr, args.gh_repo)
    # Overrides are resolved through the SAME repo, so a ref that does not exist locally fails
    # here rather than half way through an install, and both sides print what they actually are.
    overridden = False
    for attr, label in (("base_ref", "base"), ("head_ref", "head")):
        ref = getattr(args, attr)
        if not ref:
            continue
        overridden = True
        sha = _sh(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=args.repo).strip()
        if label == "base":
            base_sha = sha
        else:
            head_sha, head_ref = sha, ref
    print(f"PR #{args.pr} {head_ref}\n  base {base_sha}\n  head {head_sha}"
          f"\n  scene {scene_name}\n  EXPECT {plan.expect}\n", flush=True)
    if overridden:
        print("  NOTE: refs overridden, this is not the PR's own merge base. Say so in the "
              "comment.\n", flush=True)

    sides = [
        Side("BEFORE", base_sha, WORKSPACE / "temp" / f"uidiff{args.pr}_before",
             root / "home_before"),
        Side("AFTER", head_sha, WORKSPACE / "temp" / f"uidiff{args.pr}_after",
             root / "home_after"),
    ]
    # Ports are chosen per side JUST BEFORE that side launches, not both up front.
    # A bind test proves a port is free at the instant it runs and reserves nothing;
    # picking both here leaves the AFTER port unguarded across the BEFORE install and
    # scene, which is minutes, and anything that starts a Studio meanwhile takes it.
    # (Observed: an unrelated probe of this same workspace grabbed the reserved port
    # and the login identity check was what noticed.)

    scene = importlib.import_module(f"pr_ui_scenes.{scene_name}")
    # Persisted, and REUSED on a later run. Studio deletes the bootstrap password
    # once rotated, so a fresh random one per run would lock us out of our own
    # homes the moment --skip-install is used, or whenever anything wants to poke
    # the running Studios after the fact.
    pw_file = root / "studio_password.txt"
    if pw_file.exists():
        password = pw_file.read_text().strip()
    else:
        password = "UiDiff-" + secrets.token_urlsafe(12).replace("-", "x")
        pw_file.write_text(password)
    results: dict[str, list[Path]] = {}
    facts: dict[str, dict] = {}

    for side in sides:
        stamp = side.home / ".uidiff_sha"
        # PER SIDE, not all-or-nothing. A scene that fails after BEFORE installed used to
        # force a full reinstall of BEFORE too, because AFTER had no stamp yet. Reuse
        # whichever side is already built AT THE RIGHT SHA and install only the other.
        # The stamp is what makes reuse safe. A PR under active review moves, and a
        # home built from an older SHA yields a real screenshot of the WRONG code
        # under the right label -- the single most misleading output this tool can
        # make, because nothing about the image looks wrong. So reuse is allowed
        # only on an exact SHA match; a stale home is rebuilt, never photographed.
        built = stamp.read_text().strip() if stamp.exists() else None
        if args.skip_install and built == side.sha:
            # PER SIDE, not all-or-nothing. A scene that fails after BEFORE installed
            # used to force a full reinstall of BEFORE too, because AFTER had no stamp
            # yet. The binary lives under the home, so a reused side needs no worktree.
            print(f"[{side.label}] reusing home built at {side.sha[:9]}", flush=True)
            side.install = StudioInstall(home=side.home, repo=side.worktree, branch=side.sha)
        else:
            if args.skip_install:
                print(f"[{side.label}] --skip-install ignored: home was built from "
                      f"{built or 'an unrecorded SHA'}, PR needs {side.sha[:9]}",
                      flush=True)
            make_worktree(args.repo, side.sha, side.worktree)
            print(f"[{side.label}] installing from {side.worktree} ...", flush=True)
            # Cleared FIRST: an install that dies half way leaves a home that boots
            # but serves a mix, and a stamp left over from the previous build would
            # bless exactly that on the next --skip-install run.
            stamp.unlink(missing_ok=True)
            side.install = install_side(side, root / "logs")
            stamp.write_text(side.sha)
        taken = {s.port for s in sides if s.port}
        side.port = next(p for p in pick_free_ports(3) if p not in taken)
        launch_studio(side.install, side.port, root / f"{side.label.lower()}_studio.log",
                      extra_env=studio_env or None)
        # Proves the server answering is OURS: a stale Studio on this port has a
        # different password and fails here, rather than silently posing for the
        # photograph.
        session = studio_session(f"http://127.0.0.1:{side.port}", side.home, password)
        print(f"[{side.label}] up on :{side.port}, identity verified", flush=True)

        out_dir = root / side.label.lower()
        out_dir.mkdir(parents=True, exist_ok=True)
        shots, f = asyncio.run(scene.drive(session, out_dir, side.label, **plan.kwargs))
        results[side.label], facts[side.label] = shots, f
        print(f"[{side.label}] {len(shots)} shots  facts={json.dumps(f)[:300]}", flush=True)

    before, after = results["BEFORE"], results["AFTER"]
    if len(before) != len(after):
        raise RuntimeError(
            f"scene took {len(before)} shots BEFORE and {len(after)} AFTER; the flows "
            "diverged, so the pairs are not comparable"
        )
    combined = root / "combined"
    combined.mkdir(parents=True, exist_ok=True)
    pairs = []
    identical: list[str] = []
    for i, (b, a) in enumerate(zip(before, after)):
        out = combined / (f"pr{args.pr}_before_after.png" if len(before) == 1
                          else f"pr{args.pr}_pair_{i:02d}.png")
        hstack_images(b, a, out, label_left="BEFORE", label_right="AFTER")
        pairs.append(out)
        if _same_bytes(b, a):
            identical.append(f"{b.name} == {a.name}")

    (root / "meta.json").write_text(json.dumps({
        "pr": args.pr, "head_ref": head_ref, "base_sha": base_sha, "head_sha": head_sha,
        "scene": scene_name, "expect": plan.expect, "facts": facts,
        "studio_env": studio_env,
        "pairs": [str(p) for p in pairs],
    }, indent=2))

    print("\npairs:")
    for p in pairs:
        print(" ", p)
    print(f"\nEXPECTED: {plan.expect}")
    print("OBSERVED: BEFORE", json.dumps(facts["BEFORE"]))
    print("          AFTER ", json.dumps(facts["AFTER"]))
    print("\nFACTS DIFF (keys whose value moved between the two sides):")
    changed = _facts_diff(facts["BEFORE"], facts["AFTER"])
    if changed:
        for key, (b_val, a_val) in changed.items():
            print(f"  {key}: {json.dumps(b_val)} -> {json.dumps(a_val)}")
    else:
        print("  (none)")

    # The two checks below are the ones that used to be a printed reminder. Both failures
    # produce output that looks exactly like a successful run, so they have to be errors:
    # a reviewer reading the PR comment cannot tell a real "no visible change" from a
    # missed click, and neither can whoever pasted the image.
    problems: list[str] = []
    if identical:
        problems.append(
            "the two sides are BYTE-IDENTICAL for: " + "; ".join(identical)
        )
    if not changed:
        problems.append("no scene fact differs between BEFORE and AFTER")
    if problems:
        print("\nFAILED, and this is a result rather than a crash:")
        for p in problems:
            print(f"  - {p}")
        print(
            "\nBefore concluding the PR changes nothing, rule out the traps in\n"
            "pr_ui_evidence_workflow.md: a missed click leaving the panel on its old\n"
            "selection, the wrong dropdown opening, a home built from a stale SHA, or a\n"
            "port answered by someone else's Studio. If the PR really has no visible\n"
            "effect, it did not need a scene. Re-run with --allow-identical to keep the\n"
            "output anyway."
        )
        if not args.allow_identical:
            return 1
        print("\n--allow-identical given, so reporting success regardless.")

    print("\nNow OPEN the pair and confirm it shows the expected difference. The checks "
          "above catch two sides that are identical; they cannot tell you that a real "
          "difference is the RIGHT difference. Only the composite and `expect` do that.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
