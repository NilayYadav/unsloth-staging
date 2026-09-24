import contextlib
import io
import json
import os
from urllib.parse import unquote

import huggingface_hub
import pytest
from huggingface_hub import CommitInfo, HfApi
from huggingface_hub.errors import HfHubHTTPError

REPO = "nilay-ci/qwen3-0.6b-gguf"
RESULTS = {}


def _http_error(status, message):
    import importlib

    for name in ("httpx2", "httpx"):
        try:
            http = importlib.import_module(name)
        except ImportError:
            continue
        response = http.Response(status, request = http.Request("POST", "https://huggingface.co"))
        return HfHubHTTPError(message, response = response)
    import requests

    response = requests.Response()
    response.status_code = status
    return HfHubHTTPError(message, response = response)


def _commit_info(url, message, pr_url):
    try:
        return CommitInfo(
            commit_url = url, commit_message = message, commit_description = "", oid = "0" * 40, pr_url = pr_url
        )
    except TypeError:
        return CommitInfo(url, message, "", "0" * 40, pr_url = pr_url)


class FakeHub:
    def __init__(self):
        self.branches = {"main": {".gitattributes": 1}}
        self.prs = {1: {"target": "main", "files": {".gitattributes": 1}}}
        self.commits = []
        self.branch_calls = []

    def _size(self, op):
        src = op.path_or_fileobj
        if isinstance(src, (str, os.PathLike)):
            return os.path.getsize(src)
        if isinstance(src, bytes):
            return len(src)
        return len(src.read())

    def install(self, monkeypatch):
        hub = self

        def create_repo(self, repo_id, **kwargs):
            return f"https://huggingface.co/{repo_id}"

        def create_branch(self, repo_id, *, branch, exist_ok = False, **kwargs):
            hub.branch_calls.append(branch)
            if branch in hub.branches:
                if exist_ok:
                    return
                raise _http_error(409, f"409 Reference already exists: {branch}")
            hub.branches[branch] = dict(hub.branches["main"])

        def create_commit(self, repo_id, operations, *, commit_message, revision = None, create_pr = None, **kwargs):
            operations = list(operations)
            rev = unquote(revision) if revision else "main"
            if rev.startswith("refs/pr/"):
                num = int(rev.rsplit("/", 1)[-1])
                if num not in hub.prs:
                    raise _http_error(404, f"404 Invalid rev id: {rev}")
                tree = hub.prs[num]["files"]
            elif rev not in hub.branches:
                raise _http_error(404, f"404 Invalid rev id: {rev}")
            else:
                tree = hub.branches[rev]
            files = {op.path_in_repo: hub._size(op) for op in operations}
            pr_url = None
            if create_pr:
                num = max(hub.prs) + 1
                hub.prs[num] = {"target": rev, "files": {**tree, **files}}
                pr_url = f"https://huggingface.co/{repo_id}/discussions/{num}"
            else:
                tree.update(files)
            hub.commits.append({"revision": rev, "create_pr": bool(create_pr), "paths": sorted(files)})
            return _commit_info(f"https://huggingface.co/{repo_id}/commit/{'0' * 40}", commit_message, pr_url)

        monkeypatch.setattr(HfApi, "create_repo", create_repo)
        monkeypatch.setattr(HfApi, "create_branch", create_branch)
        monkeypatch.setattr(HfApi, "create_commit", create_commit)
        monkeypatch.setattr(HfApi, "add_tags", lambda self, *a, **k: None, raising = False)
        monkeypatch.setattr(HfApi, "whoami", lambda self, *a, **k: {"name": "nilay-ci"})


