# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved.
"""Measure real Hub SDK commit operations; model saves and remote commits are doubles."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["HF_HUB_DISABLE_XET"] = "1"
import huggingface_hub
from huggingface_hub import HfApi
from huggingface_hub.hf_api import CommitInfo
from pytest import MonkeyPatch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "studio/backend"))
import utils.paths  # Settle the real package before the test helper replaces it.
spec = importlib.util.spec_from_file_location(
    "export_probe_helpers", ROOT / "studio/backend/tests/test_export_hub_push.py"
)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


def measure(kind, reused):
    with tempfile.TemporaryDirectory() as root, MonkeyPatch.context() as patch:
        calls, seen, captured = [], {}, {}
        name = f"manifest_{kind}_{reused}"
        backend = helpers._non_mlx_backend(patch, name, calls, seen)
        patch.setitem(sys.modules, "huggingface_hub", huggingface_hub)
        module = sys.modules[name]

        class RecordingApi(HfApi):
            def create_repo(self, repo_id, **kwargs):
                return type("Repo", (), {"repo_id": repo_id})()

            def upload_folder(self, **kwargs):
                captured["folder"] = str(kwargs["folder_path"])
                return super().upload_folder(**kwargs)

            def create_commit(self, *, operations, **kwargs):
                captured["files"] = {
                    operation.path_in_repo: Path(operation.path_or_fileobj).stat().st_size
                    for operation in operations
                }
                return CommitInfo(
                    commit_url="https://huggingface.co/evidence/model/commit/" + "0" * 40,
                    commit_message="Captured locally; no Hub upload",
                    commit_description="",
                    oid="0" * 40,
                )

        patch.setattr(module, "HfApi", RecordingApi)
        export = Path(root, "export")
        export.mkdir()
        (export / "._old.gguf").write_bytes(b"\x00\x05\x16\x07rsrc")
        if reused:
            with (export / "old-full-model.gguf").open("wb") as handle:
                handle.truncate(1_048_576)
            (export / "export_metadata.json").write_text('{"base_model":"/fixture/private/base"}')
            (export / "Modelfile").write_text("FROM old-full-model.gguf")
        if kind == "base":
            result = backend.export_base_model(
                str(export), push_to_hub=True, repo_id="evidence/model", hf_token="hf_fake"
            )
            expected = ["model.safetensors", "tokenizer.json"]
        else:
            result = helpers._push_lora_gguf(backend, str(export))
            expected = helpers._LORA_GGUF_FILES
        success, message, output = result
        assert success, message
        files = captured["files"]
        assert output == str(export.resolve())
        assert all((export / file).is_file() for file in expected)
        if reused:
            assert (export / "old-full-model.gguf").stat().st_size == 1_048_576
        staging_used = Path(captured["folder"]).resolve() != Path(output).resolve()
        return {
            "scenario": f"{kind}_{'reused' if reused else 'fresh'}",
            "files": dict(sorted(files.items())),
            "upload_bytes": sum(files.values()),
            "unexpected": sorted(set(files) - set(expected)),
            "missing": sorted(set(expected) - set(files)),
            "local_export_preserved": True,
            "staging_used": staging_used,
            "staging_cleaned": not Path(captured["folder"]).exists() if staging_used else None,
            "save_passes": len(backend.current_model.conversions) if kind == "lora" else None,
        }


facts = {
    "sdk": huggingface_hub.__version__,
    "boundary": "Real ExportBackend and HfApi.upload_folder; model saves, repo creation, model card and remote commit are doubles. No GPU or real Hub upload.",
    "scenarios": [measure(kind, reused) for kind in ("base", "lora") for reused in (False, True)],
}
Path(sys.argv[1]).write_text(json.dumps(facts, indent=2) + "\n")
print(json.dumps(facts, indent=2))
assert all(not row["unexpected"] and not row["missing"] for row in facts["scenarios"]), "UPLOAD_MANIFEST_CONTAINS_ONLY_CURRENT_EXPORT"
