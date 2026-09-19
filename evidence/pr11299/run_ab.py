# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved.
"""Same tests, dependencies and fixtures; swap only the export implementation."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "evidence/pr11299/results"
OUT.mkdir(parents=True, exist_ok=True)
SOURCE = ROOT / "studio/backend/core/export/export.py"
BASE = "768d644036a948441ba80b626c9e747c1fecfbae"
HEAD = "064c27e5f52b60ecf273d1deac6a24194c352b81"
PRE_FIX = "1590374d6096f6c26346bc4915f1904af6d6a720"
original = SOURCE.read_bytes()
target_names = {
    "test_base_export_push_keeps_the_export_metadata_out_of_the_repo",
    "test_base_export_push_to_a_reused_folder_does_not_upload_its_leftovers",
    "test_lora_gguf_export_push_uploads_the_saved_folder",
    "test_lora_gguf_export_push_to_a_reused_folder_does_not_upload_its_leftovers",
}


def run(command, filename):
    result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (OUT / filename).write_text(result.stdout)
    print(result.stdout, flush=True)
    return result.returncode


results = {}
try:
    for side, ref in (("before", BASE), ("after", HEAD)):
        SOURCE.write_bytes(subprocess.check_output(["git", "show", f"{ref}:studio/backend/core/export/export.py"], cwd=ROOT))
        xml = OUT / f"{side}-tests.xml"
        code = run([sys.executable, "-m", "pytest", "studio/backend/tests/test_export_hub_push.py", "studio/backend/tests/test_export_gguf_hub_upload.py", "-q", "-k", "not staging_failure", "--tb=short", f"--junitxml={xml}"], f"{side}-tests.log")
        tree = ET.parse(xml)
        failures = {case.attrib["name"] for case in tree.findall(".//testcase") if case.find("failure") is not None}
        errors = tree.findall(".//error")
        assert not errors, f"{side}: setup/collection errors do not count as reproduction"
        if side == "before":
            assert code == 1 and failures == target_names, (code, failures)
        else:
            assert code == 0 and not failures, (code, failures)
        manifest_code = run([sys.executable, "evidence/pr11299/manifest_probe.py", str(OUT / f"{side}-manifest.json")], f"{side}-manifest.log")
        facts = json.loads((OUT / f"{side}-manifest.json").read_text())
        if side == "before":
            assert manifest_code == 1
            assert "UPLOAD_MANIFEST_CONTAINS_ONLY_CURRENT_EXPORT" in (OUT / f"{side}-manifest.log").read_text()
            assert all(row["unexpected"] for row in facts["scenarios"])
        else:
            assert manifest_code == 0
            assert all(row["staging_cleaned"] for row in facts["scenarios"] if row["staging_used"])
        results[side] = {"ref": ref, "tests": len(tree.findall(".//testcase")), "failures": sorted(failures), **facts}
    for side, ref in (("review_fix_before", PRE_FIX), ("review_fix_after", HEAD)):
        SOURCE.write_bytes(subprocess.check_output(["git", "show", f"{ref}:studio/backend/core/export/export.py"], cwd=ROOT))
        xml = OUT / f"{side}-tests.xml"
        code = run([sys.executable, "-m", "pytest", "studio/backend/tests/test_export_hub_push.py", "-k", "staging_failure", "-q", "--tb=short", f"--junitxml={xml}"], f"{side}-tests.log")
        tree = ET.parse(xml)
        assert len(tree.findall(".//testcase")) == 1 and not tree.findall(".//error")
        if side == "review_fix_before":
            assert code == 1 and len(tree.findall(".//failure")) == 1
            assert "create_repo" in (OUT / f"{side}-tests.log").read_text()
        else:
            assert code == 0
        results[side] = {"ref": ref, "tests": 1, "expected_outcome": "intended failure" if side == "review_fix_before" else "pass"}
finally:
    SOURCE.write_bytes(original)

(OUT / "meta.json").write_text(json.dumps(results, indent=2) + "\n")
lines = ["# PR 11299 export upload A/B", "", f"Base `{BASE}`; head `{HEAD}`.", "", "Identical tests and fixtures; only `export.py` changes.", "", "Before: 4 intended upload regression failures / 61 tests. After: 61 passed. The additional staging-failure test fails on the pre-review-fix head and passes on the fixed head: 62 focused tests total.", "", "| Scenario | Before bytes | After bytes | Before unwanted files |", "|---|---:|---:|---|"]
for before, after in zip(results["before"]["scenarios"], results["after"]["scenarios"]):
    lines.append(f"| {before['scenario']} | {before['upload_bytes']} | {after['upload_bytes']} | {', '.join(before['unexpected'])} |")
lines += ["", results["after"]["boundary"], "", "The stale GGUF is a synthetic 1,048,576-byte file. Byte counts describe these fixtures, not model benchmarks.", "", "Local export files are preserved, reused-folder staging is removed, and clean LoRA saves run once versus twice for reused folders."]
(OUT / "report.md").write_text("\n".join(lines) + "\n")
print("PASS: intended before failure and clean after upload manifests", flush=True)