def _fake_convert(**kwargs):
    directory = kwargs["save_directory"]
    os.makedirs(directory, exist_ok = True)
    stem = os.path.basename(directory)
    files = []
    for suffix, size in (("Q8_0.gguf", 4096), ("Q4_K_M.gguf", 2048), ("BF16-mmproj.gguf", 1024)):
        path = os.path.join(directory, f"{stem}.{suffix}")
        with open(path, "wb") as f:
            f.write(b"GGUF" + b"\0" * (size - 4))
        files.append(path)
    with open(os.path.join(directory, "config.json"), "w") as f:
        f.write("{}")
    modelfile = os.path.join(directory, "Modelfile")
    with open(modelfile, "w") as f:
        f.write("FROM x")
    return {
        "gguf_files": files,
        "modelfile_location": modelfile,
        "want_full_precision": False,
        "is_vlm": True,
        "fix_bos_token": False,
        "save_directory": directory,
    }


@pytest.fixture
def run_push(monkeypatch):
    import unsloth.save as save_mod

    monkeypatch.setattr(save_mod, "unsloth_save_pretrained_gguf", _fake_convert)

    def run(**kwargs):
        hub = FakeHub()
        hub.install(monkeypatch)
        out = io.StringIO()
        error = None
        with contextlib.redirect_stdout(out):
            try:
                save_mod.unsloth_push_to_hub_gguf(object(), REPO, tokenizer = object(), **kwargs)
            except Exception as e:
                error = f"{type(e).__name__}: {e}"
        lines = [l for l in out.getvalue().splitlines() if "Successfully uploaded GGUF" in l]
        return {
            "kwargs": kwargs,
            "commits": len(hub.commits),
            "prs_opened": len(hub.prs) - 1,
            "create_branch_calls": hub.branch_calls,
            "error": error,
            "printed": lines[-1].strip() if lines else None,
            "commit_log": hub.commits,
            "branches": {k: sorted(v) for k, v in hub.branches.items()},
            "prs": {k: {"target": v["target"], "files": sorted(v["files"])} for k, v in hub.prs.items()},
        }

    return run


def _record(name, result):
    RESULTS[name] = result
    print(f"PR11857_RESULT {name} " + json.dumps(result, sort_keys = True))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(
                f"| {name} | {result['commits']} | {result['prs_opened']} | "
                f"{result['error'] or 'ok'} | {result['printed']} |\n"
            )


EXPECTED = [
    ".gitattributes",
    "Modelfile",
    "README.md",
    "config.json",
    "qwen3-0.6b-gguf.BF16-mmproj.gguf",
    "qwen3-0.6b-gguf.Q4_K_M.gguf",
    "qwen3-0.6b-gguf.Q8_0.gguf",
]


def test_a_push_to_main(run_push):
    r = run_push()
    _record("main", r)
    assert r["error"] is None
    assert r["branches"]["main"] == EXPECTED
    assert r["commits"] == 1


def test_b_create_pr(run_push):
    r = run_push(create_pr = True)
    _record("create_pr", r)
    assert r["error"] is None
    assert r["prs_opened"] == 1, f"create_pr=True opened {r['prs_opened']} pull requests"
    assert r["prs"][2]["files"] == EXPECTED
    assert r["branches"]["main"] == [".gitattributes"]


def test_c_new_branch(run_push):
    r = run_push(revision = "gguf-v2")
    _record("new_branch", r)
    assert r["error"] is None, r["error"]
    assert r["branches"]["gguf-v2"] == EXPECTED
    assert r["commits"] == 1


def test_d_existing_pr_ref(run_push):
    r = run_push(revision = "refs/pr/1")
    _record("refs_pr_1", r)
    assert r["error"] is None
    assert r["prs"][1]["files"] == EXPECTED
    assert r["commits"] == 1
    assert r["create_branch_calls"] == []


def test_e_explicit_main(run_push):
    r = run_push(revision = "main")
    _record("revision_main", r)
    assert r["error"] is None
    assert r["branches"]["main"] == EXPECTED


def test_f_new_branch_as_pr(run_push):
    r = run_push(revision = "gguf-v3", create_pr = True)
    _record("new_branch_create_pr", r)
    assert r["error"] is None, r["error"]
    assert r["prs_opened"] == 1
    assert r["prs"][2]["target"] == "gguf-v3"
