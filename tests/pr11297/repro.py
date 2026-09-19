# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0
"""Dependency-free execution of shipped callback bodies with controlled training state."""
import ast
import json
from pathlib import Path
import queue
import subprocess
import sys
import types

root = Path.cwd()
def extract(path, name, ns):
    tree = ast.parse((root / path).read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, path, "exec"), ns)
    return ns[name]

sys.modules["transformers"] = types.SimpleNamespace(TrainerCallback=object)
sys.modules["core.training.trainer"] = types.SimpleNamespace(_RESERVED_LOG_KEYS=(), _TRAINER_SUMMARY_KEYS=())
clock = types.SimpleNamespace(now=100.0)
ns = {"time": types.SimpleNamespace(time=lambda: clock.now), "logger": types.SimpleNamespace(info=lambda *a, **k: None),
      "_TRAINER_SUMMARY_KEYS": (), "_RESERVED_LOG_KEYS": (), "_send_status": lambda *a, **k: None}
try:
    extract("studio/backend/core/training/resume.py", "session_eta_seconds", ns)
except StopIteration:
    pass
results = {}
for kind in ("cuda", "embedding"):
    for label, start, step, preparation in (("resumed",900,910,0), ("resumed_setup",900,910,600), ("fresh",0,100,0)):
        clock.now = 100.0
        q = queue.Queue()
        progress = types.SimpleNamespace(total_steps=1000)
        if kind == "cuda":
            owner = types.SimpleNamespace(should_stop=False, training_start_time=100.0, session_start_step=0, training_progress=progress)
            owner._update_progress = lambda **kw: progress.__dict__.update(kw)
            callback = extract("studio/backend/core/training/trainer.py", "_create_progress_callback", ns)(owner)
        else:
            callback = extract("studio/backend/core/training/worker.py", "_create_embedding_progress_callback", ns)(q, total_steps=1000, training_start_time=100.0, should_stop=lambda: False)
        state = types.SimpleNamespace(global_step=start, epoch=.9, num_input_tokens_seen=0)
        clock.now += preparation
        callback.on_train_begin(None,state,None)
        state.global_step=step
        clock.now += 60
        callback.on_log(None,state,None,logs={"loss":.5,"learning_rate":1e-4})
        data = progress.__dict__ if kind == "cuda" else q.get_nowait()
        results[kind+"_"+label] = {"elapsed":data["elapsed_seconds"], "eta":data["eta_seconds"], "start":data.get("session_start_step"), "step":step}
Path("repro-facts.json").write_text(json.dumps({"sha":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),"results":results},indent=2))
print(json.dumps(results,indent=2),flush=True)
for name, result in results.items():
    assert result["eta"] == 540, f"RESUMED_ETA_ASSERTION: {name} expected540s, got {result['eta']}s"
    assert result["elapsed"] == 60, f"SESSION_CLOCK_ASSERTION: {name} includes setup"
print("PASS: CUDA and embedding fresh/resumed/setup-delay cases all ETA540s and elapsed60s")
